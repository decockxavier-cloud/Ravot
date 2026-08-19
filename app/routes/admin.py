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

    # Crowdsourcing-pols (patch 210): hoeveel vullen gezinnen effectief aan?
    # Bewust geen detaillijst — enkel de beweging: hoeveel antwoorden, op
    # hoeveel fiches, en welke velden het meest.
    from ..models import Photo as _Photo, VeldStem
    dag_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    zeven = now - timedelta(days=7)
    # Gemeentestemmen (patch 268) horen niet in deze gezinspols — die hebben
    # hun eigen blok hieronder.
    _geen_bron = db.and_(VeldStem.stemmer != "bron",
                         ~VeldStem.stemmer.like("gemeente:%"))
    aanvul = {
        "vandaag": VeldStem.query.filter(VeldStem.created_at >= dag_start,
                                         _geen_bron).count(),
        "week": VeldStem.query.filter(VeldStem.created_at >= zeven,
                                      _geen_bron).count(),
        "fiches_week": db.session.query(VeldStem.event_id)
                       .filter(VeldStem.created_at >= zeven, _geen_bron)
                       .distinct().count(),
        "stemmers_week": db.session.query(VeldStem.stemmer)
                         .filter(VeldStem.created_at >= zeven, _geen_bron)
                         .distinct().count(),
    }
    # Opsplitsing gezin versus anoniem (patch 231): anonieme stemmers hebben
    # het voorvoegsel "anon:". Dit vertelt of het anoniem stemmen (p182) echt
    # bijdraagt, of dat vooral ingelogde gezinnen het werk doen.
    def _tel(vanaf, alleen=None):
        q = (db.session.query(db.func.count(VeldStem.id))
             .filter(VeldStem.created_at >= vanaf, _geen_bron))
        if alleen == "anon":
            q = q.filter(VeldStem.stemmer.like("anon:%"))
        elif alleen == "gezin":
            q = q.filter(~VeldStem.stemmer.like("anon:%"))
        return int(q.scalar() or 0)

    aanvul["gezin_week"] = _tel(zeven, "gezin")
    aanvul["anon_week"] = _tel(zeven, "anon")
    aanvul["gezin_vandaag"] = _tel(dag_start, "gezin")
    aanvul["anon_vandaag"] = _tel(dag_start, "anon")
    aanvul_velden = (db.session.query(VeldStem.veld,
                                      db.func.count(VeldStem.id).label("n"))
                     .filter(VeldStem.created_at >= zeven, _geen_bron)
                     .group_by(VeldStem.veld)
                     .order_by(db.text("n DESC")).limit(6).all())
    aanvul_fotos = (_Photo.query.filter(_Photo.created_at >= zeven).count()
                    if hasattr(_Photo, "created_at") else 0)

    # Gemeenten werken mee (patch 268): hoeveel diensten kregen een link, wie
    # levert effectief, en wát — veldantwoorden, foto's, evenementen, teksten.
    from ..models import GemeenteContact, GemeenteTekst
    _contacten = GemeenteContact.query.all()
    _stem_per = {}
    for stemmer, n in (db.session.query(VeldStem.stemmer,
                                        db.func.count(VeldStem.id))
                       .filter(VeldStem.stemmer.like("gemeente:%"))
                       .group_by(VeldStem.stemmer).all()):
        _stem_per[stemmer[len("gemeente:"):]] = int(n)
    _foto_per = {(g or "").lower(): int(n) for g, n in (
        db.session.query(Event.gemeente, db.func.count(_Photo.id))
        .join(Event, _Photo.event_id == Event.id)
        .filter(_Photo.soort == "gemeente")
        .group_by(Event.gemeente).all())}
    _event_per = {(g or "").lower(): int(n) for g, n in (
        db.session.query(Event.gemeente, db.func.count(Event.id))
        .filter(Event.source == "gemeente")
        .group_by(Event.gemeente).all())}
    _tekst_van = {t.gemeente for t in GemeenteTekst.query.filter_by(
        van_gemeente=True).all() if t.heeft_tekst or t.pending}
    gem_rijen = []
    for c in _contacten:
        g = c.gemeente
        rij = {"gemeente": g, "stemmen": _stem_per.get(g, 0),
               "fotos": _foto_per.get(g, 0), "events": _event_per.get(g, 0),
               "tekst": g in _tekst_van, "laatst": c.laatst_verrijkt,
               "verstuurd": c.laatst_verstuurd, "token": bool(c.token)}
        rij["totaal"] = (rij["stemmen"] + rij["fotos"] + rij["events"]
                         + (1 if rij["tekst"] else 0))
        gem_rijen.append(rij)
    gem_rijen.sort(key=lambda r: (-r["totaal"], r["gemeente"]))
    gemeenten_mee = {
        "links": sum(1 for r in gem_rijen if r["token"]),
        "actief": sum(1 for r in gem_rijen if r["totaal"] > 0),
        "stemmen": sum(r["stemmen"] for r in gem_rijen),
        "fotos": sum(r["fotos"] for r in gem_rijen),
        "events": sum(r["events"] for r in gem_rijen),
        "teksten": sum(1 for r in gem_rijen if r["tekst"]),
    }
    gem_rijen = gem_rijen[:10]

    # Conversietrechter (patch 223): waar haken bezoekers af op weg naar
    # een profiel? Cijfers over 14 dagen, zodat één rustige dag niets zegt.
    from ..trechter import (cijfers as trechter_cijfers,
                            methode_cijfers)
    trechter = trechter_cijfers(14)
    methodes = methode_cijfers(14)

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

    return render_template("admin/dashboard.html", kwaliteit=kwaliteit,
                           trechter=trechter, methodes=methodes,
                           aanvul=aanvul, aanvul_velden=aanvul_velden,
                           aanvul_fotos=aanvul_fotos, stats=stats,
                           gemeenten_mee=gemeenten_mee, gem_rijen=gem_rijen,
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
        ("Sociale media", ["social_facebook", "social_instagram", "social_tiktok"]),
        ("Fietsroutes", ["netwerk_bron_url", "generator_min_km", "generator_max_km",
                         "route_buurt_meter", "route_partner_meter",
                         "route_tempo_kmu", "routes_in_menu"]),
        ("Weergave & gedrag", ["default_radius", "toon_maanden_vooruit",
                               "ontdek_per_pagina", "onderhoud_aan", "uit_zichtbaar"]),
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
        ("Formules & prijzen", ["partner_prijs_jaar", "feest_prijs_jaar",
                                "combi_prijs_jaar", "partner_btw_pct",
                                "mollie_testmodus", "founding_aan", "founding_max"]),
        ("Exclusiviteit per gemeente", ["cap_zichtbaar_gemeente",
                                        "cap_feest_gemeente"]),
        ("Facturatie (Odoo)", ["odoo_product_id", "odoo_journal_id", "odoo_factuur_auto"]),
        ("Uitbaters", ["uitbater_auto_ok"]),
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
        ("Niveaus", ["niveau_drempels"]),
        ("Bijdragen zonder account", ["anoniem_stemmen_aan"]),
        ("Puntwaarden per bijdrage", [
            "punt_review", "punt_eerste_score", "punt_foto", "punt_eerste_foto",
            "punt_geweest", "punt_daguitstap", "punt_feestje", "punt_plek",
            "punt_veld_stem", "punt_bingo", "veldstem_dag_max"]),
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
    from ..models import wis_settings_cache
    wis_settings_cache()   # request-cache verversen na wijziging
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
    from ..models import PartnerPayment
    groepen, waarden, defs = _instellingen_context("facturatie")
    # Wettelijk verplicht: elke betaalde Partner-betaling hoort een factuur te
    # krijgen. De Odoo-koppeling "faalt stil" (activatie mag nooit sneuvelen op
    # een boekhoudfout) — dus maken we ontbrekende facturen hier zichtbaar.
    rijen = (PartnerPayment.query
             .filter(PartnerPayment.status == "paid",
                     PartnerPayment.odoo_invoice_id.is_(None))
             .order_by(PartnerPayment.paid_at.desc()).all())
    # gratis/handmatige partners (bedrag 0) hebben geen factuur nodig.
    # LET OP: amount is een tekstkolom ("19.00") — daarom hier in Python
    # filteren; een SQL-vergelijking met 0 crasht op Postgres.
    zonder_factuur = [b for b in rijen if float(b.amount or 0) > 0]
    return render_template("admin/facturatie.html", defs=defs,
                           waarden=waarden, groepen=groepen,
                           zonder_factuur=zonder_factuur,
                           title="Facturatie", family=None, active="facturatie")


@bp.route("/facturatie/factureer/<int:pid>", methods=["POST"])
@admin_required
def facturatie_opnieuw(pid):
    """Handmatig opnieuw proberen een Odoo-factuur te maken voor een betaling."""
    from ..models import PartnerPayment
    from ..odoo import factureer_betaling
    p = db.session.get(PartnerPayment, pid) or abort(404)
    if p.status != "paid":
        flash("Deze betaling staat niet op 'paid' — geen factuur nodig.", "error")
    elif p.odoo_invoice_id:
        flash("Deze betaling heeft al een factuur.", "ok")
    elif factureer_betaling(p):
        audit(f"Odoo-factuur handmatig aangemaakt voor betaling {p.id}")
        flash(f"Factuur aangemaakt in Odoo ({p.odoo_invoice_ref or p.odoo_invoice_id}).", "ok")
    else:
        flash("Factureren mislukte opnieuw — controleer de Odoo-koppeling op de "
              "Status-pagina en de logs.", "error")
    return redirect(url_for("admin.facturatie"))


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
                           title="Verbindingen", family=None, active="verbindingen")


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


@bp.route("/herkomst")
@medewerker_required
def herkomst():
    """Waar komen bezoekers vandaan? Kanalen, bronnen, campagnes, trends."""
    from ..herkomst import rapport
    dagen = request.args.get("dagen", type=int) or 30
    dagen = dagen if dagen in (7, 30, 90, 365) else 30
    r = rapport(dagen)
    return render_template("admin/herkomst.html", r=r, dagen=dagen,
                           title="Herkomst", family=None, active="herkomst")


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
                           title="Gezinnen", family=None, active="families")


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
                # ref_id moet uniek zijn per (gezin, reden): twee correcties
                # binnen dezelfde seconde botsten vroeger op de unieke index,
                # dus tellen we gewoon door vanaf de hoogste bestaande waarde.
                vorige = (db.session.query(db.func.coalesce(
                    db.func.max(RavotPunt.ref_id), 0))
                    .filter(RavotPunt.family_id == fid,
                            RavotPunt.reden == "admin").scalar() or 0)
                db.session.add(RavotPunt(family_id=fid, reden="admin",
                                         ref_id=int(vorige) + 1, punten=aantal,
                                         notitie=reden))
                db.session.commit()
                if request.form.get("niveau_mee") == "1" and aantal < 0:
                    from .. import punten as _pas
                    _pas.zet_niveau_terug(fid)
                    audit(f"gezin {fid}: niveau meeverlaagd ({reden})")
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
    from ..models import (Event, Inwissel, INWISSEL_STATUSSEN, PUNT_REDENEN,
                          RavotPunt)
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
        .order_by(RavotPunt.created_at.desc()).limit(200).all()
    # Waar kwam elk punt vandaan? De ref_id verwijst naar een fiche, daguitstap
    # of feestje; voor fiches tonen we de naam zodat een vraag van een gezin
    # meteen te beantwoorden is.
    _fiche_redenen = ("review", "foto", "eerste_foto", "geweest", "plek",
                      "veld_stem", "eerste_score")
    _ids = {p.ref_id for p in puntlog if p.reden in _fiche_redenen and p.ref_id}
    _namen = {e.id: (e.title, e.slug)
              for e in Event.query.filter(Event.id.in_(_ids)).all()} if _ids else {}
    punt_bron = {}
    for p in puntlog:
        if p.reden in _fiche_redenen and p.ref_id in _namen:
            punt_bron[p.id] = _namen[p.ref_id]
    inwissels = Inwissel.query.filter_by(family_id=fid) \
        .order_by(Inwissel.created_at.desc()).all()
    pas_totaal = pas.totaal(fid)
    return render_template("admin/family_detail.html", fam=fam,
                           punt_bron=punt_bron, PUNT_REDENEN=PUNT_REDENEN,
                           aantal_reviews=aantal_reviews, aantal_bewaard=aantal_bewaard,
                           bewaard=bewaard, scores=scores, puntlog=puntlog,
                           inwissels=inwissels, statussen=INWISSEL_STATUSSEN,
                           pas_totaal=pas_totaal, pas_saldo=pas.saldo(fid),
                           pas_niveau=pas.niveau(pas.niveau_punten(fid)),
                           title=f"Gezin {fam.email}", family=None, active="families")


@bp.route("/paginas", methods=["GET"])
@medewerker_required
def paginas():
    from ..models import ContentPage, CONTENT_PAGES
    pages = []
    for slug, titel in CONTENT_PAGES.items():
        cp = db.session.get(ContentPage, slug)
        pages.append({"slug": slug, "titel": titel, "bewerkt": cp.updated_at if cp else None})
    return render_template("admin/paginas.html", pages=pages,
                           title="Inhoudspagina's", family=None, active="paginas")


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
                           family=None, active="paginas")


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
                           title="Mailteksten", family=None, active="mails")


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
                           title=f"Bewerk mail: {naam}", family=None, active="mails")


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
        # Een 'zaak'-foto (sfeerbeeld van de uitbater) wordt áltijd het
        # hoofdbeeld — dat is precies waarvoor hij bedoeld is. Andere soorten
        # enkel als er nog geen hoofdbeeld was.
        if p.event and (p.soort == "zaak" or not p.event.image_url):
            eerste = not p.event.image_url
            p.event.image_url = _url("public.foto", pid=p.id)
            if p.family_id and eerste:
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
                      if b.status == "paid" and not b.odoo_invoice_ref
                      and float(b.amount or 0) > 0]
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
    het lidmaatschap traceerbaar is en netjes verloopt.

    Werkt met een expliciet event_id (knop op de fiche) of met een slug/id die
    je intikt op de partnerspagina."""
    from datetime import datetime, timedelta
    from ..models import PartnerPayment, OperatorClaim
    sleutel = (request.form.get("event") or request.form.get("event_id") or "").strip()
    try:
        maanden = max(1, min(60, int(request.form.get("maanden") or 12)))
    except (ValueError, TypeError):
        maanden = 12
    ev = None
    if sleutel.isdigit():
        ev = db.session.get(Event, int(sleutel))
    if not ev and sleutel:
        ev = Event.query.filter_by(slug=sleutel).first()
    if not ev:
        flash("Geen zaak gevonden met dat id of die slug.", "error")
        return redirect(request.referrer or url_for("admin.partners"))
    # Als de zaak geclaimd is, koppelen we de operator aan de registratie; zo
    # niet, dan blijft operator_id leeg (gratis toekenning zonder betaler).
    claim = OperatorClaim.query.filter_by(event_id=ev.id, status="approved").first()
    op_id = claim.operator_id if claim else None
    try:
        basis = ev.partner_until if (ev.partner_until and ev.partner_until > datetime.utcnow()) \
            else datetime.utcnow()
        ev.partner_until = basis + timedelta(days=round(maanden * 30.4))
        db.session.add(PartnerPayment(operator_id=op_id, event_id=ev.id,
                                      plan="handmatig", amount="0.00", status="paid",
                                      paid_at=datetime.utcnow()))
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("handmatig partner maken faalde")
        flash("Partner maken mislukte door een technische fout. Controleer de "
              "serverlogs (docker compose logs web) voor details.", "error")
        return redirect(request.referrer or url_for("admin.partners"))
    audit(f"handmatig partner gemaakt: {ev.title} (+{maanden} mnd, gratis)")
    flash(f"✅ {ev.title} is nu Partner tot {ev.partner_until.strftime('%d/%m/%Y')} "
          "(handmatig, gratis).", "ok")
    return redirect(request.referrer or url_for("admin.partners"))


@bp.route("/partners/intrekken", methods=["POST"])
@admin_required
def partner_intrekken():
    """Trek het partnerschap van een zaak in — bv. een gratis toekenning
    terugnemen, of intrekken wanneer een zaak niet aan de Ravot-voorwaarden
    voldoet. Zet partner_until op verleden zodat de zaak meteen geen partner
    meer is; de betaalgeschiedenis blijft bewaard voor de boekhouding."""
    from datetime import datetime
    sleutel = (request.form.get("event") or request.form.get("event_id") or "").strip()
    ev = None
    if sleutel.isdigit():
        ev = db.session.get(Event, int(sleutel))
    if not ev and sleutel:
        ev = Event.query.filter_by(slug=sleutel).first()
    if not ev:
        flash("Geen zaak gevonden met dat id of die slug.", "error")
        return redirect(request.referrer or url_for("admin.partners"))
    if not ev.partner_until:
        flash("Deze zaak is geen Partner.", "error")
        return redirect(request.referrer or url_for("admin.partners"))
    ev.partner_until = None
    db.session.commit()
    audit(f"partnerschap ingetrokken: {ev.title}")
    flash(f"Partnerschap van {ev.title} is ingetrokken.", "ok")
    return redirect(request.referrer or url_for("admin.partners"))


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
                      "buggy_ok", "feest", "feest_contact",
                      # patch 150: pariteit met de uitbater-fiche — het beheer
                      # hoort mínstens te kunnen wat een uitbater kan.
                      "subtype", "reservatie_url", "kinderstoel", "speelhoek",
                      "kindermenu", "terras", "overdekt_terras", "parking",
                      "toegankelijk", "allergievriendelijk", "babyvoeding",
                      "hidden", "deelgemeente",
                      "huisdieren", "feest_min_pers", "feest_max_pers")


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
    geldig = lambda t: bool(_re.match(r"^\d{1,2}:\d{2}$", t))
    for d in DAGEN:
        if f.get(f"dicht_{d}"):
            uren[d] = None            # expliciet gesloten
            continue
        blokken = []
        for nr in ("", "2"):          # hoofdblok + optioneel tweede blok (pauze)
            o = (f.get(f"open{nr}_{d}") or "").strip()
            s = (f.get(f"sluit{nr}_{d}") or "").strip()
            if geldig(o) and geldig(s):
                blokken.append([o, s])
        if blokken:
            uren[d] = blokken
    if not uren:
        return None
    uren["_handmatig"] = True   # sync mag deze uren nooit meer overschrijven
    return uren


@bp.route("/activiteiten/<int:event_id>", methods=["GET", "POST"])
@medewerker_required
def activiteit_bewerk(event_id):
    from ..models import Event, CATEGORIES
    ev = db.session.get(Event, event_id) or abort(404)
    from ..types import TYPES as _TYPES
    from ..services.openingsuren import DAGEN as _DAGEN, DAG_LABELS as _DLBL, dag_tekst as _dtxt
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
                                   feest_soorten=_FS, soorten=_TYPES,
                                   dagen=_DAGEN, dag_labels=_DLBL,
                                   uren_nu={d: _dtxt((ev.openingsuren or {}).get(d)) for d in _DAGEN},
                                   title=f"Bewerk: {ev.title}", family=None,
                                   active="activiteiten")
        # --- Opslaan ---
        import re as _re
        for veld in ADMIN_EVENT_VELDEN:
            if veld not in f:
                continue
            waarde = (f.get(veld) or "").strip()
            if veld in ("indoor", "is_free", "feest", "hidden"):
                setattr(ev, veld, f.get(veld) == "1")
            elif veld in ("omheind", "verzorgingstafel", "buggy_ok",
                          "kinderstoel", "speelhoek", "kindermenu", "terras",
                          "overdekt_terras", "parking", "toegankelijk",
                          "allergievriendelijk", "babyvoeding", "huisdieren"):
                # tri-state: '' = onbekend (None), '1' = ja, '0' = nee
                setattr(ev, veld, None if waarde == "" else waarde == "1")
            elif veld in ("feest_min_pers", "feest_max_pers"):
                setattr(ev, veld, int(waarde) if waarde.isdigit() else None)
            elif veld in ("age_min", "age_max"):
                if waarde.isdigit():
                    # zelfde grenzen als overal (0-99, patch 190/202)
                    setattr(ev, veld, max(0, min(99, int(waarde))))
            elif veld == "postcode":
                ev.postcode = _re.sub(r"\D", "", waarde)[:8] or None
            else:
                setattr(ev, veld, waarde or None)
        # Ligging rechtstreeks aanpasbaar (patch 150): tot nu kon de beheerder
        # een speld-loze zaak niet zelf op de kaart zetten.
        try:
            if (f.get("lat") or "").strip():
                ev.lat = float(f["lat"]); ev.lng = float(f.get("lng") or ev.lng)
        except (TypeError, ValueError):
            flash("Coördinaten niet begrepen — gebruik punten (51.05).", "error")
        # Speld zonder gemeente? Afleiden (patch 220) — anders is de fiche
        # onvindbaar bij het zoeken op stad.
        if ev.lat is not None and (not ev.gemeente or not ev.postcode):
            from ..geo import gemeente_uit_punt
            g2, p2 = gemeente_uit_punt(ev.lat, ev.lng)
            ev.gemeente = ev.gemeente or g2
            ev.postcode = ev.postcode or p2
        # Openingsuren, zelfde formaat als op de uitbater-fiche.
        from ..services.openingsuren import DAGEN as _DGN, parse_dagtekst
        if any(f"uren_{d}" in f for d in _DGN):
            uren = {}
            for d in _DGN:
                w, _ok = parse_dagtekst(f.get(f"uren_{d}"))
                uren[d] = w
            ev.openingsuren = uren if any(v for v in uren.values()) else None
        from ..models import FEEST_SOORTEN
        if "feest" in f:
            ev.feest_soorten = [s for s in f.getlist("feest_soorten")
                                if s in FEEST_SOORTEN]
        cat = (f.get("categorie") or "").strip()
        if cat:
            ev.categories = [cat]
        # Openingsuren via het OUDE open_/sluit_-formaat: enkel toepassen als
        # die velden meekomen — anders wist dit de zonet geparste uren_-waarden.
        if any(k.startswith(("open_", "sluit_")) for k in f.keys()):
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
                           categories=CATEGORIES, soorten=_TYPES,
                           dagen=_DAGEN, dag_labels=_DLBL,
                           uren_nu={d: _dtxt((ev.openingsuren or {}).get(d)) for d in _DAGEN}, voorstel=voorstel,
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


def _bon_logo_opslaan(beloning):
    """Geüpload webshoplogo bewaren (patch 175): PNG met transparantie,
    geschaald naar max 240x120 zodat het netjes in de mail past."""
    bestand = request.files.get("bon_logo_bestand")
    if not bestand or not bestand.filename:
        return
    from ..media import BON_LOGO_MAP
    import io
    import os
    try:
        from PIL import Image
        beeld = Image.open(io.BytesIO(bestand.read()))
        beeld = beeld.convert("RGBA")
        beeld.thumbnail((240, 120), Image.LANCZOS)
    except Exception:
        flash("Dat lijkt geen geldig logo (png/jpg/webp).", "error")
        return
    os.makedirs(BON_LOGO_MAP, exist_ok=True)
    beeld.save(f"{BON_LOGO_MAP}/{beloning.id}.png", optimize=True)
    beloning.bon_logo = "upload"     # markeert: gebruik het geüploade bestand


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
            b.is_bon = request.form.get("is_bon") == "1"
            b.bon_winkel = (request.form.get("bon_winkel") or "").strip()[:80] or None
            bu = (request.form.get("bon_url") or "").strip()[:200]
            if bu and not bu.startswith(("http://", "https://")):
                bu = "https://" + bu
            b.bon_url = bu or None
            b.bon_logo = (request.form.get("bon_logo") or "").strip()[:120] or None
            b.bon_mail = (request.form.get("bon_mail") or "").strip()[:255] or None
            db.session.commit()
            _bon_logo_opslaan(b)
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
        b.is_bon = request.form.get("is_bon") == "1"
        b.bon_winkel = (request.form.get("bon_winkel") or "").strip()[:80] or None
        bu = (request.form.get("bon_url") or "").strip()[:200]
        if bu and not bu.startswith(("http://", "https://")):
            bu = "https://" + bu
        b.bon_url = bu or None
        b.bon_logo = (request.form.get("bon_logo") or "").strip()[:120] or None
        b.bon_mail = (request.form.get("bon_mail") or "").strip()[:255] or None
        _bon_logo_opslaan(b)
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
    # Bonnencodes zijn gevoelige waarde-informatie: elke inzage komt in het
    # logboek, zodat achteraf te zien is wie ze bekeken heeft.
    if any(i.beloning and i.beloning.is_bon for i in inwissels):
        audit("bonnencodes ingezien op /beheer/beloningen")
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
                           nu=datetime.utcnow(),
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


# ---------------------------------------------------------------------------
# Artikels (blog) — patch 134
# ---------------------------------------------------------------------------

def _artikel_slug(titel, artikel_id=None):
    """Nette, unieke slug uit de titel (kleine letters, koppeltekens)."""
    import re as _re
    from ..models import Artikel
    basis = _re.sub(r"[^a-z0-9]+", "-", (titel or "artikel").lower()).strip("-")[:140] or "artikel"
    slug, n = basis, 2
    while True:
        bestaand = Artikel.query.filter_by(slug=slug).first()
        if not bestaand or bestaand.id == artikel_id:
            return slug
        slug, n = f"{basis}-{n}", n + 1


@bp.route("/artikels")
@medewerker_required
def artikels():
    from ..models import Artikel
    rijen = Artikel.query.order_by(
        Artikel.gepubliceerd.asc(), Artikel.updated_at.desc()).all()
    return render_template("admin/artikels.html", rijen=rijen,
                           title="Artikels", family=None, active="artikels")


@bp.route("/artikels/nieuw", methods=["GET", "POST"])
@bp.route("/artikels/<int:artikel_id>", methods=["GET", "POST"])
@medewerker_required
def artikel_bewerk(artikel_id=None):
    from datetime import datetime
    from ..models import Artikel
    a = db.session.get(Artikel, artikel_id) if artikel_id else None
    if artikel_id and not a:
        abort(404)
    if request.method == "POST":
        titel = (request.form.get("titel") or "").strip()[:160]
        if not titel:
            flash("Een titel is verplicht.", "error")
            return redirect(request.url)
        if a is None:
            a = Artikel(titel=titel)
            db.session.add(a)
            db.session.flush()
        a.titel = titel
        a.slug = _artikel_slug(titel, a.id)
        a.samenvatting = (request.form.get("samenvatting") or "").strip()[:200]
        a.inhoud_md = request.form.get("inhoud_md") or ""
        wil_publiek = request.form.get("gepubliceerd") == "1"
        if wil_publiek and not a.gepubliceerd:
            a.publicatie_datum = a.publicatie_datum or datetime.utcnow()
        a.gepubliceerd = wil_publiek
        db.session.commit()
        audit(f"artikel bewaard: {a.slug} ({'publiek' if a.gepubliceerd else 'concept'})")
        flash("Artikel bewaard." + ("" if a.gepubliceerd else " (concept — nog niet publiek)"), "ok")
        return redirect(url_for("admin.artikel_bewerk", artikel_id=a.id))
    return render_template("admin/artikel_bewerk.html", a=a,
                           title="Artikel", family=None, active="artikels")


@bp.route("/artikels/<int:artikel_id>/verwijder", methods=["POST"])
@admin_required
def artikel_verwijder(artikel_id):
    from ..models import Artikel
    a = db.session.get(Artikel, artikel_id) or abort(404)
    db.session.delete(a)
    db.session.commit()
    audit(f"artikel verwijderd: {a.slug}")
    flash("Artikel verwijderd.", "ok")
    return redirect(url_for("admin.artikels"))


# ---------------------------------------------------------------------------
# Redactie: nieuwsbrief-preview + artikelsuggesties + AI-concept — patch 135
# ---------------------------------------------------------------------------

@bp.route("/redactie")
@medewerker_required
def redactie():
    from ..services.redactie import voorbeeldgezin, artikel_suggesties
    from ..services.weekendmail import bouw_weekendmail
    from ..models import get_setting
    from ..models import SocialPost
    fam = voorbeeldgezin()
    mail_html, picks = None, []
    if fam:
        mail_html, _, picks = bouw_weekendmail(fam)
    social = (SocialPost.query.filter_by(status="concept")
              .order_by(SocialPost.gepland_voor.asc().nullslast()).limit(12).all())
    return render_template("admin/redactie.html",
                           fam=fam, mail_html=mail_html, picks=picks, social=social,
                           suggesties=artikel_suggesties(),
                           ai_backend=(get_setting("verrijk_backend") or "ollama"),
                           title="Redactie", family=None, active="redactie")


@bp.route("/redactie/testmail", methods=["POST"])
@medewerker_required
@limiter.limit("10/hour")
def redactie_testmail():
    """Stuur de weekendmail-preview naar het e-mailadres van de ingelogde
    beheerder — zo zie je hem exact zoals in een echte mailbox."""
    from ..models import Admin
    from ..services.redactie import voorbeeldgezin
    from ..services.weekendmail import bouw_weekendmail
    from ..services.magic import send_mail
    admin = db.session.get(Admin, session.get("admin_id")) or abort(403)
    fam = voorbeeldgezin()
    if not fam:
        flash("Nog geen gezin met nieuwsbrief-opt-in om mee te previewen.", "error")
        return redirect(url_for("admin.redactie"))
    html, text, picks = bouw_weekendmail(fam)
    if not picks:
        flash("Geen weekend-tips gevonden voor het voorbeeldgezin.", "error")
        return redirect(url_for("admin.redactie"))
    send_mail(admin.email, "PREVIEW — weekendmail (test, niet verstuurd aan gezinnen)",
              html, text)
    audit("redactie: testmail weekendmail verstuurd")
    flash(f"Testmail verstuurd naar {admin.email}.", "ok")
    return redirect(url_for("admin.redactie"))


@bp.route("/redactie/ai-concept", methods=["POST"])
@medewerker_required
@limiter.limit("20/hour")
def redactie_ai_concept():
    from ..services.redactie import ai_concept
    onderwerp = (request.form.get("onderwerp") or "").strip()[:200]
    hoek = (request.form.get("hoek") or "").strip()[:300]
    if not onderwerp:
        flash("Kies of typ eerst een onderwerp.", "error")
        return redirect(url_for("admin.redactie"))
    a = ai_concept(onderwerp, hoek)
    if not a:
        flash("De AI-backend gaf geen bruikbaar resultaat — controleer de "
              "verrijk-instellingen op de Status-pagina.", "error")
        return redirect(url_for("admin.redactie"))
    audit(f"redactie: AI-concept aangemaakt ({a.slug})")
    flash("Conceptartikel klaargezet — lees na, pas aan en publiceer bewust.", "ok")
    return redirect(url_for("admin.artikel_bewerk", artikel_id=a.id))


@bp.route("/redactie/social/<int:post_id>/<actie>", methods=["POST"])
@medewerker_required
def redactie_social_actie(post_id, actie):
    from ..models import SocialPost
    p = db.session.get(SocialPost, post_id) or abort(404)
    if actie == "gebruikt":
        p.status = "gebruikt"
        flash("Post gemarkeerd als gebruikt.", "ok")
    elif actie == "verwijder":
        db.session.delete(p)
        flash("Conceptpost verwijderd.", "ok")
    else:
        abort(400)
    db.session.commit()
    return redirect(url_for("admin.redactie"))


@bp.route("/redactie/social/nu", methods=["POST"])
@medewerker_required
@limiter.limit("6/hour")
def redactie_social_nu():
    """Handmatig de weekgeneratie draaien (zelfde code als de cron)."""
    from ..services.redactie import maak_socialconcepten, maak_weekconcept_artikel
    a = maak_weekconcept_artikel()
    posts = maak_socialconcepten()
    audit(f"redactie: concepten gegenereerd ({len(posts)} social, blog: {bool(a)})")
    flash(f"Gegenereerd: {len(posts)} socialconcept(en)"
          + (f" + blogconcept '{a.titel}'" if a else "") + ".", "ok")
    return redirect(url_for("admin.redactie"))


@bp.route("/partners/ververs-odoo", methods=["POST"])
@admin_required
@limiter.limit("12/hour")
def partners_ververs_odoo():
    """Haal de actuele factuurnummers op uit Odoo (patch 148): facturen die
    Xavier daar intussen inboekte, wisselen hun 'CONCEPT'-label voor het
    echte nummer."""
    from ..models import PartnerPayment
    from .. import odoo
    if not odoo.actief():
        flash("Odoo-koppeling is niet geconfigureerd.", "error")
        return redirect(url_for("admin.partners"))
    betalingen = PartnerPayment.query.filter(
        PartnerPayment.odoo_invoice_id.isnot(None)).all()
    try:
        n = odoo.ververs_refs(betalingen)
    except Exception:
        current_app.logger.exception("ververs uit Odoo mislukt")
        flash("Kon Odoo niet bereiken — probeer later opnieuw.", "error")
        return redirect(url_for("admin.partners"))
    audit(f"factuurstatussen ververst uit Odoo ({n} bijgewerkt)")
    flash(f"{n} factuurnummer(s) bijgewerkt uit Odoo." if n
          else "Alles was al actueel — geen concept-facturen meer in Odoo geboekt.", "ok")
    return redirect(url_for("admin.partners"))


# ---------------------------------------------------------------------------
# Verkopers & commissies (patch 153)
# ---------------------------------------------------------------------------

@bp.route("/verkopers", methods=["GET", "POST"])
@admin_required
def verkopers():
    from ..models import Verkoper, PartnerPayment
    from .. import mollie
    if request.method == "POST":
        naam = (request.form.get("naam") or "").strip()[:120]
        email = (request.form.get("email") or "").strip().lower()[:255]
        try:
            pct = max(0, min(50, int(request.form.get("commissie_pct") or 15)))
        except ValueError:
            pct = 15
        if not naam or "@" not in email:
            flash("Naam en een geldig e-mailadres zijn verplicht.", "error")
        elif Verkoper.query.filter_by(email=email).first():
            flash("Er bestaat al een verkoper met dat e-mailadres.", "error")
        else:
            import secrets as _sec
            code = "RAV-" + _sec.token_hex(2).upper()
            while Verkoper.query.filter_by(code=code).first():
                code = "RAV-" + _sec.token_hex(2).upper()
            db.session.add(Verkoper(naam=naam, email=email, code=code,
                                    commissie_pct=pct))
            db.session.commit()
            audit(f"verkoper aangemaakt: {naam} ({code})")
            flash(f"Verkoper {naam} aangemaakt — code {code}.", "ok")
        return redirect(url_for("admin.verkopers"))

    rijen = Verkoper.query.order_by(Verkoper.actief.desc(), Verkoper.naam).all()
    # Commissie per verkoper per maand (betaald, excl. btw), laatste 12 maanden.
    btw = 1 + mollie.btw_pct() / 100
    betalingen = (PartnerPayment.query
                  .filter(PartnerPayment.status == "paid",
                          PartnerPayment.verkoper_id.isnot(None))
                  .order_by(PartnerPayment.paid_at.desc()).limit(500).all())
    maanden = {}
    for b in betalingen:
        if not b.paid_at:
            continue
        sleutel = (b.verkoper_id, b.paid_at.strftime("%Y-%m"))
        r = maanden.setdefault(sleutel, {"n": 0, "excl": 0.0, "commissie": 0.0})
        excl = float(b.amount or 0) / btw
        pct = (b.verkoper.commissie_pct if b.verkoper else 15) / 100
        r["n"] += 1
        r["excl"] += excl
        r["commissie"] += excl * pct
    overzicht = sorted(
        [{"verkoper_id": vid, "maand": m, **r} for (vid, m), r in maanden.items()],
        key=lambda x: x["maand"], reverse=True)[:36]
    per_id = {v.id: v for v in rijen}
    return render_template("admin/verkopers.html", rijen=rijen,
                           overzicht=overzicht, per_id=per_id,
                           title="Verkopers", family=None, active="verkopers")


@bp.route("/verkopers/<int:vid>/toggle", methods=["POST"])
@admin_required
def verkoper_toggle(vid):
    from ..models import Verkoper
    v = db.session.get(Verkoper, vid) or abort(404)
    v.actief = not v.actief
    db.session.commit()
    audit(f"verkoper {'geactiveerd' if v.actief else 'gedeactiveerd'}: {v.naam}")
    flash(f"Verkoper {v.naam} {'actief' if v.actief else 'inactief'}.", "ok")
    return redirect(url_for("admin.verkopers"))


@bp.route("/simulator")
@admin_required
def simulator():
    """Prijssimulator (patch 158): speel met prijzen, caps, commissie en
    bezetting. Start vanaf de échte instellingen van dit moment."""
    from ..models import get_setting, get_int
    from .. import mollie
    start = {
        "prijsP": float(mollie.prijs("partner")),
        "prijsF": float(mollie.prijs("feest")),
        "prijsC": float(mollie.prijs("combi")),
        "capZ": get_int("cap_zichtbaar_gemeente", 4),
        "capF": get_int("cap_feest_gemeente", 0),
    }
    return render_template("admin/simulator.html", start=start,
                           title="Prijssimulator", family=None, active="simulator")


# ---------------------------------------------------------------------------
# Fietsroutes (patch 160)
# ---------------------------------------------------------------------------

@bp.route("/routes")
@medewerker_required
def routes():
    from ..models import FietsRoute, RouteBuurt
    # Sorteerbaar op elke kolomkop (patch 208); standaard concepten eerst.
    sorteer = (request.args.get("sorteer") or "").strip()
    omgekeerd = request.args.get("omlaag") == "1"
    tellers = dict(db.session.query(RouteBuurt.route_id,
                                    db.func.count(RouteBuurt.event_id))
                   .join(Event, RouteBuurt.event_id == Event.id)
                   .filter(Event.hidden.is_(False), Event.pending.is_(False))
                   .group_by(RouteBuurt.route_id).all())
    rijen = FietsRoute.query.all()
    sleutels = {
        "route": lambda r: (r.titel or "").lower(),
        "regio": lambda r: ((r.regio or "\uffff").lower(), (r.titel or "").lower()),
        "afstand": lambda r: (r.afstand_km or 0),
        "onderweg": lambda r: tellers.get(r.id, 0),
        "status": lambda r: (0 if r.pending else 1, 0 if r.hidden else 1,
                             (r.titel or "").lower()),
    }
    if sorteer in sleutels:
        rijen.sort(key=sleutels[sorteer], reverse=omgekeerd)
    else:
        rijen.sort(key=lambda r: (0 if r.pending else 1, (r.titel or "").lower()))
    return render_template("admin/routes.html", rijen=rijen, tellers=tellers,
                           sorteer=sorteer, omlaag=omgekeerd,
                           title="Fietsroutes", family=None, active="routes")


def _route_form_opslaan(r, f):
    """Redactionele velden uit het formulier (auto-velden komen uit de GPX)."""
    r.titel = (f.get("titel") or r.titel or "").strip()[:200]
    r.beschrijving = (f.get("beschrijving") or "").strip() or None
    r.routebeschrijving = (f.get("routebeschrijving") or "").strip() or None
    r.regio = (f.get("regio") or "").strip()[:80] or None
    r.moeilijkheid = f.get("moeilijkheid") if f.get("moeilijkheid") in (
        "vlak", "licht", "pittig") else r.moeilijkheid
    for veld in ("age_min", "age_max", "duur_min", "verhard_pct", "autovrij_pct"):
        w = f.get(veld)
        if w and w.strip().isdigit():
            setattr(r, veld, int(w))
    r.bron_naam = (f.get("bron_naam") or "").strip()[:120] or None
    r.bron_url = (f.get("bron_url") or "").strip()[:300] or None
    if r.bron_url and not r.bron_url.startswith(("http://", "https://")):
        r.bron_url = "https://" + r.bron_url
    r.buggyvriendelijk = f.get("buggyvriendelijk") == "1"
    r.pending = f.get("pending") == "1"
    r.hidden = f.get("hidden") == "1"
    if f.get("cover_photo_id", "").isdigit():
        r.cover_photo_id = int(f["cover_photo_id"])


@bp.route("/routes/nieuw", methods=["GET", "POST"])
@bp.route("/routes/<int:rid>", methods=["GET", "POST"])
@medewerker_required
def route_bewerk(rid=None):
    from ..models import FietsRoute, Photo
    from ..services import routes_gis
    from ..services.sources.base import slugify
    import os, secrets as _sec
    r = db.session.get(FietsRoute, rid) if rid else None
    if rid and not r:
        abort(404)
    if request.method == "POST":
        nieuw = r is None
        if nieuw:
            r = FietsRoute(titel=(request.form.get("titel") or "Route").strip()[:200])
            r.slug = f"{slugify(r.titel)}-{_sec.token_hex(2)}"[:220]
            db.session.add(r)
        _route_form_opslaan(r, request.form)
        # GPX: het automatische pad — alle afgeleide velden in één keer
        bestand = request.files.get("gpx")
        if bestand and bestand.filename:
            try:
                punten = routes_gis.parse_gpx(bestand.read())
            except ValueError as e:
                flash(str(e), "error")
                return redirect(url_for("admin.route_bewerk", rid=r.id) if r.id
                                else url_for("admin.routes"))
            st = routes_gis.route_stats(punten)
            for veld, waarde in st.items():
                setattr(r, veld, waarde)
            r.geometrie = routes_gis.vereenvoudig(punten)
            if not request.form.get("duur_min"):
                r.duur_min = routes_gis.duur_suggestie(st["afstand_km"])
            r.moeilijkheid = request.form.get("moeilijkheid") or \
                routes_gis.moeilijkheid_suggestie(st["afstand_km"], st["hoogte_m"])
            from ..services.route_generator import gpx_map
            os.makedirs(gpx_map(), exist_ok=True)
            naam = f"route-{r.slug}.gpx"
            bestand.seek(0)
            bestand.save(os.path.join(gpx_map(), naam))
            r.gpx_bestand = naam
            # gemeente van het startpunt via de bestaande buurtafleiding
            from ..services.sources.base import dichtste_gemeente
            gem, pc = dichtste_gemeente(r.start_lat, r.start_lng)
            if gem:
                r.gemeente, r.postcode = gem, pc
        db.session.commit()
        if r.geometrie:
            n = routes_gis.koppel_route(r)
            flash(f"Route bewaard — {n} plekken 'leuk onderweg' gekoppeld.", "ok")
        else:
            flash("Route bewaard. Upload een GPX om de kaart en de koppeling "
                  "te activeren.", "ok")
        audit(f"fietsroute {'aangemaakt' if nieuw else 'bewerkt'}: {r.titel}")
        return redirect(url_for("admin.route_bewerk", rid=r.id))

    fotos = Photo.query.filter_by(route_id=r.id).all() if r else []
    return render_template("admin/route_bewerk.html", r=r, fotos=fotos,
                           title=r.titel if r else "Nieuwe route",
                           family=None, active="routes")


@bp.route("/routes/<int:rid>/foto", methods=["POST"])
@medewerker_required
def route_foto(rid):
    from ..models import FietsRoute, Photo
    from .. import fotos as fotodienst
    r = db.session.get(FietsRoute, rid) or abort(404)
    bestand = request.files.get("foto")
    if not bestand or not bestand.filename:
        flash("Geen bestand gekozen.", "error")
        return redirect(url_for("admin.route_bewerk", rid=rid))
    naam = fotodienst.verwerk_upload(bestand)
    if not naam:
        flash("Dat lijkt geen geldige foto (jpeg/png/webp).", "error")
        return redirect(url_for("admin.route_bewerk", rid=rid))
    p = Photo(route_id=r.id, filename=naam, soort="zaak", status="approved")
    db.session.add(p)
    db.session.flush()
    if not r.cover_photo_id:
        r.cover_photo_id = p.id
    db.session.commit()
    flash("Foto toegevoegd.", "ok")
    return redirect(url_for("admin.route_bewerk", rid=rid))


@bp.route("/routes/<int:rid>/koppel", methods=["POST"])
@medewerker_required
def route_koppel(rid):
    from ..models import FietsRoute
    from ..services import routes_gis
    r = db.session.get(FietsRoute, rid) or abort(404)
    n = routes_gis.koppel_route(r)
    flash(f"Koppeling ververst: {n} plekken langs de route.", "ok")
    return redirect(url_for("admin.route_bewerk", rid=rid))


@bp.route("/routes/<int:rid>/verwijder", methods=["POST"])
@medewerker_required
def route_verwijder(rid):
    from ..models import FietsRoute, RouteBuurt, Photo
    r = db.session.get(FietsRoute, rid) or abort(404)
    RouteBuurt.query.filter_by(route_id=rid).delete()
    Photo.query.filter_by(route_id=rid).delete()
    naam = r.titel
    db.session.delete(r)
    db.session.commit()
    audit(f"fietsroute verwijderd: {naam}")
    flash(f"Route '{naam}' verwijderd.", "ok")
    return redirect(url_for("admin.routes"))


# ---------------------------------------------------------------------------
# Vrije foto's van Wikimedia Commons (patch 163)
# ---------------------------------------------------------------------------

@bp.route("/activiteit/<int:eid>/vrije-fotos")
@medewerker_required
def vrije_fotos(eid):
    ev = db.session.get(Event, eid) or abort(404)
    kandidaten = []
    if ev.lat is not None:
        from ..services.verrijking import commons_zoek
        kandidaten = commons_zoek(ev.lat, ev.lng)
    return render_template("admin/vrije_fotos.html", ev=ev,
                           kandidaten=kandidaten, title="Vrije foto's",
                           family=None, active="activiteiten")


@bp.route("/activiteit/<int:eid>/vrije-fotos/import", methods=["POST"])
@medewerker_required
def vrije_foto_import(eid):
    from ..models import Photo
    from ..services.verrijking import commons_import, _Upload
    from .. import fotos as fotodienst
    ev = db.session.get(Event, eid) or abort(404)
    data, fout = commons_import(request.form.get("url", ""))
    if fout:
        flash(fout, "error")
        return redirect(url_for("admin.vrije_fotos", eid=eid))
    naam = fotodienst.verwerk_upload(_Upload(data, "commons.jpg"))
    if not naam:
        flash("De afbeelding kon niet verwerkt worden.", "error")
        return redirect(url_for("admin.vrije_fotos", eid=eid))
    p = Photo(event_id=ev.id, filename=naam, soort="zaak", status="approved",
              bron="commons",
              fotograaf=(request.form.get("fotograaf") or "")[:120] or None,
              licentie=(request.form.get("licentie") or "")[:40] or None,
              bron_url=(request.form.get("pagina") or "")[:300] or None)
    db.session.add(p)
    db.session.commit()
    audit(f"vrije foto (Commons) geïmporteerd op fiche {ev.id}")
    flash("Foto geïmporteerd, mét bronvermelding op de fiche.", "ok")
    return redirect(url_for("admin.vrije_fotos", eid=eid))


# ---------------------------------------------------------------------------
# Type-illustraties zelf instellen (patch 167)
# ---------------------------------------------------------------------------

@bp.route("/illustraties")
@admin_required
def illustraties():
    from ..media import ILLUSTRATIES, eigen_illustratie_pad
    rijen = [(k, label, eigen_illustratie_pad(k) is not None)
             for k, label in ILLUSTRATIES.items()]
    return render_template("admin/illustraties.html", rijen=rijen,
                           title="Illustraties", family=None,
                           active="illustraties")


@bp.route("/illustraties/<sleutel>", methods=["POST"])
@admin_required
def illustratie_upload(sleutel):
    from ..media import ILLUSTRATIES, EIGEN_ILLUSTRATIE_MAP
    if sleutel not in ILLUSTRATIES:
        abort(404)
    import os
    if request.form.get("actie") == "terugzetten":
        pad = f"{EIGEN_ILLUSTRATIE_MAP}/{sleutel}.jpg"
        if os.path.exists(pad):
            os.remove(pad)
        flash(f"'{ILLUSTRATIES[sleutel]}' terug naar de ingebouwde illustratie.", "ok")
        return redirect(url_for("admin.illustraties"))
    bestand = request.files.get("beeld")
    if not bestand or not bestand.filename:
        flash("Geen bestand gekozen.", "error")
        return redirect(url_for("admin.illustraties"))
    try:
        from PIL import Image
        import io
        beeld = Image.open(io.BytesIO(bestand.read()))
        beeld = beeld.convert("RGB")
        # Uitsnijden naar 2:1 (800x400): eerst schalen tot de kleinste zijde
        # past, dan gecentreerd croppen — elke upload wordt zo netjes uniform.
        doel_w, doel_h = 800, 400
        schaal = max(doel_w / beeld.width, doel_h / beeld.height)
        beeld = beeld.resize((round(beeld.width * schaal),
                              round(beeld.height * schaal)), Image.LANCZOS)
        x = (beeld.width - doel_w) // 2
        y = (beeld.height - doel_h) // 2
        beeld = beeld.crop((x, y, x + doel_w, y + doel_h))
    except Exception:
        flash("Dat lijkt geen geldige afbeelding (jpeg/png/webp).", "error")
        return redirect(url_for("admin.illustraties"))
    os.makedirs(EIGEN_ILLUSTRATIE_MAP, exist_ok=True)
    beeld.save(f"{EIGEN_ILLUSTRATIE_MAP}/{sleutel}.jpg", quality=88, optimize=True)
    audit(f"type-illustratie vervangen: {sleutel}")
    flash(f"Illustratie '{ILLUSTRATIES[sleutel]}' vervangen — geldt meteen "
          "op alle kaartjes en fiches van dat type.", "ok")
    return redirect(url_for("admin.illustraties"))


@bp.route("/route-voorstellen", methods=["GET", "POST"])
@medewerker_required
def route_voorstellen():
    """Wachtrij van de routegenerator (patch 188): de machine stelt lussen
    voor op gezinsdichtheid, de redactie beslist — en rijdt ze liefst na."""
    from ..models import Knooppunt, RouteVoorstel
    from ..services.route_generator import genereer_voorstellen, promoveer
    if request.method == "POST":
        actie = request.form.get("actie")
        if actie == "genereer":
            gemeente = (request.form.get("gemeente") or "").strip()
            if "," in gemeente:
                # Streek-run: meerdere steden, 3 voorstellen per stad =
                # spreiding over de streek i.p.v. een stapel rond één centrum.
                from ..services.route_generator import genereer_streek
                bewaard, onderzocht, per = genereer_streek(
                    gemeente.split(","))
                if onderzocht == 0:
                    flash("Niets gevonden. Is het netwerk geladen en hebben "
                          "deze gemeenten plekken in Ravot?", "error")
                else:
                    detail = " · ".join(f"{g}: {b}" for g, b, _ in per)
                    flash(f"Streek-run: {onderzocht} lussen onderzocht, "
                          f"{bewaard} voorstellen bewaard ({detail}).", "ok")
                audit(f"routegenerator streek: {gemeente}")
            elif gemeente:
                bewaard, onderzocht = genereer_voorstellen(gemeente)
                if onderzocht == 0:
                    flash("Niets gevonden. Is het netwerk geladen en heeft "
                          f"'{gemeente}' plekken in Ravot?", "error")
                else:
                    flash(f"{onderzocht} lussen onderzocht, {bewaard} nieuwe "
                          "voorstellen bewaard.", "ok")
                audit(f"routegenerator: {gemeente}")
        elif actie in ("promoveer", "afwijzen"):
            v = db.session.get(RouteVoorstel, int(request.form.get("vid", 0)))
            if v is not None and v.status == "nieuw":
                if actie == "promoveer":
                    route = promoveer(v)
                    flash(f"Voorstel is nu conceptroute '{route.titel}' — werk "
                          "hem af (titel, verhaal, foto) en rijd hem na vóór "
                          "publicatie.", "ok")
                    audit(f"routevoorstel {v.id} gepromoveerd tot route {route.id}")
                else:
                    v.status = "afgewezen"
                    db.session.commit()
                    audit(f"routevoorstel {v.id} afgewezen")
        return redirect(url_for("admin.route_voorstellen"))
    # Gegroepeerd per streek (patch 207): kiezen doe je per streek, dus de
    # wachtrij toont per streek een blok, hoogste score bovenaan. Volgorde:
    # provincie, dan streek; gemeenten buiten de tabel onder "Overige".
    from ..regios import STREEK_PROVINCIE, streek_van_gemeente
    alle = (RouteVoorstel.query.filter_by(status="nieuw")
            .order_by(RouteVoorstel.score.desc()).limit(300).all())
    per_streek = {}
    for v in alle:
        per_streek.setdefault(streek_van_gemeente(v.gemeente) or "Overige",
                              []).append(v)
    volgorde = list(STREEK_PROVINCIE.keys()) + ["Overige"]
    groepen = [(st, STREEK_PROVINCIE.get(st, ""), per_streek[st])
               for st in volgorde if st in per_streek]
    n_knopen = Knooppunt.query.count()
    return render_template("admin/route_voorstellen.html",
                           groepen=groepen, n_knopen=n_knopen,
                           title="Routevoorstellen", family=None,
                           active="routes")


@bp.route("/routes/<int:rid>/gpx")
@medewerker_required
def route_gpx_download(rid):
    """GPX van een (concept)route voor de redactie — óók pending, want je wilt
    hem juist vóór de testrit op je fietscomputer (patch 191)."""
    import os
    from flask import send_file
    from ..models import FietsRoute
    r = db.session.get(FietsRoute, rid) or abort(404)
    # Altijd vers schrijven (patch 196): kost niets en zorgt dat het bestand
    # de actuele geometrie volgt (bv. na een verbetering of hermeting).
    from ..services.route_generator import schrijf_gpx
    if not schrijf_gpx(r):
        flash("Deze route heeft geen geometrie, dus geen GPX.", "error")
        return redirect(url_for("admin.routes"))
    db.session.commit()
    from ..services.route_generator import gpx_map
    pad = os.path.join(gpx_map(), r.gpx_bestand)
    return send_file(pad, mimetype="application/gpx+xml", as_attachment=True,
                     download_name=f"ravot-{r.slug}.gpx")


@bp.route("/routes/<int:rid>/ai-tekst", methods=["POST"])
@medewerker_required
def route_ai_tekst(rid):
    """AI-naam en -beschrijving voor een bestáánde route (patch 192): zelfde
    machine als bij de promotie, maar op verzoek. De slug blijft ongemoeid
    zodat links en reviews blijven werken; de oude titel staat in de melding
    zodat er niets stilletjes verloren gaat."""
    from ..models import FietsRoute
    from ..services.route_generator import ai_titel_en_beschrijving, schrijf_gpx
    r = db.session.get(FietsRoute, rid) or abort(404)
    ai = ai_titel_en_beschrijving(r)
    if not ai:
        flash("De AI gaf geen bruikbaar voorstel (backend niet beschikbaar?). "
              "Probeer het straks opnieuw.", "error")
        return redirect(url_for("admin.route_bewerk", rid=rid))
    naam, besch = ai
    oude_titel = r.titel
    # Warme AI-tekst hoort in de inspiratietekst (patch 195); een eerder per
    # ongeluk bewaarde letterlijke "None" ruimen we meteen op. De
    # routebeschrijving houdt enkel het stap-voor-stap-gedeelte: een oude
    # AI-inspiratietekst die daar (vóór 195) belandde, vervangen we door de
    # bewaarde knooppuntenregel.
    r.titel = naam
    r.beschrijving = besch
    oud_rb = (r.routebeschrijving or "").strip()
    knooppunten = ""
    for regel in oud_rb.splitlines():
        if regel.strip().startswith("Knooppunten:"):
            knooppunten = regel.strip()
            break
    if knooppunten and not oud_rb.startswith("Knooppunten:"):
        r.routebeschrijving = knooppunten
    for veld in ("regio",):
        if (getattr(r, veld) or "").strip() == "None":
            setattr(r, veld, None)
    if not r.regio:
        from ..services.route_generator import regio_suggestie
        r.regio = regio_suggestie(r)
    if not r.start_adres and r.start_lat is not None:
        from ..geo import adres_van_punt
        r.start_adres = adres_van_punt(r.start_lat, r.start_lng)
    if not r.gpx_bestand:
        schrijf_gpx(r)
    # Klimmeters (her)meten (patch 194): een gemeten "vlak" of een eerlijk
    # leeg veld, maar nooit een aanname.
    from ..services.route_generator import meet_klimmeters
    from ..services.routes_gis import moeilijkheid_suggestie
    klim = meet_klimmeters(r.geometrie)
    if klim is not None:
        r.hoogte_m = klim
        r.moeilijkheid = moeilijkheid_suggestie(r.afstand_km or 0, klim)
    db.session.commit()
    audit(f"route {rid}: AI-tekst toegepast ('{oude_titel}' -> '{naam}')")
    flash(f"AI-voorstel toegepast (was: '{oude_titel}'). Pas gerust aan — "
          "jij kent de route, de AI niet.", "ok")
    return redirect(url_for("admin.route_bewerk", rid=rid))


@bp.route("/bingo", methods=["GET", "POST"])
@medewerker_required
def bingo_nazicht():
    """Bingowedstrijd (patch 200): inzendingen nakijken. Goedkeuren = punten
    voor het gezin (eenmalig per route per maand, via de unieke ref).
    De maandwinnaar kies je zelf uit de goedgekeurde kaarten — extra punten
    geef je via de puntencorrectie op de gezinspagina."""
    from ..models import BingoInzending, get_int
    from .. import punten as pas
    if request.method == "POST":
        inz = db.session.get(BingoInzending,
                             int(request.form.get("bid", 0))) or abort(404)
        actie = request.form.get("actie")
        if actie == "goed" and inz.status == "pending":
            inz.status = "goed"
            ref = inz.maand * 100000 + inz.route_id
            n = pas.ken_toe(inz.family_id, "bingo", ref_id=ref)
            db.session.commit()
            audit(f"bingo {inz.id} goedgekeurd (+{n} punten gezin {inz.family_id})")
            flash(f"Goedgekeurd — +{n} ravotpunten voor het gezin.", "ok")
        elif actie == "af" and inz.status == "pending":
            inz.status = "afgekeurd"
            db.session.commit()
            audit(f"bingo {inz.id} afgekeurd")
            flash("Afgekeurd.", "ok")
        return redirect(url_for("admin.bingo_nazicht"))
    rijen = (BingoInzending.query.order_by(BingoInzending.created_at.desc())
             .limit(100).all())
    return render_template("admin/bingo.html", rijen=rijen,
                           punt_bingo=get_int("punt_bingo", 15),
                           title="Fietsbingo", family=None, active="routes")


@bp.route("/bingo-foto/<int:bid>")
@medewerker_required
def bingo_foto(bid):
    """Bingo-inzendingsfoto, enkel voor het beheer (nooit publiek)."""
    import os
    from flask import send_file
    from ..models import BingoInzending
    from ..fotos import pad_van
    inz = db.session.get(BingoInzending, bid) or abort(404)
    pad = pad_van(inz.filename)
    if not os.path.exists(pad):
        abort(404)
    return send_file(pad, mimetype="image/jpeg")


@bp.route("/route-voorstellen/<int:vid>")
@medewerker_required
def route_voorstel_detail(vid):
    """Voorstel bekijken vóór je beslist (patch 201): tracé op kaart, gemeten
    klimmeters en de plekken die er écht langs liggen."""
    from ..models import Event, RouteVoorstel, get_int
    from ..scoring import haversine_km
    from ..services.route_generator import meet_klimmeters
    from ..services.routes_gis import moeilijkheid_suggestie, sample
    v = db.session.get(RouteVoorstel, vid) or abort(404)
    if v.hoogte_m is None:
        v.hoogte_m = meet_klimmeters(v.geometrie)
        if v.hoogte_m is not None:
            db.session.commit()
    moeilijkheid = (moeilijkheid_suggestie(v.afstand_km or 0, v.hoogte_m)
                    if v.hoogte_m is not None else None)
    # Plekken langs het tracé (zelfde logica als de score, nu met namen)
    lijn = sample([(p[0], p[1], None) for p in (v.geometrie or [])],
                  stap_km=0.3)
    plekken = []
    if lijn:
        lats = [p[0] for p in lijn]
        lngs = [p[1] for p in lijn]
        marge = 0.006
        from ..types import TYPES, groep_van
        kandidaten = (Event.query
                      .filter(Event.is_permanent.is_(True),
                              Event.pending.is_(False),
                              Event.hidden.is_(False),
                              Event.lat.between(min(lats) - marge,
                                                max(lats) + marge),
                              Event.lng.between(min(lngs) - marge,
                                                max(lngs) + marge))
                      .limit(400).all())
        for ev in kandidaten:
            beste = None
            for i, (la, ln, _) in enumerate(lijn):
                d = haversine_km(ev.lat, ev.lng, la, ln)
                if beste is None or d < beste[0]:
                    beste = (d, i)
            if beste and beste[0] * 1000 <= 400:
                emoji = TYPES.get(ev.subtype or "", ("📍",))[0]
                plekken.append((round(beste[1] * 0.3, 1), emoji, ev.title,
                                groep_van(ev)))
        plekken.sort()
    return render_template("admin/route_voorstel_detail.html", v=v,
                           moeilijkheid=moeilijkheid, plekken=plekken[:25],
                           min_km=get_int("generator_min_km", 12),
                           title=f"Voorstel {v.id}", family=None,
                           active="routes")


@bp.route("/route-voorstellen/<int:vid>/gpx")
@medewerker_required
def route_voorstel_gpx(vid):
    """Proef-GPX van een voorstel — testrijden vóór je promoveert."""
    import io
    from xml.sax.saxutils import escape
    from flask import send_file
    from ..models import RouteVoorstel
    v = db.session.get(RouteVoorstel, vid) or abort(404)
    naam = f"Ravot-voorstel {v.id} — {v.gemeente} {v.afstand_km:g} km"
    regels = ['<?xml version="1.0" encoding="UTF-8"?>',
              '<gpx version="1.1" creator="Ravot.be" '
              'xmlns="http://www.topografix.com/GPX/1/1">',
              f"  <trk><name>{escape(naam)}</name><trkseg>"]
    for p in (v.geometrie or []):
        regels.append(f'    <trkpt lat="{p[0]:.6f}" lon="{p[1]:.6f}"/>')
    regels += ["  </trkseg></trk>", "</gpx>"]
    return send_file(io.BytesIO("\n".join(regels).encode()),
                     mimetype="application/gpx+xml", as_attachment=True,
                     download_name=f"ravot-voorstel-{v.id}.gpx")


@bp.route("/uitbaters")
@medewerker_required
def uitbaters():
    """Alle geregistreerde uitbaters (patch 229).

    De partnerpagina toont wie bétaalt; deze toont wie zich registreerde. Dat
    verschil is verkoopwerk waard: een uitbater die zelf een account maakte en
    zijn zaak claimde is de warmste prospect die er bestaat.
    """
    from ..models import Operator, OperatorClaim, PartnerPayment
    nu = datetime.utcnow()
    sorteer = (request.args.get("sorteer") or "").strip()
    omlaag = request.args.get("omlaag") == "1"

    claims = {}
    for c, ev in (db.session.query(OperatorClaim, Event)
                  .join(Event, OperatorClaim.event_id == Event.id).all()):
        claims.setdefault(c.operator_id, []).append((c.status, ev))
    betaald = dict(db.session.query(PartnerPayment.operator_id,
                                    db.func.count(PartnerPayment.id))
                   .filter(PartnerPayment.status == "paid")
                   .group_by(PartnerPayment.operator_id).all())

    rijen = []
    for op in Operator.query.all():
        eigen = claims.get(op.id, [])
        goedgekeurd = [ev for st, ev in eigen if st == "approved"]
        wacht = sum(1 for st, _ in eigen if st == "pending")
        actief_partner = any(ev.partner_until and ev.partner_until > nu
                             for ev in goedgekeurd)
        rijen.append({
            "op": op, "zaken": goedgekeurd, "wacht": wacht,
            "partner": actief_partner, "betalingen": betaald.get(op.id, 0),
        })

    sleutels = {
        "naam": lambda r: (r["op"].bedrijfsnaam or r["op"].email or "").lower(),
        "zaken": lambda r: len(r["zaken"]),
        "status": lambda r: (0 if r["partner"] else 1, 0 if r["wacht"] else 1),
        "nieuw": lambda r: r["op"].id,
    }
    rijen.sort(key=sleutels.get(sorteer, sleutels["nieuw"]),
               reverse=omlaag if sorteer else True)
    return render_template("admin/uitbaters.html", rijen=rijen,
                           sorteer=sorteer, omlaag=omlaag,
                           n_partner=sum(1 for r in rijen if r["partner"]),
                           n_prospect=sum(1 for r in rijen
                                          if not r["partner"] and r["zaken"]),
                           title="Uitbaters", family=None, active="uitbaters")


@bp.route("/gemeenteteksten")
@medewerker_required
def gemeenteteksten():
    """Werklijst: waar loont het om een tekst te laten schrijven? (patch 237)

    Gesorteerd op aanbod, want een gemeente met veel activiteiten en routes
    verdient de eerste euro's van een copywriter.
    """
    from ..models import FietsRoute, GemeenteTekst
    tellingen = dict(db.session.query(Event.gemeente, db.func.count(Event.id))
                     .filter(Event.pending.is_(False), Event.hidden.is_(False),
                             Event.gemeente.isnot(None))
                     .group_by(Event.gemeente).all())
    routes = dict(db.session.query(FietsRoute.gemeente,
                                   db.func.count(FietsRoute.id))
                  .filter(FietsRoute.pending.is_(False),
                          FietsRoute.hidden.is_(False),
                          FietsRoute.gemeente.isnot(None))
                  .group_by(FietsRoute.gemeente).all())
    teksten = {t.gemeente: t for t in GemeenteTekst.query.all()}
    rijen = []
    for gemeente, n in tellingen.items():
        if not gemeente:
            continue
        t = teksten.get(gemeente.strip().lower())
        rijen.append({
            "gemeente": gemeente, "aantal": n,
            "routes": routes.get(gemeente, 0),
            "tekst": t, "klaar": bool(t and t.heeft_tekst),
        })
    rijen.sort(key=lambda r: (-r["aantal"], r["gemeente"]))
    return render_template("admin/gemeenteteksten.html", rijen=rijen,
                           n_klaar=sum(1 for r in rijen if r["klaar"]),
                           title="Gemeenteteksten", family=None,
                           active="gemeenteteksten")


@bp.route("/gemeenteteksten/<gemeente>", methods=["GET", "POST"])
@medewerker_required
def gemeentetekst_bewerk(gemeente):
    from ..models import GemeenteTekst
    sleutel = gemeente.strip().lower()[:80]
    t = db.session.get(GemeenteTekst, sleutel)
    if request.method == "POST":
        if t is None:
            t = GemeenteTekst(gemeente=sleutel)
            db.session.add(t)
        t.intro_md = (request.form.get("intro_md") or "").strip()[:8000] or None
        t.slot_md = (request.form.get("slot_md") or "").strip()[:8000] or None
        t.auteur = (request.form.get("auteur") or "").strip()[:120] or None
        t.pending = False        # opslaan in het beheer = goedgekeurd
        db.session.commit()
        audit(f"gemeentetekst {sleutel} bijgewerkt")
        flash(f"Tekst voor {gemeente.title()} bewaard.", "ok")
        return redirect(url_for("admin.gemeenteteksten"))
    aantal = Event.query.filter(Event.pending.is_(False),
                                Event.hidden.is_(False),
                                db.func.lower(Event.gemeente) == sleutel).count()
    return render_template("admin/gemeentetekst_bewerk.html", t=t,
                           gemeente=gemeente.title(), sleutel=sleutel,
                           aantal=aantal, title=f"Tekst {gemeente.title()}",
                           family=None, active="gemeenteteksten")


@bp.route("/bereik")
@medewerker_required
def bereik():
    """Waar wordt er gekeken? (patch 238)

    Het cijfer voor je verkoopgesprek: hoeveel fichebezoeken per gemeente en
    per streek, en welke fietsroutes effectief meegenomen worden. Geen
    persoonsgegevens — enkel maandtotalen.
    """
    from ..statistiek import per_gemeente, per_streek, route_cijfers
    maanden = max(1, min(12, int(request.args.get("maanden", 3) or 3)))
    gemeenten = per_gemeente(maanden)
    streken = per_streek(maanden)
    routes = route_cijfers(maanden)
    return render_template("admin/bereik.html", gemeenten=gemeenten,
                           streken=streken, routes=routes, maanden=maanden,
                           totaal=sum(g["bezoeken"] for g in gemeenten),
                           title="Bereik", family=None, active="bereik")


@bp.route("/routes/herkoppel", methods=["POST"])
@medewerker_required
def routes_herkoppel():
    """Alle routes opnieuw koppelen aan hun buurt (patch 239).

    De koppelafstand ('leuk onderweg' in meter) wordt alleen toegepast op het
    moment dat een route gekoppeld wordt — bij promotie. Wie de instelling
    achteraf wijzigt, zag niets veranderen: de opgeslagen buurt bleef staan.
    Deze knop herberekent alles in één keer.
    """
    from ..models import FietsRoute, get_int
    from ..services import routes_gis
    routes = FietsRoute.query.filter_by(hidden=False).all()
    totaal = 0
    for r in routes:
        if r.geometrie:
            totaal += routes_gis.koppel_route(r)
    db.session.commit()
    meter = get_int("route_buurt_meter", 400) or 400
    audit(f"routes herkoppeld op {meter} m ({len(routes)} routes)")
    flash(f"{len(routes)} routes opnieuw gekoppeld op {meter} m — "
          f"{totaal} plekken 'leuk onderweg'.", "ok")
    return redirect(url_for("admin.routes"))


def _gemeente_token(c):
    """Zorg voor een geldig token; vernieuw als het verlopen is (patch 240)."""
    import secrets
    from datetime import date, timedelta
    if not c.token or not c.token_geldig:
        c.token = secrets.token_urlsafe(24)
        c.token_tot = date.today() + timedelta(days=365)
    return c.token


@bp.route("/gemeentecontacten")
@medewerker_required
def gemeentecontacten():
    """Wie hebben we aangeschreven, wie leverde er iets? (patch 240)"""
    from ..models import GemeenteContact, GemeenteTekst
    tellingen = dict(db.session.query(Event.gemeente, db.func.count(Event.id))
                     .filter(Event.pending.is_(False), Event.hidden.is_(False),
                             Event.gemeente.isnot(None))
                     .group_by(Event.gemeente).all())
    zonder_foto = dict(
        db.session.query(Event.gemeente, db.func.count(Event.id))
        .filter(Event.pending.is_(False), Event.hidden.is_(False),
                Event.image_url.is_(None), Event.gemeente.isnot(None))
        .group_by(Event.gemeente).all())
    contacten = {c.gemeente: c for c in GemeenteContact.query.all()}
    teksten = {t.gemeente: t for t in GemeenteTekst.query.all()}
    rijen = []
    for gemeente, n in tellingen.items():
        if not gemeente:
            continue
        sleutel = gemeente.strip().lower()
        c = contacten.get(sleutel)
        rijen.append({
            "gemeente": gemeente, "sleutel": sleutel, "aantal": n,
            "zonder_foto": zonder_foto.get(gemeente, 0),
            "contact": c, "tekst": teksten.get(sleutel),
        })
    sorteer = (request.args.get("sorteer") or "").strip()
    if sorteer == "opfrissen":
        rijen.sort(key=lambda r: (not (r["contact"] and
                                       r["contact"].vraagt_opfrissing),
                                  -r["aantal"]))
    elif sorteer == "verstuurd":
        rijen.sort(key=lambda r: (r["contact"].laatst_verstuurd
                                  if r["contact"] and r["contact"].laatst_verstuurd
                                  else datetime.min), reverse=True)
    else:
        rijen.sort(key=lambda r: -r["aantal"])
    return render_template("admin/gemeentecontacten.html", rijen=rijen,
                           sorteer=sorteer,
                           n_verstuurd=sum(1 for r in rijen if r["contact"]
                                           and r["contact"].laatst_verstuurd),
                           n_verrijkt=sum(1 for r in rijen if r["contact"]
                                          and r["contact"].laatst_verrijkt),
                           title="Gemeentecontacten", family=None,
                           active="gemeentecontacten")


@bp.route("/gemeentecontacten/<gemeente>", methods=["GET", "POST"])
@medewerker_required
def gemeentecontact(gemeente):
    """Contactgegevens, de deelbare link en een klaargezette mailtekst."""
    from ..models import GemeenteContact
    sleutel = gemeente.strip().lower()[:80]
    c = db.session.get(GemeenteContact, sleutel)
    if request.method == "POST":
        if c is None:
            c = GemeenteContact(gemeente=sleutel)
            db.session.add(c)
        actie = request.form.get("actie")
        if actie == "verstuurd":
            # Jij verstuurt de mail zelf (geen bulkmail vanuit Ravot: we hebben
            # geen toestemming van die diensten). Hier leggen we alleen vast
            # dát het gebeurd is, zodat de opfrisvraag geen giswerk wordt.
            c.laatst_verstuurd = datetime.utcnow()
            _gemeente_token(c)
            db.session.commit()
            flash("Genoteerd als verstuurd.", "ok")
            return redirect(url_for("admin.gemeentecontact", gemeente=sleutel))
        if actie == "nieuwe_link":
            c.token = None
            _gemeente_token(c)
            db.session.commit()
            audit(f"gemeentelink vernieuwd: {sleutel}")
            flash("Nieuwe link aangemaakt — de oude werkt niet meer.", "ok")
            return redirect(url_for("admin.gemeentecontact", gemeente=sleutel))
        c.email = (request.form.get("email") or "").strip()[:255] or None
        c.contactnaam = (request.form.get("contactnaam") or "").strip()[:120] or None
        c.dienst = (request.form.get("dienst") or "").strip()[:160] or None
        c.notitie = (request.form.get("notitie") or "").strip()[:4000] or None
        _gemeente_token(c)
        db.session.commit()
        flash("Bewaard.", "ok")
        return redirect(url_for("admin.gemeentecontact", gemeente=sleutel))

    if c is None:
        c = GemeenteContact(gemeente=sleutel)
        db.session.add(c)
        _gemeente_token(c)
        db.session.commit()
    # Tel wat we effectief vragen (patch 246): speelplekken, niet alles.
    from ..types import groep_van
    speelplekken = [e for e in Event.query.filter(
        Event.pending.is_(False), Event.hidden.is_(False),
        Event.is_permanent.is_(True),
        db.func.lower(Event.gemeente) == sleutel).all()
        if groep_van(e) == "ravotten"]
    aantal = len(speelplekken)
    zonder_foto = sum(1 for e in speelplekken if not e.image_url)
    # Open veldvragen (patch 269): zelfde telling als op de bijdragepagina,
    # zodat de mail een concreet en kloppend cijfer kan noemen.
    from .. import stemmen as _stemmen
    _statussen = _stemmen.veldstatussen_batch([e.id for e in speelplekken])
    open_vragen = 0
    for e in speelplekken:
        st = _statussen.get(e.id, {})
        for v in _stemmen.relevante_velden(e):
            s = st.get(v)
            if s is None or s["toestand"] != "bevestigd":
                open_vragen += 1
    link = url_for("public.gemeente_bijdrage", token=c.token, _external=True)
    return render_template("admin/gemeentecontact.html", c=c,
                           gemeente=gemeente.title(), sleutel=sleutel,
                           aantal=aantal, zonder_foto=zonder_foto,
                           open_vragen=open_vragen, link=link,
                           title=f"Contact {gemeente.title()}", family=None,
                           active="gemeentecontacten")


@bp.route("/gemeentecontacten/import", methods=["GET", "POST"])
@medewerker_required
def gemeentecontacten_import():
    """Contactgegevens invoeren uit een lijst (patch 247).

    Verwacht kolommen: Provincie, Gemeente, Dienst, E-mail, Telefoon, Plaats,
    Bron, Opmerking. Bestaande adressen worden NIET overschreven — wat jij zelf
    invulde of corrigeerde weegt zwaarder dan een lijst.
    """
    from ..models import GemeenteContact
    if request.method == "POST":
        bestand = request.files.get("bestand")
        if not bestand or not bestand.filename.lower().endswith((".xlsx", ".csv")):
            flash("Stuur een .xlsx- of .csv-bestand.", "error")
            return redirect(url_for("admin.gemeentecontacten_import"))
        rijen = []
        try:
            if bestand.filename.lower().endswith(".csv"):
                import csv
                import io
                tekst = bestand.read().decode("utf-8-sig")
                for r in csv.reader(io.StringIO(tekst)):
                    rijen.append(r)
            else:
                try:
                    import openpyxl
                except ImportError:
                    flash("Excel-invoer vereist openpyxl. Bewaar je bestand "
                          "als .csv en probeer opnieuw.", "error")
                    return redirect(url_for("admin.gemeentecontacten_import"))
                wb = openpyxl.load_workbook(bestand, read_only=True)
                ws = wb.worksheets[0]
                for r in ws.iter_rows(values_only=True):
                    rijen.append(list(r))
        except Exception as fout:
            flash(f"Kon het bestand niet lezen: {fout}", "error")
            return redirect(url_for("admin.gemeentecontacten_import"))

        # Gemeenten die we kennen (met aanbod), voor de koppeling
        bekend = {}
        for (g,) in db.session.query(Event.gemeente).filter(
                Event.gemeente.isnot(None)).distinct().all():
            if g:
                bekend[g.strip().lower()] = g.strip()

        nieuw = bijgewerkt = overgeslagen = onbekend = 0
        onbekende_namen = []
        for r in rijen[1:]:                    # eerste rij = koppen
            if not r or len(r) < 4:
                continue
            gemeente = str(r[1] or "").strip()
            email = str(r[3] or "").strip()
            if not gemeente or "@" not in email:
                continue
            sleutel = gemeente.lower()
            if sleutel not in bekend:
                onbekend += 1
                if len(onbekende_namen) < 25:
                    onbekende_namen.append(gemeente)
                continue
            c = db.session.get(GemeenteContact, sleutel)
            if c is None:
                c = GemeenteContact(gemeente=sleutel)
                db.session.add(c)
                nieuw += 1
            elif c.email:
                overgeslagen += 1              # eigen invoer wint
                continue
            else:
                bijgewerkt += 1
            c.email = email[:255]
            c.dienst = (str(r[2] or "").strip() or None)
            opmerking = str(r[7] or "").strip() if len(r) > 7 else ""
            telefoon = str(r[4] or "").strip() if len(r) > 4 else ""
            stukjes = [x for x in (telefoon, opmerking) if x]
            if stukjes and not c.notitie:
                c.notitie = " · ".join(stukjes)[:4000]
            _gemeente_token(c)
        db.session.commit()
        audit(f"gemeentecontacten geïmporteerd: {nieuw} nieuw, {bijgewerkt} aangevuld")
        boodschap = (f"{nieuw} nieuwe contacten, {bijgewerkt} aangevuld, "
                     f"{overgeslagen} overgeslagen (had al een adres).")
        if onbekend:
            boodschap += (f" {onbekend} gemeenten niet gevonden in Ravot"
                          f"{': ' + ', '.join(onbekende_namen) if onbekende_namen else ''}.")
        flash(boodschap, "ok")
        return redirect(url_for("admin.gemeentecontacten"))
    return render_template("admin/gemeentecontacten_import.html",
                           title="Contacten importeren", family=None,
                           active="gemeentecontacten")


@bp.route("/test-mail/<soort>", methods=["POST"])
@medewerker_required
@limiter.limit("10/hour")
def test_mail(soort):
    """Stuur een échte mail naar je eigen adres, met echte inhoud (patch 250).

    Zo zie je de weekendmail of welkomstmail precies zoals een gezin ze krijgt,
    zonder datums te moeten vervalsen. Het gezin dat als voorbeeld dient wordt
    niet aangeraakt: we sturen alleen naar de ingelogde beheerder.
    """
    from ..models import Family
    from ..services.magic import send_mail
    if soort not in ("welkom", "weekend"):
        abort(404)
    admin = db.session.get(Admin, session["admin_id"])
    if not admin or not admin.email:
        flash("Geen e-mailadres bij je beheerdersaccount.", "error")
        return redirect(url_for("admin.verbindingen"))

    # Een gezin als voorbeeld: liefst een met postcode, anders het eerste.
    fam = (Family.query.filter(Family.postcode.isnot(None))
           .order_by(Family.id.desc()).first()
           or Family.query.order_by(Family.id.desc()).first())
    if fam is None:
        flash("Nog geen enkel gezin in de databank — maak er eerst één aan.",
              "error")
        return redirect(url_for("admin.verbindingen"))

    try:
        if soort == "welkom":
            from ..services.welkomstmail import bouw
            onderwerp, html, tekst = bouw(fam)
        elif soort == "weekend":
            from ..services.weekendmail import bouw_weekendmail
            html, tekst, picks = bouw_weekendmail(fam)
            if not html:
                flash("Geen activiteiten gevonden voor dit gezin — de "
                      "weekendmail zou nu ook niet vertrekken.", "error")
                return redirect(url_for("admin.verbindingen"))
            onderwerp = "Dit weekend in jullie buurt"
        send_mail(admin.email, f"[TEST] {onderwerp}", html, text=tekst)
    except Exception as exc:
        flash(f"Testmail mislukte: {str(exc)[:160]}", "error")
        return redirect(url_for("admin.verbindingen"))
    audit(f"testmail '{soort}' verstuurd naar {admin.email}")
    flash(f"Testmail verstuurd naar {admin.email} — opgebouwd met de gegevens "
          f"van gezin #{fam.id}. Het gezin zelf kreeg niets.", "ok")
    return redirect(url_for("admin.verbindingen"))


@bp.route("/routes/meet-ondergrond", methods=["POST"])
@medewerker_required
def routes_meet_ondergrond():
    """Wegdek en verkeersdrukte (bij)meten voor bestaande routes (patch 252).

    Bestaande routes moeten mee-evolueren: de open data wordt wekelijks
    vernieuwd, en routes van vóór deze patch hebben nog geen meting.
    """
    from ..models import FietsRoute
    from ..services.route_generator import meet_ondergrond
    alleen_lege = request.form.get("alles") != "1"
    routes = FietsRoute.query.filter_by(hidden=False).all()
    gemeten = overgeslagen = 0
    for r in routes:
        if not r.geometrie:
            continue
        if alleen_lege and r.verhard_pct is not None:
            overgeslagen += 1
            continue
        meet_ondergrond(r, r.geometrie)
        gemeten += 1
    db.session.commit()
    audit(f"ondergrond gemeten voor {gemeten} routes")
    flash(f"{gemeten} routes gemeten op wegdek en verkeersdrukte"
          f"{f', {overgeslagen} overgeslagen (al gemeten)' if overgeslagen else ''}.",
          "ok")
    return redirect(url_for("admin.routes"))
