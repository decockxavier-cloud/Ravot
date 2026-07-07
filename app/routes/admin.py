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
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _huidige_admin() or not session.get("admin_2fa_ok"):
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

    # Populairste gemeenten (naar aantal komende events)
    top_gemeenten = db.session.query(
        Event.gemeente, db.func.count(Event.id).label("n")) \
        .filter(Event.start >= now, Event.gemeente.isnot(None)) \
        .group_by(Event.gemeente).order_by(db.text("n DESC")).limit(8).all()

    # Recentste aanmeldingen
    nieuwste_gezinnen = Family.query.order_by(Family.created_at.desc()).limit(5).all()
    recent_reviews = Review.query.order_by(Review.created_at.desc()).limit(10).all()

    return render_template("admin/dashboard.html", stats=stats,
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
    status["bronnen"] = [
        {"code": "tm", "naam": "Ticketmaster (Family)", "aan": _gb("bron_tm_aan"),
         "key_nodig": True, "geconfigureerd": bool(cfg.get("TICKETMASTER_API_KEY")),
         "aantal": Event.query.filter_by(source="tm").count(), "test": True},
        {"code": "tv", "naam": "Toerisme Vlaanderen", "aan": _gb("bron_tv_aan"),
         "key_nodig": False, "geconfigureerd": True,
         "aantal": Event.query.filter_by(source="tv").count(), "test": False},
        {"code": "osm", "naam": "OpenStreetMap (speeltuinen e.d.)", "aan": _gb("bron_osm_aan"),
         "key_nodig": False, "geconfigureerd": True,
         "aantal": Event.query.filter_by(source="osm").count(), "test": False},
    ]
    return render_template("admin/verbindingen.html", status=status,
                           syncstatus=syncstatus, sync_bezig=sync_bezig,
                           title="Verbindingen", family=None, active=None)


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
