"""Adminpaneel — afgeschermd pad, wachtwoord (Argon2id) + verplichte TOTP-2FA,
aparte sessievlag, alle acties in de audit log (strategienota §8.1)."""
from functools import wraps
from datetime import datetime, timedelta

import pyotp
from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request,
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
    """Enkel volle beheerders (role='admin'). Voor gevoelige zaken: team-beheer
    en financiën (Mollie/facturatie)."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        a = _huidige_admin()
        if not a or not session.get("admin_2fa_ok"):
            return redirect(url_for("admin.login"))
        if getattr(a, "role", "admin") != "admin":
            abort(403)   # medewerker/reviewer probeert een admin-only pagina
        return f(*args, **kwargs)
    return wrapper


def medewerker_required(f):
    """Beheerders én medewerkers: bijna de volledige backend (content,
    databronnen, gezinnen, partners, instellingen, nazicht). NIET team-beheer
    of financiën — die blijven admin_required. Reviewers hebben hier géén
    toegang (enkel nazicht)."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        a = _huidige_admin()
        if not a or not session.get("admin_2fa_ok"):
            return redirect(url_for("admin.login"))
        if getattr(a, "role", "admin") not in ("admin", "medewerker"):
            abort(403)   # reviewer heeft hier geen toegang
        return f(*args, **kwargs)
    return wrapper


def gezinnen_toegang(f):
    """Gezinsdata (persoonsgegevens): admins altijd; medewerkers enkel als de
    instelling 'medewerker_ziet_gezinnen' aan staat. Reviewers nooit."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        a = _huidige_admin()
        if not a or not session.get("admin_2fa_ok"):
            return redirect(url_for("admin.login"))
        rol = getattr(a, "role", "admin")
        if rol == "admin":
            return f(*args, **kwargs)
        from ..models import get_bool
        if rol == "medewerker" and get_bool("medewerker_ziet_gezinnen"):
            return f(*args, **kwargs)
        abort(403)
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
@medewerker_required
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

    # --- To-do: alles wat de aandacht van de beheerder vraagt, op één plek ---
    from ..models import (Report, EnrichProposal, Photo, OperatorClaim,
                          EditProposal, get_bool)
    n_wachtrij = Event.query.filter_by(pending=True).count()
    n_meldingen = Report.query.filter_by(handled=False).filter(
        db.not_(Report.reason.like("voorziening:%"))).count()
    n_conflict = Report.query.filter_by(handled=False).filter(
        Report.reason.like("voorziening:%")).count()
    n_fotos = Photo.query.filter_by(status="pending").count()
    n_claims = OperatorClaim.query.filter_by(status="pending").count()
    n_edits = EditProposal.query.filter_by(status="pending").count()
    n_ai = EnrichProposal.query.filter_by(status="pending").count()
    n_werkvoorraad = Event.query.filter(
        Event.curated.is_(True), Event.nagekeken.is_(False),
        Event.hidden.is_(False)).count()
    taken = []
    if n_wachtrij:
        taken.append(("Nieuwe inzendingen na te kijken", n_wachtrij,
                      url_for("admin.nazicht"), "📥"))
    if n_meldingen:
        taken.append(("Meldingen van gezinnen", n_meldingen,
                      url_for("admin.nazicht"), "🚩"))
    if n_conflict:
        taken.append(("Betwiste voorzieningen na te kijken", n_conflict,
                      url_for("admin.nazicht"), "⚖️"))
    if n_claims:
        taken.append(("Uitbaters die hun zaak claimen", n_claims,
                      url_for("admin.nazicht"), "🤝"))
    if n_edits:
        taken.append(("Fichewijzigingen van uitbaters", n_edits,
                      url_for("admin.nazicht"), "✏️"))
    if n_fotos:
        taken.append(("Foto's na te kijken", n_fotos,
                      url_for("admin.nazicht"), "📷"))
    if n_ai:
        taken.append(("AI-verrijkingsvoorstellen", n_ai,
                      url_for("admin.nazicht"), "🤖"))
    if n_werkvoorraad:
        taken.append(("Gecureerde fiches in de werkvoorraad", n_werkvoorraad,
                      url_for("admin.activiteiten", status="nakijken"), "📋"))
    taken_totaal = sum(t[1] for t in taken)

    # Systeemstatus in één oogopslag (lichte, lokale checks + sync-versheid).
    from ..services.health import dashboard_samenvatting
    from ..models import MailLog
    try:
        systeem = dashboard_samenvatting()
    except Exception:
        current_app.logger.exception("dashboard_samenvatting mislukt")
        systeem = {"problemen": 0, "waarschuwingen": 0, "items": [], "gezond": True}
    # Laatste automatische mails (weekend/maandag) — recentste per soort.
    laatste_mails = []
    for soort in ("weekendmail", "maandagmail"):
        m = MailLog.query.filter_by(soort=soort).order_by(
            MailLog.created_at.desc()).first()
        if m:
            laatste_mails.append({"soort": soort, "ok": m.ok,
                                  "detail": m.detail, "wanneer": m.created_at})

    return render_template("admin/dashboard.html", kwaliteit=kwaliteit, stats=stats,
                           top_gemeenten=top_gemeenten, nieuwste_gezinnen=nieuwste_gezinnen,
                           reviews=recent_reviews, taken=taken,
                           taken_totaal=taken_totaal, systeem=systeem,
                           laatste_mails=laatste_mails, title="Dashboard",
                           family=None, active="dashboard")


@bp.route("/review/<int:review_id>/verwijder", methods=["POST"])
@medewerker_required
def delete_review(review_id):
    rv = db.session.get(Review, review_id) or abort(404)
    audit(f"review {review_id} verwijderd (event {rv.event_id})")
    db.session.delete(rv)
    db.session.commit()
    flash("Review verwijderd.", "ok")
    return redirect(url_for("admin.dashboard"))


# Centrale indeling van alle instellingen. Elke SETTING_DEFS-key hoort in
# precies één pagina thuis; het vangnet "Overige" (op de kernpagina) vangt
# nieuwe, nog niet ingedeelde keys op.
INSTELLING_PAGINAS = {
    "kern": [
        ("Weergave & gedrag", ["default_radius", "toon_maanden_vooruit",
                               "ontdek_per_pagina", "onderhoud_aan"]),
        ("Team & toegang", ["medewerker_ziet_gezinnen"]),
        ("Ranking & kwaliteit", ["kwaliteit_min_lijst", "kwaliteit_hoog",
                                 "enkel_gecureerd", "verborgen_types",
                                 "score_prior_n", "score_prior_waarde",
                                 "partner_score_bonus", "geen_partner_malus",
                                 "foto_malus", "tag_drempel", "report_drempel"]),
        ("Ravot-label (kwaliteitslabel)", ["label_aan", "label_min_voorzieningen",
                                           "label_min_reviews"]),
        ("Weer", ["weer_aan", "regen_drempel", "zon_drempel"]),
        ("Mails", ["weekendmail_aan", "maandagmail_aan"]),
        ("Beveiliging & limieten", ["codes_per_uur", "punten_dag_max",
                                    "geweest_dag_max", "wissel_min_dagen"]),
        ("AI-verrijking", ["verrijk_backend", "ollama_model", "cloud_model"]),
    ],
    "facturatie": [
        ("Partner-abonnement", ["partner_prijs_jaar",
                                "partner_btw_pct", "founding_aan", "founding_max"]),
        ("Facturatie (Odoo)", ["odoo_product_id", "odoo_factuur_auto"]),
    ],
    "verbindingen": [
        ("UiTdatabank", ["bron_uit_aan", "uit_query", "sync_max_pages"]),
        ("OpenStreetMap", ["bron_osm_aan", "osm_tags", "osm_horeca_aan",
                           "osm_regios"]),
    ],
    "feestjes": [
        ("Feestjesmodule", ["feestjes_aan", "feest_straal_km",
                            "feest_max_aanvragen", "feest_enkel_partners"]),
        ("Kampenmodule", ["kampen_aan", "kamp_marge_dagen"]),
    ],
    "beloningen": [
        ("Beloningen & punten", ["beloningen_aan", "punt_waarde_eur",
                                 "punten_geldig_maanden"]),
    ],
}


def _instellingen_context(pagina):
    """Groepen + waarden voor één instellingenpagina (met vangnet op 'kern')."""
    from ..models import SETTING_DEFS, get_setting
    groepen = list(INSTELLING_PAGINAS[pagina])
    if pagina == "kern":
        gebruikt = {k for pg in INSTELLING_PAGINAS.values() for _, keys in pg
                    for k in keys}
        rest = [k for k in SETTING_DEFS if k not in gebruikt]
        if rest:
            groepen.append(("Overige", rest))
    keys = [k for _, ks in groepen for k in ks]
    waarden = {k: get_setting(k) for k in keys}
    return groepen, waarden, SETTING_DEFS


@bp.route("/instellingen/opslaan", methods=["POST"])
@medewerker_required
def instellingen_opslaan():
    """Gedeelde opslag voor álle instellingenpagina's. Verwerkt ENKEL de keys
    die het formulier zelf beheert (veld _keys) — zo kan een uitgevinkte
    checkbox op pagina A nooit een schakelaar op pagina B omgooien."""
    from ..models import Setting, SETTING_DEFS
    keys = [k for k in (request.form.get("_keys") or "").split(",")
            if k in SETTING_DEFS]
    gewijzigd = []
    for key in keys:
        default, label, typ = SETTING_DEFS[key]
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
    doel = request.form.get("_terug") or url_for("admin.instellingen")
    if not doel.startswith("/beheer"):
        doel = url_for("admin.instellingen")
    return redirect(doel)


@bp.route("/instellingen")
@medewerker_required
def instellingen():
    """Kerninstellingen. Domeinspecifieke instellingen staan bij hun domein:
    bronnen bij Verbindingen, prijzen bij Facturatie, enzovoort."""
    groepen, waarden, defs = _instellingen_context("kern")
    return render_template("admin/instellingen.html", defs=defs,
                           waarden=waarden, groepen=groepen,
                           title="Instellingen",
                           family=None, active="instellingen")


@bp.route("/facturatie")
@admin_required
def facturatie():
    """Alles rond geld op één plek: abonnementsprijzen, btw en Odoo."""
    groepen, waarden, defs = _instellingen_context("facturatie")
    return render_template("admin/facturatie.html", defs=defs,
                           waarden=waarden, groepen=groepen,
                           title="Facturatie", family=None, active="facturatie")


@bp.route("/verbindingen")
@medewerker_required
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
    inst_groepen, inst_waarden, inst_defs = _instellingen_context("verbindingen")
    bron_tellingen = dict(db.session.query(Event.source, db.func.count(Event.id))
                          .group_by(Event.source).all())
    return render_template("admin/verbindingen.html",
                           bron_tellingen=bron_tellingen,
                           groepen=inst_groepen, waarden=inst_waarden, defs=inst_defs, status=status,
                           syncstatus=syncstatus, sync_bezig=sync_bezig,
                           title="Verbindingen", family=None, active=None)


@bp.route("/test-ollama", methods=["POST"])
@medewerker_required
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
@medewerker_required
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
@medewerker_required
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
@medewerker_required
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
@medewerker_required
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
@medewerker_required
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
@gezinnen_toegang
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
@gezinnen_toegang
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
        elif actie == "nieuwsbrief":
            fam.newsletter_opt_in = not fam.newsletter_opt_in
            db.session.commit()
            audit(f"gezin {fid} nieuwsbrief {'aan' if fam.newsletter_opt_in else 'uit'}")
            flash("Nieuwsbrief " + ("ingeschakeld." if fam.newsletter_opt_in
                                    else "uitgeschakeld."), "ok")
        elif actie == "punten":
            # Handmatige correctie: bonus (bv. wedstrijd) of rechtzetting bij
            # misbruik. Negatief mag; het niveau volgt het nieuwe totaal.
            from ..models import RavotPunt
            try:
                aantal = int(request.form.get("aantal") or 0)
            except ValueError:
                aantal = 0
            reden = (request.form.get("reden") or "").strip()[:100]
            if aantal and reden:
                import time as _t
                db.session.add(RavotPunt(family_id=fid, reden="admin",
                                         ref_id=int(_t.time()), punten=aantal))
                db.session.commit()
                audit(f"gezin {fid}: {aantal:+d} punten ({reden})")
                flash(f"{aantal:+d} punten toegekend ({reden}).", "ok")
            else:
                flash("Aantal én reden zijn verplicht.", "error")
        elif actie == "verwijder":
            # GDPR: alle gekoppelde data mee verwijderen
            from ..models import (DagUitstap, Feestje, Inwissel, Photo,
                                  RavotPunt)
            Review.query.filter_by(family_id=fid).delete()
            SavedEvent.query.filter_by(family_id=fid).delete()
            Interaction.query.filter_by(family_id=fid).delete()
            RavotPunt.query.filter_by(family_id=fid).delete()
            Inwissel.query.filter_by(family_id=fid).delete()
            for f in Feestje.query.filter_by(family_id=fid).all():
                db.session.delete(f)          # cascade: aanvragen mee
            DagUitstap.query.filter_by(family_id=fid).delete()
            Photo.query.filter_by(family_id=fid) \
                .update({"family_id": None})  # foto's anonimiseren
            db.session.delete(fam)
            db.session.commit()
            audit(f"gezin {fid} volledig verwijderd (GDPR)")
            flash("Gezin en alle gekoppelde data verwijderd.", "ok")
            return redirect(url_for("admin.families"))
        return redirect(url_for("admin.family_detail", fid=fid))
    aantal_reviews = Review.query.filter_by(family_id=fid).count()
    aantal_bewaard = SavedEvent.query.filter_by(family_id=fid).count()
    # Detail: uitstappen, scores, Ravotpas en inwisselingen — het volledige
    # dossier op één scherm, zodat je vragen en misbruik zelf kan beoordelen.
    from ..models import Event, Inwissel, INWISSEL_STATUSSEN, RavotPunt
    from .. import punten as pas
    bewaard = db.session.query(SavedEvent, Event) \
        .join(Event, Event.id == SavedEvent.event_id) \
        .filter(SavedEvent.family_id == fid) \
        .order_by(SavedEvent.created_at.desc()).limit(20).all()
    scores = db.session.query(Review, Event) \
        .join(Event, Event.id == Review.event_id) \
        .filter(Review.family_id == fid) \
        .order_by(Review.created_at.desc()).limit(20).all()
    puntlog = RavotPunt.query.filter_by(family_id=fid) \
        .order_by(RavotPunt.created_at.desc()).limit(15).all()
    inwissels = Inwissel.query.filter_by(family_id=fid) \
        .order_by(Inwissel.created_at.desc()).all()
    pas_totaal = pas.totaal(fid)
    return render_template("admin/family_detail.html", fam=fam,
                           aantal_reviews=aantal_reviews, aantal_bewaard=aantal_bewaard,
                           bewaard=bewaard, scores=scores, puntlog=puntlog,
                           inwissels=inwissels, statussen=INWISSEL_STATUSSEN,
                           pas_totaal=pas_totaal, pas_saldo=pas.saldo(fid),
                           pas_niveau=pas.niveau(pas_totaal),
                           title=f"Gezin {fam.email}", family=None, active=None)


@bp.route("/paginas", methods=["GET"])
@medewerker_required
def paginas():
    from ..models import ContentPage, CONTENT_PAGES
    pages = []
    for slug, titel in CONTENT_PAGES.items():
        cp = db.session.get(ContentPage, slug)
        pages.append({"slug": slug, "titel": titel, "bewerkt": cp.updated_at if cp else None})
    return render_template("admin/paginas.html", pages=pages,
                           title="Inhoudspagina's", family=None, active=None)


@bp.route("/paginas/<slug>", methods=["GET", "POST"])
@medewerker_required
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
@medewerker_required
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
@medewerker_required
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
        # Goedkeuren ÍS cureren: de beheerder bekeek de plek zelf. Zonder
        # kwaliteitsscore viel ze bovendien buiten het kaart-contingent en
        # bleef ze onzichtbaar — de bug van de "verdwenen" gezinsplek.
        ev.curated = True
        ev.nagekeken = True
        from ..kwaliteit import bereken_kwaliteit
        ev.quality = bereken_kwaliteit(ev, heeft_reviews=False)
        if ev.submitted_by:
            from .. import punten as pas
            pas.ken_toe(ev.submitted_by, "plek", ev.id)
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
@medewerker_required
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
    # tellers per zone zodat de admin ziet waar de winst zit
    from ..models import get_int, EnrichProposal
    k_min = get_int("kwaliteit_min_lijst", 30)
    k_hoog = get_int("kwaliteit_hoog", 60)
    heeft_voorstel = db.session.query(EnrichProposal.event_id)
    basis = Event.query.filter(Event.is_permanent.is_(True),
                               Event.pending.is_(False),
                               Event.hidden.is_(False),
                               ~Event.id.in_(heeft_voorstel))
    tellers = {
        "midden": basis.filter(Event.quality >= k_min, Event.quality < k_hoog).count(),
        "totaal_open": basis.count(),
        "k_min": k_min, "k_hoog": k_hoog,
    }
    return render_template("admin/verrijk.html", voorstel=voorstel, plek=plek,
                           fout=fout, recent=recent, tellers=tellers,
                           backend=get_setting("verrijk_backend"),
                           model=get_setting("ollama_model"), title="AI-verrijking",
                           family=None, active="verrijk")


_verrijk_bezig = {"aan": False}


@bp.route("/verrijk/batch", methods=["POST"])
@medewerker_required
@limiter.limit("6/hour")
def verrijk_batch_start():
    """Start in de achtergrond een AI-verrijkingsbatch (voorstellen -> Nazicht)."""
    import threading
    from flask import current_app
    try:
        n = max(1, min(100, int(request.form.get("n") or 10)))
    except ValueError:
        n = 10
    zone = "alles" if request.form.get("zone") == "alles" else "midden"
    if _verrijk_bezig["aan"]:
        flash("Er loopt al een verrijkingsbatch. Even geduld.", "error")
        return redirect(url_for("admin.verrijk"))
    app_obj = current_app._get_current_object()

    def _job():
        _verrijk_bezig["aan"] = True
        try:
            with app_obj.app_context():
                from ..enrich import verrijk_batch
                verrijk_batch(limit=n, zone=zone)
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
        # Ravotpas: punten voor de uploader; extra bonus voor de állereerste
        # foto van een plek (lost precies het foto-gat in de catalogus op).
        from .. import punten as pas
        if p.family_id:
            pas.ken_toe(p.family_id, "foto", p.event_id)
        # als de plek nog geen foto had, wordt deze de hoofdafbeelding
        if p.event and not p.event.image_url:
            p.event.image_url = _url("public.foto", pid=p.id)
            if p.family_id:
                pas.ken_toe(p.family_id, "eerste_foto", p.event_id)
        audit(f"foto goedgekeurd: #{p.id} (event {p.event_id})")
        flash("Foto goedgekeurd en zichtbaar.", "ok")
    elif actie == "afwijzen":
        verwijder(p.filename)          # bestand van schijf verwijderen
        p.status = "rejected"
        p.weiger_reden = (request.form.get("weiger_reden") or "").strip()[:60] or None
        audit(f"foto afgewezen: #{p.id}"
              + (f" ({p.weiger_reden})" if p.weiger_reden else ""))
        flash("Foto afgewezen en verwijderd.", "ok")
    db.session.commit()
    return redirect(url_for("admin.nazicht"))


@bp.app_context_processor
def _inject_admin_rol():
    """Rol van de ingelogde beheerder/reviewer beschikbaar in templates."""
    try:
        a = _huidige_admin()
        rol = getattr(a, "role", "admin") if a else None
        from ..models import get_bool
        mag_gezinnen = rol == "admin" or (
            rol == "medewerker" and get_bool("medewerker_ziet_gezinnen"))
        return {"admin_rol": rol, "mag_gezinnen": mag_gezinnen}
    except Exception:
        return {"admin_rol": None, "mag_gezinnen": False}


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
        rol = request.form.get("rol")
        if rol not in ("reviewer", "medewerker"):
            rol = "reviewer"
        db.session.add(Admin(email=email, pw_hash=_ph.hash(ww),
                             totp_secret=pyotp.random_base32(),
                             totp_confirmed=False, role=rol))
        db.session.commit()
        audit(f"{rol} aangemaakt: {email}")
        rol_naam = "Medewerker" if rol == "medewerker" else "Reviewer"
        flash(f"{rol_naam} '{email}' aangemaakt. Die logt in op /beheer en stelt "
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
        # Bevestigingsmail naar de uitbater (warm, met link naar de fiche).
        from ..models import Operator
        from ..services import uitbater_mail
        op = db.session.get(Operator, c.operator_id)
        if op and c.event:
            try:
                uitbater_mail.claim_goedgekeurd(
                    op.email, c.event.title,
                    url_for("uitbater.fiche", event_id=c.event_id, _external=True))
            except Exception:
                current_app.logger.exception("claim-bevestigingsmail mislukt")
        flash("Claim goedgekeurd — de uitbater is verwittigd en kan nu wijzigingen voorstellen.", "ok")
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
    """Overzicht van Partner-betalingen mét Odoo-factuurreferentie.
    Admin-only: bevat omzet- en factuurgegevens."""
    from datetime import datetime, timedelta
    from ..models import PartnerPayment
    from .. import mollie, odoo
    nu = datetime.utcnow()
    betalingen = PartnerPayment.query.order_by(
        PartnerPayment.created_at.desc()).limit(200).all()
    # Actieve lidmaatschappen: zaken met een lopende partner-periode, met hun
    # laatste betaalde plan en de uitbater erbij.
    actieve = Event.query.filter(Event.partner_until.isnot(None),
                                 Event.partner_until >= nu)         .order_by(Event.partner_until).all()
    laatste_per_event = {}
    for b in betalingen:
        if b.status == "paid" and b.event_id not in laatste_per_event:
            laatste_per_event[b.event_id] = b
    leden = [{"ev": ev, "betaling": laatste_per_event.get(ev.id),
              "verloopt_snel": ev.partner_until <= nu + timedelta(days=14)}
             for ev in actieve]
    jaar_start = datetime(nu.year, 1, 1)
    omzet_jaar = sum(float(b.amount) for b in betalingen
                     if b.status == "paid" and b.paid_at and b.paid_at >= jaar_start)
    zonder_factuur = [b for b in betalingen
                      if b.status == "paid" and not b.odoo_invoice_ref]
    # Feestpartner-evaluatie: wie krijgt aanvragen, en wie maakt ze waar?
    from ..models import FeestjeAanvraag
    feest_stats = db.session.query(
        FeestjeAanvraag.event_id,
        db.func.count(FeestjeAanvraag.id).label("aanvragen"),
        db.func.sum(db.case((FeestjeAanvraag.status == "bevestigd", 1),
                            else_=0)).label("bevestigd"),
        db.func.sum(db.case((FeestjeAanvraag.status == "beantwoord", 1),
                            else_=0)).label("beantwoord"),
    ).group_by(FeestjeAanvraag.event_id) \
     .order_by(db.desc("aanvragen")).limit(30).all()
    feest_zaken = {e.id: e for e in Event.query.filter(
        Event.id.in_([r.event_id for r in feest_stats])).all()} if feest_stats else {}
    return render_template("admin/partners.html",
                           feest_stats=feest_stats, feest_zaken=feest_zaken, betalingen=betalingen,
                           leden=leden, omzet_jaar=omzet_jaar,
                           zonder_factuur=zonder_factuur,
                           mollie_actief=mollie.actief(),
                           odoo_actief=odoo.actief(), title="Partners",
                           family=None, active="partners")


@bp.route("/partners/factuur/<int:pid>", methods=["POST"])
@admin_required
def partner_factuur_alsnog(pid):
    """Betaalde betaling zonder Odoo-factuur: alsnog aanmaken (bv. nadat de
    Odoo-koppeling later geconfigureerd werd)."""
    from ..models import PartnerPayment
    from .. import odoo
    b = db.session.get(PartnerPayment, pid) or abort(404)
    if b.status != "paid" or b.odoo_invoice_ref:
        flash("Deze betaling heeft geen factuur nodig.", "error")
        return redirect(url_for("admin.partners"))
    try:
        odoo.factureer_betaling(b)
        db.session.commit()
        audit(f"odoo-factuur alsnog aangemaakt voor betaling #{b.id}")
        flash(f"Factuur aangemaakt: {b.odoo_invoice_ref or 'zie Odoo'}.", "ok")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("odoo-nafacturatie faalde")
        flash("Factuur aanmaken mislukte — check de Odoo-koppeling op de "
              "Status-pagina.", "error")
    return redirect(url_for("admin.partners"))


@bp.route("/partners/handmatig", methods=["POST"])
@admin_required
def partner_handmatig():
    """Maak een zaak handmatig (gratis) Partner voor een aantal maanden — bv.
    voor een pilootpartner, een bevriende zaak of een persoonlijk overtuigde
    uitbater. Legt een PartnerPayment met plan='handmatig', bedrag 0 vast, zodat
    het lidmaatschap traceerbaar is en netjes verloopt."""
    from datetime import datetime, timedelta
    from ..models import PartnerPayment
    slug_of_id = (request.form.get("event") or "").strip()
    try:
        maanden = max(1, min(60, int(request.form.get("maanden") or 12)))
    except ValueError:
        maanden = 12
    ev = None
    if slug_of_id.isdigit():
        ev = db.session.get(Event, int(slug_of_id))
    if not ev:
        ev = Event.query.filter_by(slug=slug_of_id).first()
    if not ev:
        flash("Geen zaak gevonden met dat id of die slug.", "error")
        return redirect(url_for("admin.partners"))
    basis = ev.partner_until if (ev.partner_until and ev.partner_until > datetime.utcnow()) \
        else datetime.utcnow()
    ev.partner_until = basis + timedelta(days=round(maanden * 30.4))
    db.session.add(PartnerPayment(operator_id=None, event_id=ev.id,
                                  plan="handmatig", amount="0.00", status="paid",
                                  paid_at=datetime.utcnow()))
    db.session.commit()
    audit(f"handmatig partner gemaakt: {ev.title} (+{maanden} mnd, gratis)")
    flash(f"✅ {ev.title} is nu Partner tot {ev.partner_until.strftime('%d/%m/%Y')} "
          "(handmatig, gratis).", "ok")
    return redirect(url_for("admin.partners"))


@bp.route("/feeds", methods=["GET", "POST"])
@medewerker_required
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
@medewerker_required
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
                      "age_min", "age_max", "omheind", "verzorgingstafel",
                      "buggy_ok", "feest", "feest_contact")


@bp.route("/activiteiten")
@medewerker_required
def activiteiten():
    """Zoek- en beheeroverzicht van ALLE fiches (ook permanente plekken en
    pending), met sortering en een focus op wat aangevuld moet worden."""
    from ..models import Event, EnrichProposal, get_int
    zoek = (request.args.get("q") or "").strip()
    bron = request.args.get("bron", "")
    # Standaard opent de pagina op de werkvoorraad: gecureerde fiches die je
    # nog niet nakeek. Afwerken in plaats van zoeken.
    status = request.args.get("status", "nakijken")
    sort = request.args.get("sort", "kwaliteit-op")   # kwaliteit-op|kwaliteit-af|recent
    q = Event.query
    if zoek:
        like = f"%{zoek.lower()}%"
        q = q.filter(db.or_(db.func.lower(Event.title).like(like),
                            db.func.lower(Event.gemeente).like(like),
                            db.func.lower(Event.postcode).like(like)))
    if bron:
        q = q.filter(Event.source == bron)
    soort = request.args.get("soort", "")
    if soort == "_events":
        q = q.filter(Event.is_permanent.is_(False))
    elif soort == "_plekken":
        q = q.filter(Event.is_permanent.is_(True))
    elif soort == "_horeca":
        q = q.filter(Event.subtype == "horeca")
    elif soort == "_speeltuin":
        q = q.filter(Event.subtype.in_(("playground", "park")))
    elif soort:
        q = q.filter(Event.subtype == soort)
    if status == "pending":
        q = q.filter(Event.pending.is_(True))
    elif status == "live":
        q = q.filter(Event.pending.is_(False))
    elif status == "aanvullen":
        # De middenzone zonder AI-voorstel: precies wat verrijking nodig heeft.
        k_min = get_int("kwaliteit_min_lijst", 30)
        k_hoog = get_int("kwaliteit_hoog", 60)
        heeft_voorstel = db.session.query(EnrichProposal.event_id)
        q = q.filter(Event.quality >= k_min, Event.quality < k_hoog,
                     ~Event.id.in_(heeft_voorstel))
    elif status == "gecureerd":
        q = q.filter(Event.curated.is_(True))
    elif status == "nakijken":
        q = q.filter(Event.curated.is_(True), Event.nagekeken.is_(False),
                     Event.hidden.is_(False))
    elif status == "klaar":
        q = q.filter(Event.curated.is_(True), Event.nagekeken.is_(True),
                     Event.hidden.is_(False))
    elif status == "tebeoordelen":
        q = q.filter(Event.curated.is_(False), Event.is_permanent.is_(True))
    if sort == "kwaliteit-af":
        q = q.order_by(Event.quality.desc().nullslast())
    elif sort == "recent":
        q = q.order_by(Event.updated_at.desc())
    else:  # kwaliteit-op: zwakste (of dichtst bij groen) eerst — standaard
        q = q.order_by(Event.quality.asc().nullsfirst())
    rijen = q.limit(200).all()
    from ..services.sources import REGISTRY
    tellers = {
        "nakijken": Event.query.filter(Event.curated.is_(True),
                                       Event.nagekeken.is_(False),
                                       Event.hidden.is_(False)).count(),
        "klaar": Event.query.filter(Event.curated.is_(True),
                                    Event.nagekeken.is_(True),
                                    Event.hidden.is_(False)).count(),
        "pending": Event.query.filter(Event.pending.is_(True)).count(),
    }
    from ..types import TYPES
    return render_template("admin/activiteiten.html", rijen=rijen, zoek=zoek,
                           bron=bron, status=status, sort=sort, soort=soort,
                           bronnen=["uit", "osm", "overture", "user"],
                           soorten=TYPES, tellers=tellers,
                           title="Activiteiten", family=None, active="activiteiten")


def _parse_openingsuren(f):
    """Bouw de openingsuren-JSON uit formuliervelden open_<dag>/sluit_<dag> en
    dicht_<dag> (checkbox 'gesloten'). Lege dagen worden 'onbekend' (weggelaten)."""
    import re as _re
    from ..services.openingsuren import DAGEN
    uren = {}
    for d in DAGEN:
        if f.get(f"dicht_{d}"):
            uren[d] = None            # expliciet gesloten
            continue
        o = (f.get(f"open_{d}") or "").strip()
        s = (f.get(f"sluit_{d}") or "").strip()
        if _re.match(r"^\d{1,2}:\d{2}$", o) and _re.match(r"^\d{1,2}:\d{2}$", s):
            uren[d] = [o, s]
    return uren or None


@bp.route("/activiteiten/<int:event_id>", methods=["GET", "POST"])
@medewerker_required
def activiteit_bewerk(event_id):
    from ..models import Event, CATEGORIES
    ev = db.session.get(Event, event_id) or abort(404)
    voorstel = None
    if request.method == "POST":
        f = request.form
        # --- AI-voorstel genereren (vult de velden, slaat NIET op) ---
        if f.get("actie") == "verrijk":
            from ..enrich import verrijk_plek
            extra_url = (f.get("verrijk_url") or "").strip() or None
            try:
                voorstel = verrijk_plek(ev, extra_url=extra_url)
                bron = "op basis van de website" if voorstel.get("webtekst_gebruikt") else "op basis van de bekende gegevens"
                flash(f"AI-voorstel ingevuld ({bron}) — controleer, pas aan en klik Opslaan.", "ok")
            except Exception as exc:
                flash(f"AI-verrijking mislukt: {str(exc)[:150]}. Draait Ollama?", "error")
            from ..models import FEEST_SOORTEN as _FS
            return render_template("admin/activiteit_bewerk.html", ev=ev,
                                   categories=CATEGORIES, voorstel=voorstel,
                                   feest_soorten=_FS,
                                   title=f"Bewerk: {ev.title}", family=None,
                                   active="activiteiten")
        # --- Opslaan ---
        import re as _re
        for veld in ADMIN_EVENT_VELDEN:
            if veld not in f:
                continue
            waarde = (f.get(veld) or "").strip()
            if veld in ("indoor", "is_free", "feest"):
                setattr(ev, veld, f.get(veld) == "1")
            elif veld in ("omheind", "verzorgingstafel", "buggy_ok"):
                # tri-state: '' = onbekend (None), '1' = ja, '0' = nee
                setattr(ev, veld, None if waarde == "" else waarde == "1")
            elif veld in ("age_min", "age_max"):
                if waarde.isdigit():
                    setattr(ev, veld, int(waarde))
            elif veld == "postcode":
                ev.postcode = _re.sub(r"\D", "", waarde)[:8] or None
            else:
                setattr(ev, veld, waarde or None)
        from ..models import FEEST_SOORTEN
        if "feest" in f:
            ev.feest_soorten = [s for s in f.getlist("feest_soorten")
                                if s in FEEST_SOORTEN]
        cat = (f.get("categorie") or "").strip()
        if cat:
            ev.categories = [cat]
        # Openingsuren: per dag open/sluit uit het formulier -> JSON.
        ev.openingsuren = _parse_openingsuren(f)
        if "pending" in f:
            ev.pending = f.get("pending") == "1"
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
    from ..models import FEEST_SOORTEN as _FS2, OperatorClaim, Operator
    actieve_claims = db.session.query(OperatorClaim, Operator).join(
        Operator, OperatorClaim.operator_id == Operator.id).filter(
        OperatorClaim.event_id == ev.id,
        OperatorClaim.status.in_(("pending", "approved"))).all()
    return render_template("admin/activiteit_bewerk.html", ev=ev,
                           categories=CATEGORIES, voorstel=voorstel,
                           feest_soorten=_FS2, actieve_claims=actieve_claims,
                           title=f"Bewerk: {ev.title}", family=None,
                           active="activiteiten")


@bp.route("/activiteiten/<int:event_id>/ontclaim", methods=["POST"])
@medewerker_required
def activiteit_ontclaim(event_id):
    """Trek alle goedgekeurde/openstaande claims op een zaak in, zodat de
    uitbater de toegang verliest. Handig als een claim onterecht bleek."""
    from ..models import OperatorClaim
    ev = db.session.get(Event, event_id) or abort(404)
    claims = OperatorClaim.query.filter_by(event_id=event_id).filter(
        OperatorClaim.status.in_(("pending", "approved"))).all()
    for c in claims:
        c.status = "rejected"
    db.session.commit()
    audit(f"claims ingetrokken op event #{event_id} '{ev.title}' ({len(claims)})")
    flash(f"{len(claims)} claim(s) ingetrokken — de uitbater heeft geen toegang meer.", "ok")
    return redirect(url_for("admin.activiteit_bewerk", event_id=event_id))


@bp.route("/activiteiten/<int:event_id>/verwijder", methods=["POST"])
@medewerker_required
def activiteit_verwijder(event_id):
    from ..models import Event
    ev = db.session.get(Event, event_id) or abort(404)
    titel = ev.title
    db.session.delete(ev)
    db.session.commit()
    audit(f"activiteit verwijderd door admin: '{titel}'")
    flash(f"'{titel}' verwijderd.", "ok")
    return redirect(url_for("admin.activiteiten"))


@bp.route("/types", methods=["GET", "POST"])
@medewerker_required
def types_beheer():
    """Per activiteittype kiezen of het publiek zichtbaar is + aantallen tonen."""
    from ..models import Event, get_setting, Setting
    from ..types import TYPES, _CAT_NAAR_EV, type_code
    if request.method == "POST":
        # aangevinkt = zichtbaar; niet aangevinkt = verbergen
        zichtbaar = set(request.form.getlist("zichtbaar"))
        verborgen = [code for code in TYPES if code not in zichtbaar]
        row = Setting.query.filter_by(key="verborgen_types").first()
        if not row:
            row = Setting(key="verborgen_types")
            db.session.add(row)
        row.value = ",".join(verborgen)
        db.session.commit()
        audit(f"types-zichtbaarheid aangepast: {len(verborgen)} verborgen")
        flash("Zichtbaarheid per type opgeslagen.", "ok")
        return redirect(url_for("admin.types_beheer"))

    verborgen = set((get_setting("verborgen_types") or "").split(","))
    # Aantallen per type (subtype voor vaste plekken; categorie voor events).
    tellers = {code: 0 for code in TYPES}
    sub_counts = dict(db.session.query(Event.subtype, db.func.count(Event.id))
                      .group_by(Event.subtype).all())
    for st, n in sub_counts.items():
        if st in tellers:
            tellers[st] += n
    # events zonder subtype: tel per categorie-afgeleid ev-type (benadering)
    for cat, code in _CAT_NAAR_EV.items():
        n = (Event.query.filter(Event.subtype.is_(None))
             .filter(db.func.lower(db.cast(Event.categories, db.String)).like(f'%"{cat}"%'))
             .count())
        tellers[code] += n
    rijen = [{"code": c, "emoji": TYPES[c][0], "label": TYPES[c][1],
              "plaats": TYPES[c][2], "aantal": tellers[c],
              "zichtbaar": c not in verborgen} for c in TYPES]
    return render_template("admin/types.html", rijen=rijen, title="Activiteittypes",
                           family=None, active="types")


@bp.route("/activiteiten/<int:event_id>/waardig", methods=["POST"])
@medewerker_required
def activiteit_waardig(event_id):
    """Toggle 'Ravot-waardig' — de menselijke goedkeuring die de kern vormt."""
    from ..models import Event
    from datetime import datetime
    ev = db.session.get(Event, event_id) or abort(404)
    ev.curated = not ev.curated
    ev.curated_by = session.get("admin_id") if ev.curated else None
    ev.curated_at = datetime.utcnow() if ev.curated else None
    db.session.commit()
    audit(f"{'goedgekeurd' if ev.curated else 'goedkeuring ingetrokken'}: '{ev.title}'")
    flash("Als Ravot-waardig gemarkeerd. ✓" if ev.curated
          else "Goedkeuring ingetrokken.", "ok")
    terug = request.form.get("terug") or ""
    # Open-redirect-bescherming: enkel relatieve paden binnen de site.
    if not terug.startswith("/") or terug.startswith("//"):
        terug = url_for("admin.activiteit_bewerk", event_id=ev.id)
    return redirect(terug)


@bp.route("/feestjes")
@medewerker_required
def feestjes():
    """Overzicht van de feestjesmodule: aanvraagvolume per partner (hét
    verkoopargument voor het Partner-abonnement) + recente feestjes."""
    from ..models import Feestje, FeestjeAanvraag, Event
    recente = Feestje.query.order_by(Feestje.created_at.desc()).limit(50).all()
    per_partner = db.session.query(
        Event.id, Event.title, Event.gemeente,
        db.func.count(FeestjeAanvraag.id).label("n"),
        db.func.sum(db.case((FeestjeAanvraag.status == "bevestigd", 1),
                            else_=0)).label("bevestigd"),
    ).join(FeestjeAanvraag, FeestjeAanvraag.event_id == Event.id) \
     .group_by(Event.id, Event.title, Event.gemeente) \
     .order_by(db.desc("n")).limit(100).all()
    partners_zonder_contact = Event.query.filter(
        Event.feest.is_(True), Event.feest_contact.is_(None)).count()
    inst_groepen, inst_waarden, inst_defs = _instellingen_context("feestjes")
    return render_template("admin/feestjes.html",
                           groepen=inst_groepen, waarden=inst_waarden, defs=inst_defs, recente=recente,
                           per_partner=per_partner,
                           zonder_contact=partners_zonder_contact,
                           title="Feestjes", family=None, active="feestjes")


@bp.route("/horeca-import", methods=["GET", "POST"])
@medewerker_required
def horeca_import():
    """Horeca-verkenner: live alle horeca/bars rond een gemeente uit OSM,
    waarna de beheerder aanvinkt wat Ravot-waardig is (horeca of zomerbar).
    Curatie door een mens, zoekwerk door de machine."""
    from ..geo import zoek_centrum
    from ..models import HorecaKandidaat
    from ..services.sources import osm as osm_bron
    from ..services.sources import overture as ov_bron
    resultaten, zoekterm, straal, fout = None, "", 5, None
    bron = request.form.get("bron") or request.args.get("bron") or "overture"
    if bron not in ("overture", "osm"):
        bron = "overture"
    if request.method == "POST" and request.form.get("actie") == "importeer" \
            and not request.form.get("actie_gesloten"):
        keuzes = [(ext_id, request.form.get(f"soort_{ext_id}", "horeca"))
                  for ext_id in request.form.getlist("kies")]
        try:
            if bron == "overture":
                aantal = ov_bron.importeer(keuzes)
            else:
                aantal = osm_bron.importeer_horeca(keuzes)
            db.session.commit()
            audit(f"horeca-import ({bron}): {aantal} zaken")
            flash(f"{aantal} zaken geïmporteerd als gecureerde fiche.", "ok")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("horeca-import faalde")
            flash("Import mislukt — probeer opnieuw.", "error")
        return redirect(url_for("admin.horeca_import", bron=bron))
    if request.method == "POST":
        zoekterm = (request.form.get("plaats") or "").strip()
        try:
            straal = max(1, min(25, int(request.form.get("straal", 5))))
        except ValueError:
            straal = 5
        centrum = zoek_centrum(zoekterm) if zoekterm else None
        if not centrum:
            fout = "Gemeente niet gevonden — probeer een postcode."
        else:
            try:
                if request.form.get("actie_gesloten") and bron == "overture":
                    ext_id = request.form.get("actie_gesloten") or ""
                    if ov_bron.markeer_gesloten(ext_id):
                        db.session.commit()
                        audit(f"horeca-kandidaat gesloten gemarkeerd: {ext_id}")
                        flash("Gemarkeerd als gesloten — verdwijnt uit alle "
                              "lijsten (en de fiche is verborgen als ze al "
                              "bestond).", "ok")
                if request.form.get("actie") == "ai" and bron == "overture":
                    # AI-voorsortering draait op de ACHTERGROND: het model
                    # denkt minuten na en dat past niet in een webverzoek.
                    # De pagina toont de voortgang; herladen = bijwerken.
                    gestart = ov_bron.start_ai_triage_achtergrond(
                        current_app._get_current_object(),
                        centrum[0], centrum[1], straal)
                    flash("AI-voorsortering gestart op de achtergrond. Zoek "
                          "gerust opnieuw om de voortgang te zien — beoordeelde "
                          "zaken krijgen meteen hun badge." if gestart else
                          "Er loopt al een AI-beoordeling — even geduld.", "ok")
                if bron == "overture":
                    resultaten = ov_bron.zoek_kandidaten(centrum[0], centrum[1], straal)
                else:
                    resultaten = osm_bron.verken_horeca(centrum[0], centrum[1], straal)
                bestaand = {e.ext_id for e in Event.query
                            .filter(Event.subtype.in_(("horeca", "zomerbar"))).all()}
                for r in resultaten:
                    r["bestaat"] = r["ext_id"] in bestaand
            except Exception:
                db.session.rollback()   # anders sleept een SQL-fout de rest mee
                current_app.logger.exception("horeca-verkenner faalde")
                fout = ("De bron antwoordt momenteel niet. Probeer opnieuw, of "
                        "wissel van bron.")
    kandidaten_n = HorecaKandidaat.query.count()
    ai_klaar = ai_totaal = 0
    if resultaten is not None and bron == "overture":
        ai_totaal = len(resultaten)
        ai_klaar = sum(1 for r in resultaten if r.get("ai"))
    return render_template("admin/horeca_import.html", resultaten=resultaten,
                           ai_klaar=ai_klaar, ai_totaal=ai_totaal,
                           ai_status=ov_bron.triage_status(),
                           ai_bezig=ov_bron.triage_actief(),
                           zoekterm=zoekterm, straal=straal, fout=fout,
                           bron=bron, kandidaten_n=kandidaten_n,
                           title="Horeca-import", family=None,
                           active="horeca-import")


@bp.route("/beloningen", methods=["GET", "POST"])
@medewerker_required
def beloningen():
    """Catalogus van beloningen + opvolging van inwisselingen. De richtprijs
    (punten = euro x punt_waarde) wordt live meegerekend als hulp."""
    from ..models import (Beloning, Event, Inwissel, INWISSEL_STATUSSEN,
                          get_setting)
    if request.method == "POST" and request.form.get("actie") == "nieuw":
        try:
            eur = float((request.form.get("waarde") or "0").replace(",", "."))
            pt = int(request.form.get("punten") or 0)
        except ValueError:
            eur, pt = 0, 0
        naam = (request.form.get("naam") or "").strip()[:120]
        if naam and pt > 0:
            partner_id = request.form.get("partner_id")
            b = Beloning(
                emoji=(request.form.get("emoji") or "🎁").strip()[:8],
                naam=naam,
                beschrijving=(request.form.get("beschrijving") or "").strip()[:300] or None,
                soort="partner" if partner_id else "ravot",
                partner_event_id=int(partner_id) if partner_id and partner_id.isdigit() else None,
                punten=pt, waarde_eur=eur,
                voorraad=int(request.form["voorraad"]) if (request.form.get("voorraad") or "").isdigit() else None)
            db.session.add(b)
            db.session.commit()
            audit(f"beloning toegevoegd: {naam} ({pt} pt / €{eur})")
            flash("Beloning toegevoegd.", "ok")
        else:
            flash("Naam en punten zijn verplicht.", "error")
        return redirect(url_for("admin.beloningen"))
    if request.method == "POST" and request.form.get("actie") == "toggle":
        b = db.session.get(Beloning, int(request.form.get("bid", 0))) or abort(404)
        b.actief = not b.actief
        db.session.commit()
        return redirect(url_for("admin.beloningen"))
    if request.method == "POST" and request.form.get("actie") == "bewerk":
        b = db.session.get(Beloning, int(request.form.get("bid", 0))) or abort(404)
        try:
            b.waarde_eur = float((request.form.get("waarde") or "0").replace(",", "."))
            b.punten = max(1, int(request.form.get("punten") or b.punten))
        except ValueError:
            flash("Waarde of punten ongeldig.", "error")
            return redirect(url_for("admin.beloningen"))
        b.emoji = (request.form.get("emoji") or b.emoji).strip()[:8]
        b.naam = (request.form.get("naam") or b.naam).strip()[:120]
        b.beschrijving = (request.form.get("beschrijving") or "").strip()[:300] or None
        vr = (request.form.get("voorraad") or "").strip()
        b.voorraad = int(vr) if vr.isdigit() else None
        partner_id = request.form.get("partner_id") or ""
        b.partner_event_id = int(partner_id) if partner_id.isdigit() else None
        b.soort = "partner" if b.partner_event_id else "ravot"
        db.session.commit()
        audit(f"beloning #{b.id} bewerkt: {b.naam}")
        flash("Beloning bijgewerkt.", "ok")
        return redirect(url_for("admin.beloningen"))
    if request.method == "POST" and request.form.get("actie") == "verwijder":
        b = db.session.get(Beloning, int(request.form.get("bid", 0))) or abort(404)
        if Inwissel.query.filter_by(beloning_id=b.id).count():
            flash("Er bestaan al inwisselingen voor deze beloning — zet ze uit "
                  "in plaats van ze te wissen (zo blijft de historiek kloppen).",
                  "error")
            return redirect(url_for("admin.beloningen"))
        naam = b.naam
        db.session.delete(b)
        db.session.commit()
        audit(f"beloning verwijderd: {naam}")
        flash("Beloning verwijderd.", "ok")
        return redirect(url_for("admin.beloningen"))
    if request.method == "POST" and request.form.get("actie") == "status":
        i = db.session.get(Inwissel, int(request.form.get("iid", 0))) or abort(404)
        status = request.form.get("status")
        if status in INWISSEL_STATUSSEN:
            if status == "geannuleerd" and i.status != "geannuleerd" \
                    and i.beloning and i.beloning.voorraad is not None:
                i.beloning.voorraad += 1     # voorraad terug bij annulatie
            i.status = status
            db.session.commit()
            audit(f"inwissel #{i.id} -> {status}")
        return redirect(url_for("admin.beloningen"))
    try:
        punt_eur = float(get_setting("punt_waarde_eur") or 0.05)
    except ValueError:
        punt_eur = 0.05
    catalogus = Beloning.query.order_by(Beloning.actief.desc(), Beloning.punten).all()
    inwissels = Inwissel.query.order_by(Inwissel.created_at.desc()).limit(100).all()
    # Controle: opvallende puntenverdieners van de laatste 7 dagen. Farming
    # valt hier meteen op (veel punten, veel 'geweest' op korte tijd).
    from datetime import datetime, timedelta
    from ..models import RavotPunt
    week = datetime.utcnow() - timedelta(days=7)
    controle = db.session.query(
        RavotPunt.family_id,
        db.func.sum(RavotPunt.punten).label("pt"),
        db.func.sum(db.case((RavotPunt.reden == "geweest", 1), else_=0)).label("bezoeken"),
        db.func.count(RavotPunt.id).label("acties"),
    ).filter(RavotPunt.created_at >= week)      .group_by(RavotPunt.family_id)      .order_by(db.desc("pt")).limit(10).all()
    partners = Event.query.filter(Event.partner_until.isnot(None)) \
        .order_by(Event.title).limit(200).all()
    inst_groepen, inst_waarden, inst_defs = _instellingen_context("beloningen")
    return render_template("admin/beloningen.html", catalogus=catalogus,
                           groepen=inst_groepen, waarden=inst_waarden, defs=inst_defs,
                           inwissels=inwissels, controle=controle,
                           statussen=INWISSEL_STATUSSEN,
                           partners=partners, punt_eur=punt_eur,
                           title="Beloningen", family=None, active="beloningen")


@bp.route("/status")
@medewerker_required
def status():
    """Health-dashboard: live status van alle API's en koppelingen, plus de
    laatste run van elke databron. Herladen = opnieuw controleren."""
    from ..services.health import alle_checks
    checks, bronnen = alle_checks()
    problemen = sum(1 for c in checks if c["ok"] is False)
    return render_template("admin/status.html", checks=checks, bronnen=bronnen,
                           problemen=problemen, title="Status",
                           family=None, active="status")


@bp.route("/partner-log")
@medewerker_required
def partner_log():
    """Chronologisch logboek van partner-activiteit: claims, fichewijzigingen,
    nieuwe partnerzaken en betalingen. Bewust GEEN gezinsdata."""
    from ..models import OperatorClaim, EditProposal, PartnerPayment, Operator, Event
    gebeurtenissen = []
    # Claims (ingediend/goedgekeurd/ingetrokken)
    for c, op, ev in db.session.query(OperatorClaim, Operator, Event).join(
            Operator, OperatorClaim.operator_id == Operator.id).join(
            Event, OperatorClaim.event_id == Event.id).order_by(
            OperatorClaim.created_at.desc()).limit(80).all():
        status_tekst = {"pending": "ingediend", "approved": "goedgekeurd",
                        "rejected": "afgewezen/ingetrokken"}.get(c.status, c.status)
        gebeurtenissen.append({
            "wanneer": c.created_at, "type": "claim", "icoon": "🤝",
            "wie": op.email, "zaak": ev.title,
            "detail": f"claim {status_tekst}" + (" · domein-match" if c.domein_match else "")})
    # Fichewijzigingen
    for e, op, ev in db.session.query(EditProposal, Operator, Event).join(
            Operator, EditProposal.operator_id == Operator.id).join(
            Event, EditProposal.event_id == Event.id).order_by(
            EditProposal.created_at.desc()).limit(80).all():
        velden = ", ".join((e.changes or {}).keys()) if e.changes else "—"
        gebeurtenissen.append({
            "wanneer": e.created_at, "type": "wijziging", "icoon": "✏️",
            "wie": op.email, "zaak": ev.title,
            "detail": f"fichewijziging ({e.status}): {velden[:80]}"})
    # Nieuwe zaken door uitbaters (pending, source=user)
    for ev in Event.query.filter_by(source="user", is_kamp=False).order_by(
            Event.id.desc()).limit(40).all():
        if ev.updated_at:
            gebeurtenissen.append({
                "wanneer": ev.updated_at, "type": "nieuwe_zaak", "icoon": "🏠",
                "wie": "—", "zaak": ev.title,
                "detail": "zaak toegevoegd" + (" (in wachtrij)" if ev.pending else "")})
    # Betalingen
    for p, op, ev in db.session.query(PartnerPayment, Operator, Event).join(
            Operator, PartnerPayment.operator_id == Operator.id).join(
            Event, PartnerPayment.event_id == Event.id).order_by(
            PartnerPayment.created_at.desc()).limit(60).all():
        gebeurtenissen.append({
            "wanneer": p.created_at, "type": "betaling", "icoon": "💶",
            "wie": op.email, "zaak": ev.title,
            "detail": f"partner-betaling {p.plan} ({p.status})"})
    gebeurtenissen.sort(key=lambda g: g["wanneer"] or datetime.min, reverse=True)
    return render_template("admin/partner_log.html",
                           gebeurtenissen=gebeurtenissen[:150],
                           title="Partnerlog", family=None, active="partner-log")


@bp.route("/verbindingen/wis-bron", methods=["POST"])
@medewerker_required
def bron_data_wissen():
    """Wis alle data van één bron (testdata-opkuis). Veiligheidskleppen:
    fiches met een partner, claim of betaling blijven ALTIJD staan, net als
    handmatig gecureerde fiches als de beheerder dat aanvinkt."""
    from ..models import (EditProposal, Photo, Review, SavedEvent)
    bron = (request.form.get("bron") or "").strip()
    bekend = {r[0] for r in db.session.query(Event.source).distinct().all() if r[0]}
    if bron not in bekend:
        flash("Onbekende bron.", "error")
        return redirect(url_for("admin.verbindingen"))
    behoud_gecureerd = request.form.get("behoud_gecureerd") == "1"
    q = Event.query.filter(Event.source == bron)
    if behoud_gecureerd:
        q = q.filter(Event.curated.is_(False))
    # nooit wissen: partnerzaken, geclaimde of betaalde fiches
    from ..models import OperatorClaim, PartnerPayment
    beschermd_ids = {r[0] for r in db.session.query(OperatorClaim.event_id).all()} \
        | {r[0] for r in db.session.query(PartnerPayment.event_id).all()}
    doel = [e for e in q.all()
            if e.id not in beschermd_ids and e.partner_until is None]
    ids = [e.id for e in doel]
    if not ids:
        flash("Niets te wissen voor deze bron (of alles is beschermd).", "ok")
        return redirect(url_for("admin.verbindingen"))
    for model, kolom in ((SavedEvent, SavedEvent.event_id),
                         (Review, Review.event_id),
                         (Photo, Photo.event_id),
                         (EditProposal, EditProposal.event_id)):
        db.session.query(model).filter(kolom.in_(ids)) \
            .delete(synchronize_session=False)
    n = Event.query.filter(Event.id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()
    audit(f"bron '{bron}' gewist: {n} fiches (+ gekoppelde data)")
    flash(f"{n} fiches van bron '{bron}' gewist.", "ok")
    return redirect(url_for("admin.verbindingen"))


@bp.route("/horeca-import/ai-voortgang")
@medewerker_required
def horeca_ai_voortgang():
    """Klein JSON-endpoint voor de live voortgangsbalk van de AI-triage."""
    from ..geo import zoek_centrum
    from ..services.sources import overture as ov_bron
    plaats = (request.args.get("plaats") or "").strip()
    try:
        straal = max(1, min(25, int(request.args.get("straal", 5))))
    except ValueError:
        straal = 5
    centrum = zoek_centrum(plaats) if plaats else None
    klaar = totaal = 0
    if centrum:
        ks = ov_bron.kandidaten_in_gebied(centrum[0], centrum[1], straal)
        totaal = len(ks)
        klaar = sum(1 for k in ks if k.ai_advies)
    st = ov_bron.triage_status()
    return {"bezig": st.get("actief", False), "fout": st.get("fout"),
            "klaar": klaar, "totaal": totaal}


@bp.route("/activiteiten/alles-nagekeken", methods=["POST"])
@medewerker_required
def activiteiten_alles_nagekeken():
    """Bulk: de hele huidige werkvoorraad afvinken. Voor wie de initiële
    machine-import vertrouwt en de curatie aan meldingen/reviews overlaat."""
    n = Event.query.filter(Event.curated.is_(True),
                           Event.nagekeken.is_(False)) \
        .update({"nagekeken": True}, synchronize_session=False)
    db.session.commit()
    audit(f"werkvoorraad in bulk afgevinkt: {n} fiches")
    flash(f"{n} fiches gemarkeerd als nagekeken.", "ok")
    return redirect(url_for("admin.activiteiten"))


@bp.route("/activiteiten/<int:event_id>/nagekeken", methods=["POST"])
@medewerker_required
def activiteit_nagekeken(event_id):
    """Vinkje in de werkvoorraad: deze fiche is met eigen ogen bekeken."""
    ev = db.session.get(Event, event_id) or abort(404)
    ev.nagekeken = not ev.nagekeken
    db.session.commit()
    audit(f"fiche #{event_id} nagekeken={'ja' if ev.nagekeken else 'nee'}")
    return redirect(request.referrer or url_for("admin.activiteiten"))


@bp.route("/activiteiten/bulk-nagekeken", methods=["POST"])
@medewerker_required
def activiteiten_bulk_nagekeken():
    """Bulk-curatie: markeer een hele selectie (bron/type/status) in één keer
    als nagekeken. Voor de initiële vulling: publieke infrastructuur en
    AI-gecureerde zaken hoeven niet stuk voor stuk je blik."""
    from ..models import Event, get_int, EnrichProposal
    bron = request.form.get("bron", "")
    soort = request.form.get("soort", "")
    q = Event.query.filter(Event.curated.is_(True), Event.nagekeken.is_(False),
                           Event.hidden.is_(False))
    if bron:
        q = q.filter(Event.source == bron)
    if soort == "_plekken":
        q = q.filter(Event.is_permanent.is_(True))
    elif soort == "_events":
        q = q.filter(Event.is_permanent.is_(False))
    elif soort == "_horeca":
        q = q.filter(Event.subtype == "horeca")
    elif soort == "_speeltuin":
        q = q.filter(Event.subtype.in_(("playground", "park")))
    elif soort:
        q = q.filter(Event.subtype == soort)
    n = q.update({"nagekeken": True}, synchronize_session=False)
    db.session.commit()
    audit(f"bulk nagekeken: {n} fiches (bron={bron or 'alle'}, soort={soort or 'alle'})")
    flash(f"{n} fiches in één keer als nagekeken gemarkeerd.", "ok")
    return redirect(request.referrer or url_for("admin.activiteiten"))



@bp.route("/horeca-import/export.csv")
@medewerker_required
def horeca_export_csv():
    """Exporteer geïmporteerde horeca-zaken met hun contactgegevens als CSV —
    voor een gerichte mailactie om het partnermodel voor te stellen. Enkel
    zaken die al als fiche op Ravot staan (dus door de triage/curatie geraakt),
    met de contactdata uit Overture ernaast."""
    import csv
    import io
    from ..models import Event, HorecaKandidaat, OperatorClaim
    # zaken die live staan als horeca-fiche
    fiches = Event.query.filter(Event.source == "overture",
                                Event.subtype == "horeca",
                                Event.hidden.is_(False)).all()
    ext_ids = [e.ext_id for e in fiches if e.ext_id]
    kand = {k.ext_id: k for k in HorecaKandidaat.query.filter(
        HorecaKandidaat.ext_id.in_(ext_ids)).all()} if ext_ids else {}
    geclaimd = {c.event_id for c in OperatorClaim.query.filter_by(
        status="approved").all()}
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["naam", "gemeente", "postcode", "adres", "website",
                "telefoon", "email", "al_partner", "ravot_fiche"])
    for e in fiches:
        k = kand.get(e.ext_id)
        w.writerow([
            e.title, e.gemeente or "", e.postcode or "",
            (k.adres if k else "") or "",
            (k.website if k else "") or e.source_url or "",
            (k.telefoon if k else "") or e.telefoon or "",
            (k.email if k else "") or "",
            "ja" if e.id in geclaimd else "nee",
            url_for("public.event", slug=e.slug, _external=True),
        ])
    audit(f"horeca-export gedownload: {len(fiches)} zaken")
    from flask import Response
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition":
                             "attachment; filename=ravot-horeca-partners.csv"})


@bp.route("/feestprospecten")
@medewerker_required
def feestprospecten():
    """Feestpartners: zaken die zelf aangaven feestjes te organiseren
    (uitbater vinkte 'wij doen feestjes' aan). Dit vult zich organisch — niet
    uit Overture, want feest-leveranciers zitten daar buiten de horeca-tak."""
    from ..models import Event, OperatorClaim
    zoek = (request.args.get("q") or "").strip()
    q = Event.query.filter(Event.feest.is_(True), Event.hidden.is_(False))
    if zoek:
        like = f"%{zoek.lower()}%"
        q = q.filter(db.or_(db.func.lower(Event.title).like(like),
                            db.func.lower(Event.gemeente).like(like)))
    rijen = q.order_by(Event.gemeente, Event.title).limit(500).all()
    totaal = Event.query.filter(Event.feest.is_(True)).count()
    return render_template("admin/feestprospecten.html", rijen=rijen, zoek=zoek,
                           totaal=totaal, title="Feestpartners",
                           family=None, active="feestprospecten")


@bp.route("/feestprospecten/export.csv")
@medewerker_required
def feestprospecten_export():
    import csv
    import io
    from ..models import Event
    from ..services.feestjes import contact_email
    rijen = Event.query.filter(Event.feest.is_(True), Event.hidden.is_(False)) \
        .order_by(Event.gemeente, Event.title).all()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["naam", "gemeente", "postcode", "contact_email",
                "telefoon", "website", "partner"])
    for e in rijen:
        w.writerow([e.title, e.gemeente or "", e.postcode or "",
                    contact_email(e) or "", e.telefoon or "",
                    e.source_url or "", "ja" if e.partner_until else "nee"])
    audit(f"feestpartner-export: {len(rijen)} zaken")
    from flask import Response
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition":
                             "attachment; filename=ravot-feestpartners.csv"})
