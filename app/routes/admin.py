"""Adminpaneel — afgeschermd pad, wachtwoord (Argon2id) + verplichte TOTP-2FA,
aparte sessievlag, alle acties in de audit log (strategienota §8.1)."""
from functools import wraps
from datetime import datetime, timedelta

import pyotp
from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   session, url_for)
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_ph = PasswordHasher()  # Argon2id (default)

from ..extensions import db, limiter
from ..models import Admin, AuditLog, Event, Family, Interaction, Review

bp = Blueprint("admin", __name__, url_prefix="/beheer")


def _huidige_admin():
    """Admin uit de sessie, of None als de sessie verweesd/ongeldig is.
    Maakt een kapotte sessie meteen leeg zodat de app nooit vastloopt."""
    aid = session.get("admin_id")
    if not aid:
        return None
    admin = db.session.get(Admin, aid)
    if admin is None:  # sessie verwijst naar niet-bestaande admin → opruimen
        session.pop("admin_id", None)
        session.pop("admin_2fa_ok", None)
    return admin


def admin_required(f):
    """Enkel volle beheerders (role='admin')."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        a = _huidige_admin()
        if not a or not session.get("admin_2fa_ok"):
            return redirect(url_for("admin.login"))
        if getattr(a, "role", "admin") != "admin":
            abort(403)   # reviewer probeert een admin-pagina
        return f(*args, **kwargs)
    return wrapper


def reviewer_required(f):
    """Beheerders én reviewers: content nazien en valideren (Nazicht)."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        a = _huidige_admin()
        if not a or not session.get("admin_2fa_ok"):
            return redirect(url_for("admin.login"))
        return f(*args, **kwargs)
    return wrapper


def audit(action):
    db.session.add(AuditLog(admin_id=session.get("admin_id"), action=action))
    db.session.commit()


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10/hour", methods=["POST"])
def login():
    if request.method == "GET":
        # Schone start: ruim een half-ingelogde of verweesde sessie op,
        # zodat een oude cookie je nooit blokkeert (geen incognito nodig).
        if not session.get("admin_2fa_ok"):
            session.pop("admin_id", None)
            session.pop("admin_2fa_ok", None)
    if request.method == "POST":
        admin = Admin.query.filter_by(email=request.form.get("email", "").lower().strip()).first()
        ok = False
        if admin:
            try:
                ok = _ph.verify(admin.pw_hash, request.form.get("password", ""))
            except VerifyMismatchError:
                ok = False
        if ok:
            session.clear()  # verse sessie, geen resten van een oude cookie
            session["admin_id"] = admin.id
            session["admin_2fa_ok"] = False
            # Nog geen bevestigde 2FA → verplichte enrollment met QR-code.
            if not admin.totp_confirmed:
                return redirect(url_for("admin.tweefa_instellen"))
            return redirect(url_for("admin.otp"))
        flash("Onjuiste gegevens.", "error")
    return render_template("admin/login.html", title="Beheer", family=None, active=None)


@bp.route("/2fa-instellen", methods=["GET", "POST"])
@limiter.limit("15/hour")
def tweefa_instellen():
    """Verplichte 2FA-enrollment: toon QR, bevestig eerste code, dan pas toegang.
    Bereikbaar na wachtwoord-login, zolang totp_confirmed nog False is."""
    if not session.get("admin_id"):
        return redirect(url_for("admin.login"))
    admin = _huidige_admin()
    if admin is None:
        return redirect(url_for("admin.login"))
    if admin.totp_confirmed:  # al ingesteld → niets te doen hier
        return redirect(url_for("admin.otp"))

    if request.method == "POST":
        totp = pyotp.TOTP(admin.totp_secret)
        if totp.verify(request.form.get("code", ""), valid_window=1):
            admin.totp_confirmed = True
            db.session.commit()
            session["admin_2fa_ok"] = True
            audit("2fa ingesteld + login")
            flash("Tweestapsverificatie is ingesteld. 🎉", "ok")
            a = _huidige_admin()
            if a and getattr(a, "role", "admin") == "reviewer":
                return redirect(url_for("admin.nazicht"))
            return redirect(url_for("admin.dashboard"))
        flash("Die code klopt niet. Scan de QR opnieuw en probeer een verse code.", "error")

    # QR-code server-side genereren als PNG data-URI (puur zwart-wit = best
    # leesbaar voor scanners), niets externs, geen tracking.
    import io
    import base64
    import segno
    uri = pyotp.TOTP(admin.totp_secret).provisioning_uri(
        name=admin.email, issuer_name="Ravot Beheer")
    qr = segno.make(uri, error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="png", scale=8, border=4, dark="#000000", light="#ffffff")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    qr_svg = f'<img src="data:image/png;base64,{b64}" alt="QR-code voor 2FA" width="240" height="240">'
    return render_template("admin/tweefa_instellen.html", qr_svg=qr_svg,
                           secret=admin.totp_secret, title="Stel 2FA in",
                           family=None, active=None)


@bp.route("/otp", methods=["GET", "POST"])
@limiter.limit("10/hour", methods=["POST"])
def otp():
    if not session.get("admin_id"):
        return redirect(url_for("admin.login"))
    admin = _huidige_admin()
    if admin is None:
        return redirect(url_for("admin.login"))
    if not admin.totp_confirmed:  # nog niet ingeschreven → naar QR-flow
        return redirect(url_for("admin.tweefa_instellen"))
    if request.method == "POST":
        totp = pyotp.TOTP(admin.totp_secret)
        if totp.verify(request.form.get("code", ""), valid_window=1):
            session["admin_2fa_ok"] = True
            audit("login")
            a = _huidige_admin()
            if a and getattr(a, "role", "admin") == "reviewer":
                return redirect(url_for("admin.nazicht"))
            return redirect(url_for("admin.dashboard"))
        flash("Onjuiste code.", "error")
    return render_template("admin/otp.html", title="Tweestapsverificatie",
                           family=None, active=None)


@bp.route("/")
@admin_required
def dashboard():
    from ..routes.public import window
    from ..models import SavedEvent
    now = datetime.utcnow()
    week_start, week_end = window("deze-week")

    stats = {
        "gezinnen": Family.query.count(),
        "gezinnen_actief": Family.query.filter_by(active=True).count(),
        "events_totaal": Event.query.count(),
        "events_komend": Event.query.filter(Event.start >= now).count(),
        "events_deze_week": Event.query.filter(
            Event.start >= week_start, Event.start <= week_end).count(),
        "reviews": Review.query.count(),
        "bewaard": SavedEvent.query.count(),
        "nieuwsbrief": Family.query.filter_by(newsletter_opt_in=True).count(),
        "nieuw_deze_week": Family.query.filter(Family.created_at >= week_start).count(),
    }

    # Kwaliteitsverdeling: waar staat de data? (drijft de kwaliteitslaag)
    from ..models import get_int
    k_min = get_int("kwaliteit_min_lijst", 30)
    k_hoog = get_int("kwaliteit_hoog", 60)
    kwaliteit = {
        "hoog": Event.query.filter(Event.quality >= k_hoog).count(),
        "midden": Event.query.filter(Event.quality >= k_min, Event.quality < k_hoog).count(),
        "laag": Event.query.filter(Event.quality < k_min).count(),
        "onbekend": Event.query.filter(Event.quality.is_(None)).count(),
        "min": k_min, "hoog_v": k_hoog,
    }

    # Populairste gemeenten (naar aantal komende events)
    top_gemeenten = db.session.query(
        Event.gemeente, db.func.count(Event.id).label("n")) \
        .filter(Event.start >= now, Event.gemeente.isnot(None)) \
        .group_by(Event.gemeente).order_by(db.text("n DESC")).limit(8).all()

    # Recentste aanmeldingen
    nieuwste_gezinnen = Family.query.order_by(Family.created_at.desc()).limit(5).all()
    recent_reviews = Review.query.order_by(Review.created_at.desc()).limit(10).all()

    return render_template("admin/dashboard.html", kwaliteit=kwaliteit, stats=stats,
                           top_gemeenten=top_gemeenten, nieuwste_gezinnen=nieuwste_gezinnen,
                           reviews=recent_reviews, title="Dashboard",
                           family=None, active="dashboard")


@bp.route("/review/<int:review_id>/verwijder", methods=["POST"])
@admin_required
def delete_review(review_id):
    rv = db.session.get(Review, review_id) or abort(404)
    audit(f"review {review_id} verwijderd (event {rv.event_id})")
    db.session.delete(rv)
    db.session.commit()
    flash("Review verwijderd.", "ok")
    return redirect(url_for("admin.dashboard"))


@bp.route("/instellingen", methods=["GET", "POST"])
@admin_required
def instellingen():
    """Niet-geheime configuratie beheren. Secrets staan bewust NIET hier."""
    from ..models import Setting, SETTING_DEFS, get_setting
    if request.method == "POST":
        gewijzigd = []
        for key, (default, label, typ) in SETTING_DEFS.items():
            if typ == "bool":
                nieuw = "1" if request.form.get(key) == "on" else "0"
            else:
                nieuw = (request.form.get(key) or "").strip()
                if typ == "int" and not nieuw.isdigit():
                    continue  # ongeldige int negeren
            row = db.session.get(Setting, key)
            if row is None:
                row = Setting(key=key)
                db.session.add(row)
            if row.value != nieuw:
                gewijzigd.append(key)
            row.value = nieuw
        db.session.commit()
        if gewijzigd:
            audit("instellingen gewijzigd: " + ", ".join(gewijzigd))
        flash("Instellingen bewaard.", "ok")
        return redirect(url_for("admin.instellingen"))
    waarden = {key: get_setting(key) for key in SETTING_DEFS}
    return render_template("admin/instellingen.html", defs=SETTING_DEFS,
                           waarden=waarden, title="Instellingen",
                           family=None, active=None)


@bp.route("/verbindingen")
@admin_required
def verbindingen():
    """Statusoverzicht van externe diensten. Toont GEEN secrets, enkel of ze
    geconfigureerd zijn en werken."""
    from flask import current_app
    cfg = current_app.config
    # UiT: is er een key en staat de URL op test of productie?
    uit_url = cfg.get("UIT_SEARCH_URL", "")
    status = {
        "uit": {
            "geconfigureerd": bool(cfg.get("UIT_API_KEY")),
            "omgeving": "productie" if "search.uitdatabank" in uit_url and "test" not in uit_url else "test",
            "url": uit_url,
            "laatste_event": None,
            "aantal_events": Event.query.count(),
        },
        "smtp": {
            "geconfigureerd": bool(cfg.get("SMTP_HOST")),
            "host": cfg.get("SMTP_HOST") or "(console-modus — mails naar log)",
            "afzender": cfg.get("MAIL_FROM", ""),
        },
    }
    laatste = Event.query.order_by(Event.updated_at.desc()).first()
    if laatste:
        status["uit"]["laatste_event"] = laatste.updated_at

    # Sync-status per bron + of er iets loopt (voor de knoppen in de admin)
    from ..services.sources import get_statuses, is_sync_running
    syncstatus = get_statuses()
    sync_bezig = is_sync_running()

    # Extra bronnen: aan/uit + eventueel een key + aantal events per bron.
    from ..models import get_bool as _gb
    status["uit"]["aan"] = _gb("bron_uit_aan")
    # Dynamisch uit de bronnen-registry: elke bron die bestaat, staat hier —
    # er kan er nooit meer één "vergeten" worden op deze pagina.
    from ..services.sources import REGISTRY
    _extra = {"tm": {"key_nodig": True, "geconfigureerd": bool(cfg.get("TICKETMASTER_API_KEY")), "test": True}}
    status["bronnen"] = []
    for code, (setting_key, label, _mod) in REGISTRY.items():
        if code == "uit":
            continue  # heeft zijn eigen blok bovenaan
        ex = _extra.get(code, {})
        status["bronnen"].append({
            "code": code, "naam": label, "aan": _gb(setting_key),
            "key_nodig": ex.get("key_nodig", False),
            "geconfigureerd": ex.get("geconfigureerd", True),
            "aantal": Event.query.filter_by(source=code).count(),
            "test": ex.get("test", False),
        })
    # Ollama (AI-verrijking): bereikbaar? welk model?
    status["ollama"] = {"url": cfg.get("OLLAMA_URL", ""), "model": cfg.get("OLLAMA_MODEL", "")}
    return render_template("admin/verbindingen.html", status=status,
                           syncstatus=syncstatus, sync_bezig=sync_bezig,
                           title="Verbindingen", family=None, active=None)


@bp.route("/test-ollama", methods=["POST"])
@admin_required
@limiter.limit("10/hour")
def test_ollama():
    """Test of de Ollama-container bereikbaar is en het model geladen kan worden."""
    import requests as _rq
    from flask import current_app
    url = (current_app.config.get("OLLAMA_URL") or "").rstrip("/")
    model = current_app.config.get("OLLAMA_MODEL") or ""
    if not url:
        flash("OLLAMA_URL is niet geconfigureerd in .env.", "error")
        return redirect(url_for("admin.verbindingen"))
    try:
        r = _rq.get(f"{url}/api/tags", timeout=8)
        r.raise_for_status()
        modellen = [m.get("name", "") for m in (r.json().get("models") or [])]
        if not modellen:
            flash("Ollama draait, maar er is nog geen model gepulld. "
                  "Draai: docker compose exec ollama ollama pull " + (model or "qwen2.5:7b"), "error")
        elif model and not any(model in m for m in modellen):
            flash(f"Ollama draait met {', '.join(modellen)}, maar het ingestelde model "
                  f"'{model}' ontbreekt. Pull het of pas OLLAMA_MODEL aan.", "error")
        else:
            # kleine echte generatie als ultieme proef
            g = _rq.post(f"{url}/api/generate", json={
                "model": model or modellen[0], "prompt": "Zeg exact: OK", "stream": False,
            }, timeout=60)
            g.raise_for_status()
            antwoord = (g.json().get("response") or "").strip()[:40]
            audit("ollama-test uitgevoerd")
            flash(f"Ollama werkt ✅ — model antwoordde: \"{antwoord}\"", "ok")
    except Exception as exc:
        flash(f"Ollama niet bereikbaar: {str(exc)[:150]}", "error")
    return redirect(url_for("admin.verbindingen"))


@bp.route("/test-uit", methods=["POST"])
@admin_required
@limiter.limit("10/hour")
def test_uit():
    """Test de UiT-verbinding met één kale call. Toont GEEN key."""
    import requests
    from flask import current_app
    cfg = current_app.config
    try:
        r = requests.get(f"{cfg['UIT_SEARCH_URL']}/events",
                         params={"clientId": cfg["UIT_API_KEY"], "limit": 1},
                         timeout=8)
        if r.status_code == 200:
            n = r.json().get("totalItems", "?")
            flash(f"UiT-verbinding OK ✅ — {n} events beschikbaar.", "ok")
        else:
            flash(f"UiT antwoordde met status {r.status_code}. Controleer de key in .env.", "error")
    except Exception as exc:
        flash(f"UiT niet bereikbaar: {str(exc)[:120]}", "error")
    audit("UiT-verbinding getest")
    return redirect(url_for("admin.verbindingen"))


@bp.route("/sync/<naam>", methods=["POST"])
@admin_required
@limiter.limit("30/hour")
def sync_bron(naam):
    """Start een sync in de achtergrond (bron of 'all'). De webrequest keert
    meteen terug; de status volg je op deze pagina (herladen)."""
    import threading
    from flask import current_app
    from ..services.sources import REGISTRY, is_sync_running, sync_one, sync_all
    if naam != "all" and naam not in REGISTRY:
        flash("Onbekende bron.", "error")
        return redirect(url_for("admin.verbindingen"))
    if is_sync_running():
        flash("Er loopt al een sync. Even geduld en herlaad de pagina.", "error")
        return redirect(url_for("admin.verbindingen"))
    app_obj = current_app._get_current_object()

    def _job():
        with app_obj.app_context():
            try:
                sync_all() if naam == "all" else sync_one(naam)
            except Exception as exc:
                app_obj.logger.warning("admin-sync %s faalde: %s", naam, str(exc)[:160])

    if current_app.testing:
        _job()                       # deterministisch in tests, geen thread
    else:
        threading.Thread(target=_job, daemon=True).start()
    audit(f"Sync gestart via admin: {naam}")
    flash(f"Sync gestart voor '{naam}'. Herlaad de pagina om de voortgang te zien.", "ok")
    return redirect(url_for("admin.verbindingen"))


@bp.route("/purge/<naam>", methods=["POST"])
@admin_required
@limiter.limit("10/hour")
def purge_bron(naam):
    """Verwijder alle data van één bron. Vereist een expliciete bevestiging."""
    from ..services.sources import REGISTRY, purge_source, is_sync_running
    if naam not in REGISTRY:
        flash("Onbekende bron.", "error")
        return redirect(url_for("admin.verbindingen"))
    if request.form.get("bevestig") != "ja":
        flash("Vink eerst 'Ja, verwijder' aan om te bevestigen.", "error")
        return redirect(url_for("admin.verbindingen"))
    if is_sync_running():
        flash("Er loopt een sync — wacht tot die klaar is voor je verwijdert.", "error")
        return redirect(url_for("admin.verbindingen"))
    n = purge_source(naam)
    audit(f"Bron verwijderd via admin: {naam} ({n} events)")
    flash(f"Bron '{naam}' verwijderd: {n} activiteiten weg.", "ok")
    return redirect(url_for("admin.verbindingen"))


@bp.route("/test-tm", methods=["POST"])
@admin_required
@limiter.limit("10/hour")
def test_tm():
    """Test de Ticketmaster-verbinding met één kale call (Family, BE)."""
    import requests
    from flask import current_app
    cfg = current_app.config
    if not cfg.get("TICKETMASTER_API_KEY"):
        flash("Geen TICKETMASTER_API_KEY in .env. Vraag een gratis key aan "
              "op developer.ticketmaster.com.", "error")
        return redirect(url_for("admin.verbindingen"))
    try:
        r = requests.get(f"{cfg['TICKETMASTER_URL'].rstrip('/')}/events.json",
                         params={"apikey": cfg["TICKETMASTER_API_KEY"],
                                 "countryCode": "BE", "classificationName": "family",
                                 "size": 1}, timeout=8)
        if r.status_code == 200:
            n = (r.json().get("page") or {}).get("totalElements", "?")
            flash(f"Ticketmaster OK ✅ — {n} Family-events in BE beschikbaar.", "ok")
        else:
            flash(f"Ticketmaster antwoordde met status {r.status_code}. "
                  "Controleer de key in .env.", "error")
    except Exception as exc:
        flash(f"Ticketmaster niet bereikbaar: {str(exc)[:120]}", "error")
    audit("Ticketmaster-verbinding getest")
    return redirect(url_for("admin.verbindingen"))


@bp.route("/test-smtp", methods=["POST"])
@admin_required
@limiter.limit("5/hour")
def test_smtp():
    """Stuur een testmail naar het adres van de ingelogde admin."""
    from ..services.magic import send_mail
    admin = db.session.get(Admin, session["admin_id"])
    try:
        send_mail(admin.email, "Ravot — testmail",
                  "<p>Dit is een testmail vanuit het Ravot-beheer. "
                  "Als je dit ziet, werkt SMTP. 🎉</p>",
                  text="Testmail vanuit Ravot-beheer. SMTP werkt.")
        flash(f"Testmail verstuurd naar {admin.email}. Kijk in je mailbox (of de console-log bij dev).", "ok")
    except Exception as exc:
        flash(f"Mail versturen mislukte: {str(exc)[:120]}", "error")
    audit("SMTP-testmail verstuurd")
    return redirect(url_for("admin.verbindingen"))


@bp.route("/families")
@admin_required
def families():
    """Overzicht van gezinnen met zoeken."""
    from ..models import Family
    zoek = (request.args.get("q") or "").strip().lower()
    q = Family.query
    if zoek:
        q = q.filter(db.func.lower(Family.email).like(f"%{zoek}%"))
    gezinnen = q.order_by(Family.created_at.desc()).limit(200).all()
    return render_template("admin/families.html", gezinnen=gezinnen, zoek=zoek,
                           title="Gezinnen", family=None, active=None)


@bp.route("/families/<int:fid>", methods=["GET", "POST"])
@admin_required
def family_detail(fid):
    from ..models import Family, Review, SavedEvent, Interaction
    fam = db.session.get(Family, fid) or abort(404)
    if request.method == "POST":
        actie = request.form.get("actie")
        if actie == "email":
            nieuw = (request.form.get("email") or "").strip().lower()
            if nieuw and "@" in nieuw:
                fam.email = nieuw
                db.session.commit()
                audit(f"e-mail gezin {fid} gewijzigd")
                flash("E-mailadres aangepast.", "ok")
        elif actie == "deactiveer":
            fam.active = not fam.active
            db.session.commit()
            audit(f"gezin {fid} {'geactiveerd' if fam.active else 'gedeactiveerd'}")
            flash("Gezin " + ("geactiveerd." if fam.active else "gedeactiveerd."), "ok")
        elif actie == "verwijder":
            # GDPR: alle gekoppelde data mee verwijderen
            Review.query.filter_by(family_id=fid).delete()
            SavedEvent.query.filter_by(family_id=fid).delete()
            Interaction.query.filter_by(family_id=fid).delete()
            db.session.delete(fam)
            db.session.commit()
            audit(f"gezin {fid} volledig verwijderd (GDPR)")
            flash("Gezin en alle gekoppelde data verwijderd.", "ok")
            return redirect(url_for("admin.families"))
        return redirect(url_for("admin.family_detail", fid=fid))
    aantal_reviews = Review.query.filter_by(family_id=fid).count()
    aantal_bewaard = SavedEvent.query.filter_by(family_id=fid).count()
    return render_template("admin/family_detail.html", fam=fam,
                           aantal_reviews=aantal_reviews, aantal_bewaard=aantal_bewaard,
                           title=f"Gezin {fam.email}", family=None, active=None)


@bp.route("/paginas", methods=["GET"])
@admin_required
def paginas():
    from ..models import ContentPage, CONTENT_PAGES
    pages = []
    for slug, titel in CONTENT_PAGES.items():
        cp = db.session.get(ContentPage, slug)
        pages.append({"slug": slug, "titel": titel, "bewerkt": cp.updated_at if cp else None})
    return render_template("admin/paginas.html", pages=pages,
                           title="Inhoudspagina's", family=None, active=None)


@bp.route("/paginas/<slug>", methods=["GET", "POST"])
@admin_required
def pagina_bewerk(slug):
    from ..models import ContentPage, CONTENT_PAGES
    if slug not in CONTENT_PAGES:
        abort(404)
    cp = db.session.get(ContentPage, slug)
    if request.method == "POST":
        if cp is None:
            cp = ContentPage(slug=slug, titel=CONTENT_PAGES[slug])
            db.session.add(cp)
        cp.titel = (request.form.get("titel") or CONTENT_PAGES[slug]).strip()[:120]
        cp.inhoud_md = request.form.get("inhoud_md") or ""
        db.session.commit()
        audit(f"pagina '{slug}' bewerkt")
        flash("Pagina bewaard.", "ok")
        return redirect(url_for("admin.pagina_bewerk", slug=slug))
    inhoud = cp.inhoud_md if cp else ""
    titel = cp.titel if cp else CONTENT_PAGES[slug]
    return render_template("admin/pagina_bewerk.html", slug=slug, titel=titel,
                           inhoud=inhoud, title=f"Bewerk: {CONTENT_PAGES[slug]}",
                           family=None, active=None)


@bp.route("/mails", methods=["GET"])
@admin_required
def mails():
    from ..models import MailTemplate, MAIL_TEMPLATES
    templates = []
    for slug, (naam, placeholders) in MAIL_TEMPLATES.items():
        mt = db.session.get(MailTemplate, slug)
        templates.append({"slug": slug, "naam": naam, "placeholders": placeholders,
                          "bewerkt": mt.updated_at if mt else None})
    return render_template("admin/mails.html", templates=templates,
                           title="Mailteksten", family=None, active=None)


@bp.route("/mails/<slug>", methods=["GET", "POST"])
@admin_required
def mail_bewerk(slug):
    from ..models import MailTemplate, MAIL_TEMPLATES
    if slug not in MAIL_TEMPLATES:
        abort(404)
    naam, placeholders = MAIL_TEMPLATES[slug]
    mt = db.session.get(MailTemplate, slug)
    if request.method == "POST":
        if mt is None:
            mt = MailTemplate(slug=slug, naam=naam)
            db.session.add(mt)
        mt.onderwerp = (request.form.get("onderwerp") or "").strip()[:200]
        mt.inhoud_md = request.form.get("inhoud_md") or ""
        db.session.commit()
        audit(f"mailtekst '{slug}' bewerkt")
        flash("Mailtekst bewaard.", "ok")
        return redirect(url_for("admin.mail_bewerk", slug=slug))
    return render_template("admin/mail_bewerk.html", slug=slug, naam=naam,
                           placeholders=placeholders,
                           onderwerp=mt.onderwerp if mt else "",
                           inhoud=mt.inhoud_md if mt else "",
                           title=f"Bewerk mail: {naam}", family=None, active=None)


@bp.route("/logout")
def logout():
    if session.get("admin_id"):
        audit("logout")
    session.pop("admin_id", None)
    session.pop("admin_2fa_ok", None)
    return redirect(url_for("public.vandaag"))


@bp.route("/nazicht")
@reviewer_required
def nazicht():
    """Moderatie: door gebruikers toegevoegde plekken (wachtrij) + meldingen."""
    from ..models import (Report, REPORT_REASONS, EnrichProposal, Photo,
                          OperatorClaim, EditProposal)
    wachtrij = Event.query.filter_by(pending=True).order_by(Event.id.desc()).limit(200).all()
    meldingen = Report.query.filter_by(handled=False).order_by(
        Report.created_at.desc()).limit(200).all()
    voorstellen = EnrichProposal.query.filter_by(status="pending").order_by(
        EnrichProposal.id.desc()).limit(100).all()
    fotos = Photo.query.filter_by(status="pending").order_by(Photo.id.desc()).limit(100).all()
    claims = OperatorClaim.query.filter_by(status="pending").order_by(
        OperatorClaim.id.desc()).limit(100).all()
    edits = EditProposal.query.filter_by(status="pending").order_by(
        EditProposal.id.desc()).limit(100).all()
    return render_template("admin/nazicht.html", wachtrij=wachtrij, meldingen=meldingen,
                           voorstellen=voorstellen, fotos=fotos, claims=claims,
                           edits=edits, redenen=REPORT_REASONS,
                           title="Nazicht", family=None, active="nazicht")


@bp.route("/nazicht/plek/<int:event_id>/<actie>", methods=["POST"])
@reviewer_required
def nazicht_plek(event_id, actie):
    ev = db.session.get(Event, event_id)
    if not ev or not ev.pending:
        abort(404)
    if actie == "goedkeuren":
        ev.pending = False
        audit(f"plek goedgekeurd: {ev.title} (#{ev.id})")
        flash(f"'{ev.title}' is nu zichtbaar.", "ok")
    elif actie == "afwijzen":
        audit(f"plek afgewezen: {ev.title} (#{ev.id})")
        db.session.delete(ev)
        flash("Plek afgewezen en verwijderd.", "ok")
    db.session.commit()
    return redirect(url_for("admin.nazicht"))


@bp.route("/nazicht/melding/<int:report_id>/<actie>", methods=["POST"])
@reviewer_required
def nazicht_melding(report_id, actie):
    from ..models import Report
    r = db.session.get(Report, report_id)
    if not r:
        abort(404)
    if actie == "verberg" and r.event:      # plek verbergen (bv. gesloten)
        r.event.hidden = True
        audit(f"plek verborgen na melding: #{r.event_id}")
    if actie == "verwijder" and r.event:    # plek definitief weg
        audit(f"plek verwijderd na melding: #{r.event_id}")
        db.session.delete(r.event)
    r.handled = True
    audit(f"melding afgehandeld: #{r.id} ({actie})")
    db.session.commit()
    flash("Melding afgehandeld.", "ok")
    return redirect(url_for("admin.nazicht"))


@bp.route("/verrijk", methods=["GET", "POST"])
@admin_required
def verrijk():
    """Testknop voor AI-verrijking: genereer een voorstel voor één plek en
    toon het (nog niet opgeslagen — dat is de latere wachtrij-stap)."""
    from ..models import get_setting
    voorstel = plek = fout = None
    if request.method == "POST":
        try:
            eid = int(request.form.get("event_id") or 0)
        except ValueError:
            eid = 0
        plek = db.session.get(Event, eid)
        if not plek:
            fout = "Geen plek gevonden met dat id."
        else:
            from ..enrich import verrijk_plek
            try:
                voorstel = verrijk_plek(plek)
                audit(f"AI-verrijking getest voor #{plek.id}")
            except Exception as exc:
                fout = f"Verrijking mislukt: {exc}"
    # een handvol recente plekken tonen om snel te kiezen
    recent = Event.query.filter_by(is_permanent=True).order_by(
        Event.id.desc()).limit(15).all()
    return render_template("admin/verrijk.html", voorstel=voorstel, plek=plek,
                           fout=fout, recent=recent, backend=get_setting("verrijk_backend"),
                           model=get_setting("ollama_model"), title="AI-verrijking",
                           family=None, active="verrijk")


_verrijk_bezig = {"aan": False}


@bp.route("/verrijk/batch", methods=["POST"])
@admin_required
@limiter.limit("6/hour")
def verrijk_batch_start():
    """Start in de achtergrond een AI-verrijkingsbatch (voorstellen -> Nazicht)."""
    import threading
    from flask import current_app
    try:
        n = max(1, min(100, int(request.form.get("n") or 10)))
    except ValueError:
        n = 10
    if _verrijk_bezig["aan"]:
        flash("Er loopt al een verrijkingsbatch. Even geduld.", "error")
        return redirect(url_for("admin.verrijk"))
    app_obj = current_app._get_current_object()

    def _job():
        _verrijk_bezig["aan"] = True
        try:
            with app_obj.app_context():
                from ..enrich import verrijk_batch
                verrijk_batch(limit=n)
        except Exception as exc:
            app_obj.logger.warning("verrijk-batch faalde: %s", str(exc)[:160])
        finally:
            _verrijk_bezig["aan"] = False

    if current_app.testing:
        _job()
    else:
        threading.Thread(target=_job, daemon=True).start()
    audit(f"AI-verrijkingsbatch gestart (n={n})")
    flash(f"Verrijking gestart voor {n} plekken. De voorstellen verschijnen in Nazicht "
          "(kan even duren op CPU — herlaad die pagina).", "ok")
    return redirect(url_for("admin.verrijk"))


@bp.route("/verrijk/voorstel/<int:pid>/<actie>", methods=["POST"])
@reviewer_required
def verrijk_voorstel(pid, actie):
    from ..models import EnrichProposal
    from ..enrich import pas_voorstel_toe
    vp = db.session.get(EnrichProposal, pid)
    if not vp or vp.status != "pending":
        abort(404)
    if actie == "goedkeuren":
        pas_voorstel_toe(vp, beschrijving=request.form.get("beschrijving"))
        audit(f"AI-voorstel goedgekeurd: #{vp.id} (event {vp.event_id})")
        flash("Voorstel toegepast op de plek.", "ok")
    elif actie == "afwijzen":
        vp.status = "rejected"
        db.session.commit()
        audit(f"AI-voorstel afgewezen: #{vp.id}")
        flash("Voorstel afgewezen.", "ok")
    return redirect(url_for("admin.nazicht"))


@bp.route("/foto/<int:pid>/<actie>", methods=["POST"])
@reviewer_required
def nazicht_foto(pid, actie):
    """Keur een gebruikersfoto goed of wijs ze af (met verwijderen van bestand)."""
    from ..models import Photo
    from ..fotos import verwijder
    from flask import url_for as _url
    p = db.session.get(Photo, pid)
    if not p or p.status != "pending":
        abort(404)
    if actie == "goedkeuren":
        p.status = "approved"
        # als de plek nog geen foto had, wordt deze de hoofdafbeelding
        if p.event and not p.event.image_url:
            p.event.image_url = _url("public.foto", pid=p.id)
        audit(f"foto goedgekeurd: #{p.id} (event {p.event_id})")
        flash("Foto goedgekeurd en zichtbaar.", "ok")
    elif actie == "afwijzen":
        verwijder(p.filename)          # bestand van schijf verwijderen
        p.status = "rejected"
        audit(f"foto afgewezen: #{p.id}")
        flash("Foto afgewezen en verwijderd.", "ok")
    db.session.commit()
    return redirect(url_for("admin.nazicht"))


@bp.app_context_processor
def _inject_admin_rol():
    """Rol van de ingelogde beheerder/reviewer beschikbaar in templates."""
    try:
        a = _huidige_admin()
        return {"admin_rol": getattr(a, "role", "admin") if a else None}
    except Exception:
        return {"admin_rol": None}


@bp.route("/team", methods=["GET", "POST"])
@admin_required
@limiter.limit("20/hour", methods=["POST"])
def team():
    """Teambeheer: reviewers toevoegen die enkel Nazicht mogen doen."""
    import re as _re
    import pyotp
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        ww = request.form.get("wachtwoord") or ""
        if not _re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            flash("Geef een geldig e-mailadres.", "error")
            return redirect(url_for("admin.team"))
        if len(ww) < 12:
            flash("Kies een wachtwoord van minstens 12 tekens.", "error")
            return redirect(url_for("admin.team"))
        if Admin.query.filter_by(email=email).first():
            flash("Er bestaat al een account met dat e-mailadres.", "error")
            return redirect(url_for("admin.team"))
        db.session.add(Admin(email=email, pw_hash=_ph.hash(ww),
                             totp_secret=pyotp.random_base32(),
                             totp_confirmed=False, role="reviewer"))
        db.session.commit()
        audit(f"reviewer aangemaakt: {email}")
        flash(f"Reviewer '{email}' aangemaakt. Die logt in op /beheer en stelt "
              "bij de eerste login 2FA in via de QR-code.", "ok")
        return redirect(url_for("admin.team"))
    leden = Admin.query.order_by(Admin.role, Admin.email).all()
    return render_template("admin/team.html", leden=leden, title="Team",
                           family=None, active="team")


@bp.route("/team/<int:aid>/verwijder", methods=["POST"])
@admin_required
def team_verwijder(aid):
    a = db.session.get(Admin, aid)
    if not a:
        abort(404)
    if a.id == session.get("admin_id"):
        flash("Je kunt jezelf niet verwijderen.", "error")
        return redirect(url_for("admin.team"))
    if a.role == "admin" and Admin.query.filter_by(role="admin").count() <= 1:
        flash("Er moet minstens één beheerder overblijven.", "error")
        return redirect(url_for("admin.team"))
    audit(f"teamlid verwijderd: {a.email} ({a.role})")
    db.session.delete(a)
    db.session.commit()
    flash(f"{'Beheerder' if a.role == 'admin' else 'Reviewer'} verwijderd.", "ok")
    return redirect(url_for("admin.team"))


@bp.route("/nazicht/claim/<int:cid>/<actie>", methods=["POST"])
@reviewer_required
def nazicht_claim(cid, actie):
    from ..models import OperatorClaim
    c = db.session.get(OperatorClaim, cid)
    if not c or c.status != "pending":
        abort(404)
    if actie == "goedkeuren":
        c.status = "approved"
        audit(f"claim goedgekeurd: operator {c.operator_id} -> event {c.event_id}")
        flash("Claim goedgekeurd — de uitbater kan nu wijzigingen voorstellen.", "ok")
    elif actie == "afwijzen":
        c.status = "rejected"
        audit(f"claim afgewezen: #{c.id}")
        flash("Claim afgewezen.", "ok")
    db.session.commit()
    return redirect(url_for("admin.nazicht"))


@bp.route("/nazicht/wijziging/<int:pid>/<actie>", methods=["POST"])
@reviewer_required
def nazicht_wijziging(pid, actie):
    from ..models import EditProposal, EDIT_VELDEN
    v = db.session.get(EditProposal, pid)
    if not v or v.status != "pending":
        abort(404)
    if actie == "goedkeuren":
        if v.event:
            for veld, waarde in (v.changes or {}).items():
                if veld in EDIT_VELDEN:            # whitelist, ook bij toepassen
                    setattr(v.event, veld, waarde)
        v.status = "approved"
        if v.event:
            from ..kwaliteit import bereken_kwaliteit
            v.event.quality = bereken_kwaliteit(v.event)
        audit(f"fichewijziging toegepast: #{v.id} (event {v.event_id})")
        flash("Wijziging toegepast op de fiche.", "ok")
    elif actie == "afwijzen":
        v.status = "rejected"
        audit(f"fichewijziging afgewezen: #{v.id}")
        flash("Wijziging afgewezen.", "ok")
    db.session.commit()
    return redirect(url_for("admin.nazicht"))


@bp.route("/partners")
@admin_required
def partners():
    """Overzicht van Partner-betalingen mét Odoo-factuurreferentie."""
    from ..models import PartnerPayment
    from .. import odoo
    betalingen = PartnerPayment.query.order_by(
        PartnerPayment.created_at.desc()).limit(200).all()
    return render_template("admin/partners.html", betalingen=betalingen,
                           odoo_actief=odoo.actief(), title="Partners",
                           family=None, active="partners")


@bp.route("/feeds", methods=["GET", "POST"])
@admin_required
@limiter.limit("30/hour", methods=["POST"])
def feeds():
    """Beheer van vertrouwde agenda-feeds (iCal/RSS) van cultuurcentra e.d."""
    from ..models import Feed
    import re as _re
    if request.method == "POST":
        url = (request.form.get("url") or "").strip()
        naam = (request.form.get("naam") or "").strip()[:160]
        if not (url.startswith("http") and naam):
            flash("Geef een naam en een geldige URL (http/https).", "error")
            return redirect(url_for("admin.feeds"))
        db.session.add(Feed(
            naam=naam, url=url[:500],
            kind="rss" if request.form.get("kind") == "rss" else "ical",
            gemeente=(request.form.get("gemeente") or "").strip()[:80] or None,
            postcode=_re.sub(r"\D", "", request.form.get("postcode") or "")[:8] or None,
            categorie=(request.form.get("categorie") or "cultuur").strip()[:40],
            trusted=bool(request.form.get("trusted")),
        ))
        db.session.commit()
        audit(f"feed toegevoegd: {naam}")
        flash("Feed toegevoegd. Draai een sync om de agenda op te halen.", "ok")
        return redirect(url_for("admin.feeds"))
    alle = Feed.query.order_by(Feed.naam).all()
    return render_template("admin/feeds.html", feeds=alle, title="Agenda-feeds",
                           family=None, active="feeds")


@bp.route("/feeds/<int:fid>/verwijder", methods=["POST"])
@admin_required
def feed_verwijder(fid):
    from ..models import Feed
    f = db.session.get(Feed, fid) or abort(404)
    audit(f"feed verwijderd: {f.naam}")
    db.session.delete(f)
    db.session.commit()
    flash("Feed verwijderd.", "ok")
    return redirect(url_for("admin.feeds"))


# Velden die de beheerder rechtstreeks op een fiche mag aanpassen.
ADMIN_EVENT_VELDEN = ("title", "description", "adres", "postcode", "gemeente",
                      "source_url", "image_url", "indoor", "is_free",
                      "age_min", "age_max")


@bp.route("/activiteiten")
@admin_required
def activiteiten():
    """Zoek- en beheeroverzicht van ALLE fiches (ook permanente plekken en
    pending). Lost op dat de admin anders niet aan de meeste data kan."""
    from ..models import Event
    zoek = (request.args.get("q") or "").strip()
    bron = request.args.get("bron", "")
    status = request.args.get("status", "")
    q = Event.query
    if zoek:
        like = f"%{zoek.lower()}%"
        q = q.filter(db.or_(db.func.lower(Event.title).like(like),
                            db.func.lower(Event.gemeente).like(like),
                            db.func.lower(Event.postcode).like(like)))
    if bron:
        q = q.filter(Event.source == bron)
    if status == "pending":
        q = q.filter(Event.pending.is_(True))
    elif status == "live":
        q = q.filter(Event.pending.is_(False))
    rijen = q.order_by(Event.updated_at.desc()).limit(200).all()
    from ..services.sources import REGISTRY
    return render_template("admin/activiteiten.html", rijen=rijen, zoek=zoek,
                           bron=bron, status=status, bronnen=list(REGISTRY),
                           title="Activiteiten", family=None, active="activiteiten")


@bp.route("/activiteiten/<int:event_id>", methods=["GET", "POST"])
@admin_required
def activiteit_bewerk(event_id):
    from ..models import Event
    ev = db.session.get(Event, event_id) or abort(404)
    if request.method == "POST":
        f = request.form
        import re as _re
        for veld in ADMIN_EVENT_VELDEN:
            if veld not in f:
                continue
            waarde = (f.get(veld) or "").strip()
            if veld in ("indoor", "is_free"):
                setattr(ev, veld, f.get(veld) == "1")
            elif veld in ("age_min", "age_max"):
                if waarde.isdigit():
                    setattr(ev, veld, int(waarde))
            elif veld == "postcode":
                ev.postcode = _re.sub(r"\D", "", waarde)[:8] or None
            else:
                setattr(ev, veld, waarde or None)
        # categorie apart (kan meerdere zijn; hier één hoofdcategorie)
        cat = (f.get("categorie") or "").strip()
        if cat:
            ev.categories = [cat]
        # pending togglen (publiceren / terug in wachtrij)
        if "pending" in f:
            ev.pending = f.get("pending") == "1"
        # coördinaten herberekenen uit postcode als gevraagd
        if f.get("herbereken_geo") and ev.postcode:
            from ..geo import postcode_coord
            coord = postcode_coord(ev.postcode)
            if coord:
                ev.lat, ev.lng = coord
        from ..kwaliteit import bereken_kwaliteit
        ev.quality = bereken_kwaliteit(ev)
        db.session.commit()
        audit(f"activiteit bewerkt door admin: #{ev.id} '{ev.title}'")
        flash("Fiche opgeslagen.", "ok")
        return redirect(url_for("admin.activiteit_bewerk", event_id=ev.id))
    from ..models import CATEGORIES
    return render_template("admin/activiteit_bewerk.html", ev=ev,
                           categories=CATEGORIES, title=f"Bewerk: {ev.title}",
                           family=None, active="activiteiten")


@bp.route("/activiteiten/<int:event_id>/verwijder", methods=["POST"])
@admin_required
def activiteit_verwijder(event_id):
    from ..models import Event
    ev = db.session.get(Event, event_id) or abort(404)
    titel = ev.title
    db.session.delete(ev)
    db.session.commit()
    audit(f"activiteit verwijderd door admin: '{titel}'")
    flash(f"'{titel}' verwijderd.", "ok")
    return redirect(url_for("admin.activiteiten"))
