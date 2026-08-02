"""Uitbatersportaal (fase 1, gratis): claim je zaak en stel correcties voor.

Alles wat een uitbater indient (claims én fichewijzigingen) passeert de
moderatiewachtrij in het beheer — niets gaat rechtstreeks live. Inloggen is
wachtwoordloos via e-mailcode, met dezelfde beveiligde code-infrastructuur
als voor gezinnen (aparte purpose zodat codes niet uitwisselbaar zijn).
"""
import re
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, session, url_for)

from ..extensions import csrf, db, limiter
from ..models import (EDIT_VELDEN, EditProposal, Event, Operator,
                      OperatorClaim)
from ..services import magic

bp = Blueprint("uitbater", __name__, url_prefix="/uitbater")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _huidige():
    oid = session.get("operator_id")
    return db.session.get(Operator, oid) if oid else None


def operator_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        op = _huidige()
        if not op or not op.active:
            return redirect(url_for("uitbater.login"))
        return f(op, *args, **kwargs)
    return wrapper


def _mijn_goedgekeurde_claim(op, event_id):
    return OperatorClaim.query.filter_by(
        operator_id=op.id, event_id=event_id, status="approved").first()


# ---------------------------------------------------------------- auth --

def _claim_domein_match(email, website):
    """True als het e-maildomein van de uitbater overeenkomt met het
    website-domein van de zaak (bv. jan@heaven24.be claimt heaven24.be).
    Sterk verificatiesignaal: alleen de echte eigenaar heeft zo'n adres."""
    import re
    if not email or not website:
        return False
    mail_domein = email.rsplit("@", 1)[-1].lower().strip()
    if not mail_domein:
        return False
    # website -> kaal domein (zonder scheme, www, pad)
    w = re.sub(r"^https?://", "", website.lower().strip())
    w = re.sub(r"^www\.", "", w).split("/")[0].split(":")[0]
    if not w:
        return False
    # gangbare gratis mailproviders tellen niet als bewijs
    gratis = {"gmail.com", "hotmail.com", "hotmail.be", "outlook.com",
              "outlook.be", "telenet.be", "skynet.be", "yahoo.com",
              "live.be", "live.com", "proximus.be", "icloud.com"}
    if mail_domein in gratis:
        return False
    return mail_domein == w or mail_domein.endswith("." + w) or w.endswith("." + mail_domein)


@bp.route("/", methods=["GET"])
def start():
    if _huidige():
        return redirect(url_for("uitbater.dashboard"))
    return redirect(url_for("uitbater.login"))


def _prijzen():
    from ..models import get_setting
    def _f(key, standaard):
        try:
            return float((get_setting(key) or "").replace(",", ".") or standaard)
        except ValueError:
            return standaard
    return {"jaar": _f("partner_prijs_jaar", 100)}


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("20/hour", methods=["POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        if not EMAIL_RE.match(email):
            flash("Dat lijkt geen geldig e-mailadres.", "error")
            return render_template("uitbater/login.html", title="Voor uitbaters",
                                   prijzen=_prijzen(), family=None, active=None)
        if magic.recent_requests(email) >= current_app.config["MAGIC_REQUESTS_PER_HOUR"]:
            flash("Er zijn al enkele codes verstuurd. Kijk in je mailbox (ook spam).", "error")
            return render_template("uitbater/login.html", title="Voor uitbaters",
                                   prijzen=_prijzen(), family=None, active=None)
        code = magic.issue_code(email, purpose="operator")
        magic.send_mail(
            email, f"Jouw Ravot-uitbaterscode: {code}",
            render_template("mail/inlogcode.html", code=code),
            text=f"Jouw inlogcode voor het Ravot-uitbatersportaal is {code}. "
                 f"Ze is 15 minuten geldig. Niet aangevraagd? Negeer deze mail.",
        )
        session["operator_code_email"] = email
        return render_template("uitbater/code.html", email=email,
                               title="Voer je code in", family=None, active=None)
    return render_template("uitbater/login.html", title="Voor uitbaters",
                           prijzen=_prijzen(), family=None, active=None)


@bp.route("/code", methods=["POST"])
@limiter.limit("20/hour")
def code_verify():
    email = (request.form.get("email") or session.get("operator_code_email") or "").strip().lower()
    code = re.sub(r"\D", "", request.form.get("code", ""))
    if not email:
        return redirect(url_for("uitbater.login"))
    if magic.verify_code(email, code, purpose="operator") is None:
        flash("Die code klopt niet of is verlopen.", "error")
        return render_template("uitbater/code.html", email=email,
                               title="Voer je code in", family=None, active=None)
    session.pop("operator_code_email", None)
    op = Operator.query.filter_by(email=email).first()
    if not op:
        op = Operator(email=email)
        db.session.add(op)
        db.session.commit()
    session.permanent = True
    session["operator_id"] = op.id
    return redirect(url_for("uitbater.dashboard"))


@bp.route("/logout")
def logout():
    session.pop("operator_id", None)
    flash("Je bent afgemeld.", "ok")
    return redirect(url_for("public.landing"))


# ----------------------------------------------------------- dashboard --

@bp.route("/overzicht")
@operator_required
def dashboard(op):
    from .. import mollie
    from ..models import Interaction
    from datetime import datetime, timedelta
    claims = OperatorClaim.query.filter_by(operator_id=op.id) \
        .order_by(OperatorClaim.created_at.desc()).all()
    voorstellen = EditProposal.query.filter_by(operator_id=op.id) \
        .order_by(EditProposal.created_at.desc()).limit(20).all()
    # Bezoekersstatistieken per goedgekeurde zaak (laatste 30 dagen) — voor
    # ÁLLE uitbaters, ook wie (nog) geen Partner is. Zo ziet een uitbater dat
    # zijn fiche al bezoekers krijgt: het bewijs van waarde vóór de betaalvraag.
    sinds = datetime.utcnow() - timedelta(days=30)
    stats = {}
    for c in claims:
        if c.status == "approved" and c.event:
            rijen = db.session.query(Interaction.type, db.func.count(Interaction.id)) \
                .filter(Interaction.event_id == c.event_id,
                        Interaction.created_at >= sinds) \
                .group_by(Interaction.type).all()
            per = dict(rijen)
            stats[c.event_id] = {"views": per.get("view", 0),
                                 "saves": per.get("save", 0) + per.get("like", 0),
                                 "clicks": per.get("click", 0)}
    from datetime import datetime as _dt
    from ..models import VerkoperMachtiging
    machtigingen = (VerkoperMachtiging.query
                    .filter(VerkoperMachtiging.operator_id == op.id,
                            VerkoperMachtiging.tot > _dt.utcnow()).all())
    return render_template("uitbater/dashboard.html", machtigingen=machtigingen, op=op, claims=claims,
                           voorstellen=voorstellen, stats=stats,
                           is_partner=mollie.is_partner, title="Mijn zaken",
                           family=None, active=None)


@bp.route("/claim", methods=["GET", "POST"])
@operator_required
@limiter.limit("20/hour", methods=["POST"])
def claim(op):
    """Zoek je zaak en claim ze. De claim gaat naar de moderatiewachtrij."""
    if request.method == "POST":
        try:
            eid = int(request.form.get("event_id") or 0)
        except ValueError:
            eid = 0
        ev = db.session.get(Event, eid)
        if not ev:
            flash("Kies eerst een plek uit de zoekresultaten.", "error")
            return redirect(url_for("uitbater.claim"))
        bestaand = OperatorClaim.query.filter_by(operator_id=op.id, event_id=ev.id) \
            .filter(OperatorClaim.status.in_(("pending", "approved"))).first()
        if bestaand:
            flash("Je hebt deze plek al geclaimd.", "error")
            return redirect(url_for("uitbater.dashboard"))
        # Domein-verificatie: matcht het e-maildomein van de uitbater met het
        # website-domein van de zaak? Zo ja, sterk signaal dat de claim echt is.
        domein_match = _claim_domein_match(op.email, ev.source_url)
        db.session.add(OperatorClaim(
            operator_id=op.id, event_id=ev.id, domein_match=domein_match,
            note=(request.form.get("note") or "").strip()[:500]))
        db.session.commit()
        if domein_match:
            flash("Claim ingediend! Je e-mailadres matcht de website van de zaak — "
                  "we ronden dit snel af.", "ok")
        else:
            flash("Claim ingediend! We kijken ze na — je krijgt toegang zodra ze "
                  "is goedgekeurd.", "ok")
        return redirect(url_for("uitbater.dashboard"))
    zoek = (request.args.get("q") or "").strip()
    resultaten = []
    if zoek:
        like = f"%{zoek.lower()}%"
        resultaten = Event.query.filter(
            Event.is_permanent.is_(True), Event.hidden.is_(False),
            Event.pending.is_(False),
            db.func.lower(Event.title).like(like)).limit(20).all()
    return render_template("uitbater/claim.html", zoek=zoek, resultaten=resultaten,
                           title="Claim je zaak", family=None, active=None)


@bp.route("/zaak/nieuw", methods=["GET", "POST"])
@operator_required
@limiter.limit("10/hour", methods=["POST"])
def zaak_nieuw(op):
    """Staat de zaak nog niet op Ravot? Dan kan de uitbater ze zelf aanmaken.
    Ze gaat via het nazicht (pending) en wordt meteen aan de uitbater
    toegewezen (auto-claim, goedgekeurd zodra de redactie de zaak goedkeurt)."""
    from .account import _slugify
    import secrets
    if request.method == "POST":
        titel = (request.form.get("titel") or "").strip()[:200]
        adres = (request.form.get("adres") or "").strip()[:200]
        gemeente = (request.form.get("gemeente") or "").strip()[:80]
        postcode = (request.form.get("postcode") or "").strip()[:8]
        website = (request.form.get("website") or "").strip()[:500]
        beschrijving = (request.form.get("beschrijving") or "").strip()[:2000]
        if not titel:
            flash("Vul minstens de naam van je zaak in.", "error")
            return redirect(url_for("uitbater.zaak_nieuw"))
        # Soort + ligging (patch 142): zonder soort valt de zaak buiten de
        # filters, zonder ligging staat ze op geen enkele kaart.
        from ..types import TYPES
        soort = request.form.get("soort") or None
        if soort not in TYPES:
            soort = None
        try:
            p_lat = float(request.form.get("lat") or "")
            p_lng = float(request.form.get("lng") or "")
        except ValueError:
            p_lat = p_lng = None
        if p_lat is None and postcode:
            from ..geo import postcode_coord
            c = postcode_coord(postcode)
            if c:
                p_lat, p_lng = c
                flash("We zetten je zaak voorlopig midden in je postcodegebied — "
                      "verfijn de speld straks via 'Fiche bewerken'.", "ok")
        ev = Event(
            slug=f"{_slugify(titel)}-{secrets.token_hex(3)}",
            title=titel, source="user", is_permanent=True,
            pending=True, curated=False, hidden=False,
            adres=adres or None, gemeente=gemeente or None,
            postcode=postcode or None, source_url=website or None,
            description=beschrijving or None, subtype=soort,
            lat=p_lat, lng=p_lng,
            age_min=0, age_max=12, categories=[])
        db.session.add(ev)
        db.session.flush()
        # Auto-claim: de indiener is de uitbater. Domein-match indien mogelijk.
        db.session.add(OperatorClaim(
            operator_id=op.id, event_id=ev.id,
            domein_match=_claim_domein_match(op.email, website),
            note="Zaak zelf toegevoegd door de uitbater."))
        db.session.commit()
        # Bevestigingsmail
        from ..services import uitbater_mail
        try:
            uitbater_mail.zaak_toegevoegd(op.email, titel)
        except Exception:
            current_app.logger.exception("zaak-toegevoegd-mail mislukt")
        flash("Je zaak is ingediend! Onze redactie kijkt ze na — je krijgt een "
              "mail zodra ze online staat.", "ok")
        return redirect(url_for("uitbater.dashboard"))
    from ..types import TYPES
    soorten = {k: v for k, v in TYPES.items()
               if not k.startswith(("ev_", "uit_"))}
    return render_template("uitbater/zaak_nieuw.html", title="Zaak toevoegen",
                           soorten=soorten, family=None, active=None)


@bp.route("/fiche/<int:event_id>", methods=["GET", "POST"])
@operator_required
@limiter.limit("30/hour", methods=["POST"])
def fiche(op, event_id):
    """Stel correcties voor aan je (goedgekeurde) fiche -> moderatiewachtrij."""
    ev = db.session.get(Event, event_id)
    if not ev or not _mijn_goedgekeurde_claim(op, event_id):
        abort(403)   # enkel je eigen, goedgekeurde zaken
    if request.method == "POST":
        wijzigingen = {}
        for veld in ("description", "adres", "gemeente", "source_url",
                     "reservatie_url"):
            waarde = (request.form.get(veld) or "").strip()
            if waarde and waarde != (getattr(ev, veld) or ""):
                maxlen = {"description": 2000, "adres": 255,
                          "gemeente": 80, "source_url": 500,
                          "reservatie_url": 500}[veld]
                wijzigingen[veld] = waarde[:maxlen]
        postcode = re.sub(r"\D", "", request.form.get("postcode") or "")[:4]
        if postcode and postcode != (ev.postcode or ""):
            wijzigingen["postcode"] = postcode
        # checkboxes: expliciet meesturen als ze afwijken
        indoor = bool(request.form.get("indoor"))
        gratis = bool(request.form.get("is_free"))
        if indoor != bool(ev.indoor):
            wijzigingen["indoor"] = indoor
        if gratis != bool(ev.is_free):
            wijzigingen["is_free"] = gratis
        # Kindvriendelijke voorzieningen — dit zijn dé parameters die van een
        # gewoon café een kindvriendelijke zaak maken.
        for veld in ("kinderstoel", "speelhoek", "kindermenu",
                     "verzorgingstafel", "buggy_ok", "omheind",
                     "terras", "overdekt_terras", "parking", "toegankelijk",
                     "allergievriendelijk", "babyvoeding", "huisdieren"):
            nieuw = bool(request.form.get(veld))
            if nieuw != bool(getattr(ev, veld)):
                wijzigingen[veld] = nieuw
        # Verjaardagsfeestjes: aanbod + contactadres (ook via nazicht)
        from ..models import FEEST_SOORTEN
        feest = bool(request.form.get("feest"))
        if feest != bool(ev.feest):
            wijzigingen["feest"] = feest
        soorten = [s for s in request.form.getlist("feest_soorten")
                   if s in FEEST_SOORTEN]
        if feest and soorten != (ev.feest_soorten or []):
            wijzigingen["feest_soorten"] = soorten
        feest_mail = (request.form.get("feest_contact") or "").strip()[:255]
        if feest_mail and feest_mail != (ev.feest_contact or ""):
            wijzigingen["feest_contact"] = feest_mail
        for veld in ("feest_min_pers", "feest_max_pers"):
            ruw = (request.form.get(veld) or "").strip()
            nieuw = int(ruw) if ruw.isdigit() and 1 <= int(ruw) <= 200 else None
            if nieuw != getattr(ev, veld):
                wijzigingen[veld] = nieuw
        # Soort (patch 142): bepaalt filters en het juiste kaart-contingent.
        from ..types import TYPES
        soort = request.form.get("soort") or ""
        if soort in TYPES and soort != (ev.subtype or ""):
            wijzigingen["subtype"] = soort
        # Openingsuren (patch 142): 7 tekstvelden, leeg = gesloten.
        from ..services.openingsuren import DAGEN, parse_dagtekst, dag_tekst
        uren, uren_fout, uren_anders = {}, False, False
        for dag in DAGEN:
            waarde, ok = parse_dagtekst(request.form.get(f"uren_{dag}"))
            if not ok:
                uren_fout = True
            uren[dag] = waarde
            if dag_tekst(waarde) != dag_tekst((ev.openingsuren or {}).get(dag)):
                uren_anders = True
        if uren_fout:
            flash("Sommige openingsuren begreep ik niet — gebruik het formaat "
                  "09:00-18:00 (of 09:00-12:00, 13:00-18:00). Die dagen liet ik leeg.",
                  "error")
        if uren_anders and any(v for v in uren.values()):
            wijzigingen["openingsuren"] = uren
        # Ligging (patch 142): de speld op de kaart.
        try:
            n_lat = float(request.form.get("lat") or "")
            n_lng = float(request.form.get("lng") or "")
        except ValueError:
            n_lat = n_lng = None
        if n_lat is not None and (ev.lat is None or
                                  abs(n_lat - ev.lat) > 1e-6 or abs(n_lng - ev.lng) > 1e-6):
            wijzigingen["lat"], wijzigingen["lng"] = n_lat, n_lng
        if not wijzigingen:
            flash("Geen wijzigingen gevonden.", "error")
            return redirect(url_for("uitbater.fiche", event_id=ev.id))
        from ..models import get_bool, EDIT_VELDEN
        voorstel = EditProposal(operator_id=op.id, event_id=ev.id,
                                changes=wijzigingen)
        if get_bool("uitbater_auto_ok"):
            # Vertrouwde uitbater (goedgekeurde claim): meteen toepassen.
            # Het voorstel blijft als logboekregel bestaan (Partnerlog).
            for veld, waarde in wijzigingen.items():
                if veld in EDIT_VELDEN:
                    setattr(ev, veld, waarde)
            from ..kwaliteit import bereken_kwaliteit
            ev.quality = bereken_kwaliteit(ev)
            voorstel.status = "approved"
            db.session.add(voorstel)
            db.session.commit()
            flash("Wijziging meteen doorgevoerd — bedankt om je fiche actueel te houden!", "ok")
        else:
            db.session.add(voorstel)
            db.session.commit()
            flash("Wijziging ingediend! Ze verschijnt op de fiche zodra ze is nagekeken.", "ok")
        return redirect(url_for("uitbater.dashboard"))
    from .. import mollie
    from ..types import TYPES
    from ..services.openingsuren import DAGEN, DAG_LABELS, dag_tekst
    uren_nu = {dag: dag_tekst((ev.openingsuren or {}).get(dag)) for dag in DAGEN}
    from ..models import Photo
    eigen_fotos = (Photo.query.filter(Photo.event_id == ev.id,
                                      Photo.family_id.is_(None))
                   .order_by(Photo.created_at.desc()).all())
    soorten = {k: v for k, v in TYPES.items()
               if not k.startswith(("ev_", "uit_"))}
    return render_template("uitbater/fiche.html", ev=ev, title=f"Fiche: {ev.title}",
                           is_partner=mollie.is_partner(ev),
                           soorten=soorten, dagen=DAGEN, dag_labels=DAG_LABELS,
                           uren_nu=uren_nu, eigen_fotos=eigen_fotos,
                           family=None, active=None)


@bp.route("/fiche/<int:event_id>/foto", methods=["POST"])
@operator_required
@limiter.limit("20/hour")
def fiche_foto(op, event_id):
    """Kindermenu of kinderhoek-foto uploaden — komt na nazicht in het
    'Voor de kinderen'-blok op de fiche."""
    ev = db.session.get(Event, event_id)
    if not ev or not _mijn_goedgekeurde_claim(op, event_id):
        abort(403)
    soort = request.form.get("soort")
    if soort not in ("kindermenu", "kinderhoek", "zaak"):
        abort(400)
    from .. import fotos
    from ..models import Photo
    bestand = request.files.get("foto")
    if not bestand or not bestand.filename:
        flash("Kies eerst een foto.", "error")
        return redirect(url_for("uitbater.fiche", event_id=ev.id))
    filename = fotos.verwerk_upload(bestand)
    if not filename:
        flash("Dat lijkt geen geldige foto (jpg/png/webp).", "error")
        return redirect(url_for("uitbater.fiche", event_id=ev.id))
    from ..models import get_bool
    p = Photo(event_id=ev.id, filename=filename, soort=soort)
    if get_bool("uitbater_auto_ok"):
        p.status = "approved"
        db.session.add(p)
        db.session.flush()
        if soort == "zaak" or not ev.image_url:
            ev.image_url = f"/foto/{p.id}"
        db.session.commit()
        flash("Foto staat meteen op je fiche. Bedankt!", "ok")
    else:
        db.session.add(p)
        db.session.commit()
        flash("Foto ontvangen! Ze verschijnt op de fiche zodra ze is nagekeken.", "ok")
    return redirect(url_for("uitbater.fiche", event_id=ev.id))


# ------------------------------------------------------- Ravot Partner --

@bp.route("/partner/<int:event_id>", methods=["GET", "POST"])
@operator_required
@limiter.limit("10/hour", methods=["POST"])
def partner(op, event_id):
    """Ravot Partner afsluiten voor een geclaimde zaak (via Mollie)."""
    from .. import mollie
    from ..models import PartnerPayment
    ev = db.session.get(Event, event_id)
    if not ev or not _mijn_goedgekeurde_claim(op, event_id):
        abort(403)
    if request.method == "POST":
        if not (op.bedrijfsnaam and op.btw_nummer):
            flash("Vul eerst je facturatiegegevens in (bedrijfsnaam en btw-nummer) — "
                  "verplicht voor een correcte factuur.", "error")
            return redirect(url_for("uitbater.facturatie", volgende=ev.id))
        # Formulekeuze (patch 153) + exclusiviteitscap per gemeente.
        from ..models import get_int, Verkoper
        plan = request.form.get("plan") or "partner"
        if plan not in mollie.PLANNEN:
            plan = "partner"
        if ev.gemeente:
            if plan in ("partner", "combi"):
                cap = get_int("cap_zichtbaar_gemeente", 5)
                if mollie.plekken_bezet(ev.gemeente, "zichtbaar") >= cap:
                    flash(f"De zichtbaarheidsplekken in {ev.gemeente} zijn volzet "
                          f"({cap}/{cap}). Mail info@ravot.be voor de wachtlijst.", "error")
                    return redirect(url_for("uitbater.partner", event_id=ev.id))
            if plan in ("feest", "combi"):
                cap = get_int("cap_feest_gemeente", 3)
                if mollie.plekken_bezet(ev.gemeente, "feest") >= cap:
                    flash(f"De feestpartnerplekken in {ev.gemeente} zijn volzet "
                          f"({cap}/{cap}). Mail info@ravot.be voor de wachtlijst.", "error")
                    return redirect(url_for("uitbater.partner", event_id=ev.id))
        # Verkoperscode: koppelt de deal aan de verkoper (commissie) en geeft
        # de klant het uitgebreide welkomstpakket.
        verkoper = None
        code = (request.form.get("verkoperscode") or "").strip().upper()
        if code:
            verkoper = Verkoper.query.filter_by(code=code, actief=True).first()
            if not verkoper:
                flash("Die verkoperscode kennen we niet — check de schrijfwijze "
                      "of laat het veld leeg.", "error")
                return redirect(url_for("uitbater.partner", event_id=ev.id))
        # Invulhulp-machtiging (patch 155): expliciete opt-in, 30 dagen,
        # altijd intrekbaar via het dashboard.
        if verkoper and request.form.get("invulhulp") == "1":
            from datetime import datetime as _dt, timedelta as _td
            from ..models import VerkoperMachtiging
            m = (VerkoperMachtiging.query
                 .filter_by(verkoper_id=verkoper.id, event_id=ev.id).first())
            if not m:
                m = VerkoperMachtiging(verkoper_id=verkoper.id, event_id=ev.id,
                                       operator_id=op.id)
                db.session.add(m)
            m.tot = _dt.utcnow() + _td(days=30)
            db.session.commit()
        if not mollie.actief():
            flash("Online betalen is nog niet geconfigureerd. Mail info@ravot.be "
                  "om Partner te worden.", "error")
            return redirect(url_for("uitbater.partner", event_id=ev.id))
        betaling = PartnerPayment(operator_id=op.id, event_id=ev.id, plan=plan,
                                  verkoper_id=verkoper.id if verkoper else None,
                                  amount=mollie.prijs_incl(plan))   # incl. btw innen
        db.session.add(betaling)
        db.session.commit()
        try:
            checkout = mollie.start_betaling(betaling)
            db.session.commit()          # mollie_id bewaren
        except Exception:
            current_app.logger.warning("mollie start faalde voor betaling %s", betaling.id)
            flash("De betaalpagina kon niet gestart worden. Probeer straks opnieuw.", "error")
            return redirect(url_for("uitbater.partner", event_id=ev.id))
        return redirect(checkout)
    f_aan, f_bezet, f_max = _founding_status()
    al_founding = PartnerPayment.query.filter_by(event_id=ev.id, plan="founding").first() is not None
    from ..models import get_int
    vrij = {}
    if ev.gemeente:
        vrij = {"zichtbaar": max(0, get_int("cap_zichtbaar_gemeente", 5)
                                 - mollie.plekken_bezet(ev.gemeente, "zichtbaar")),
                "feest": max(0, get_int("cap_feest_gemeente", 3)
                             - mollie.plekken_bezet(ev.gemeente, "feest"))}
    formules = [
        {"plan": "partner", "naam": "⭐ Ravot Partner", "prijs": mollie.prijs("partner"),
         "incl": mollie.prijs_incl("partner"), "pool": "zichtbaar",
         "punten": ["Partner-badge en uitgelicht blok", "Grotere kaartspeld",
                    "Reserveerknop op je fiche"]},
        {"plan": "feest", "naam": "🎉 Feestpartner", "prijs": mollie.prijs("feest"),
         "incl": mollie.prijs_incl("feest"), "pool": "feest",
         "punten": ["Exclusieve verjaardagsfeest-aanvragen", "Vermelding in de feestgids",
                    "Rechtstreeks contact met gezinnen"]},
        {"plan": "combi", "naam": "🤝 Combi", "prijs": mollie.prijs("combi"),
         "incl": mollie.prijs_incl("combi"), "pool": "beide",
         "punten": ["Alles van Partner én Feestpartner",
                    "Bespaar t.o.v. twee losse formules", "Uitgebreid welkomstpakket"]},
    ]
    return render_template("uitbater/partner.html", ev=ev,
                           founding_aan=f_aan, founding_over=max(0, f_max - f_bezet),
                           al_founding=al_founding, formules=formules, vrij=vrij,
                           prijs_jaar=mollie.prijs("jaar"),
                           prijs_jaar_incl=mollie.prijs_incl("jaar"),
                           heeft_facturatie=bool(op.bedrijfsnaam and op.btw_nummer),
                           betalen_actief=mollie.actief(),
                           is_partner=mollie.is_partner(ev),
                           title="Word Ravot Partner", family=None, active=None)


@bp.route("/partner/klaar/<int:pid>")
def partner_klaar(pid):
    """Terug van de Mollie-checkout: status tonen (webhook doet het echte werk).
    Bewust ZONDER operator_required: raakte de sessie onderweg kwijt, dan
    herstelt het ondertekende token uit de terugkeer-URL de login — anders
    belandde de uitbater na het betalen op het loginscherm en leek de
    betaling mislukt."""
    from ..models import PartnerPayment, Operator
    from .. import mollie
    op = None
    if session.get("operator_id"):
        op = db.session.get(Operator, session["operator_id"])
    if op is None:
        op_id = mollie.lees_terugkeer_token(request.args.get("t", ""), pid)
        if op_id:
            op = db.session.get(Operator, op_id)
            if op:
                session["operator_id"] = op.id
                session.permanent = True
    if op is None:
        flash("Log even opnieuw in om je betaalstatus te zien.", "error")
        return redirect(url_for("uitbater.login"))
    p = db.session.get(PartnerPayment, pid)
    if not p or p.operator_id != op.id:
        abort(404)
    # Status even verversen voor directe feedback (webhook blijft de bron).
    if p.mollie_id and mollie.actief():
        try:
            mollie.verwerk_webhook(p.mollie_id)
        except Exception:
            pass
    p = db.session.get(PartnerPayment, pid)
    if p.status == "paid":
        flash("Betaling gelukt — je zaak is nu Ravot Partner! 🎉", "ok")
    else:
        flash("De betaling is nog niet bevestigd. Zodra ze binnen is, wordt "
              "Partner automatisch geactiveerd.", "ok")
    return redirect(url_for("uitbater.dashboard"))


@bp.route("/mollie-webhook", methods=["POST"])
@csrf.exempt
@limiter.limit("120/hour")
def mollie_webhook():
    """Mollie meldt enkel een id; wij verifiëren de status bij Mollie zelf.
    Body wordt nooit vertrouwd. Antwoord altijd 200 (Mollie-conventie)."""
    from .. import mollie
    mollie_id = (request.form.get("id") or "").strip()
    if mollie_id.startswith("tr_"):
        try:
            mollie.verwerk_webhook(mollie_id)
        except Exception:
            current_app.logger.warning("mollie-webhook verwerking faalde: %s", mollie_id)
    return ("", 200)


@bp.route("/facturatie", methods=["GET", "POST"])
@operator_required
@limiter.limit("20/hour", methods=["POST"])
def facturatie(op):
    """Facturatiegegevens (verplicht voor Partner): bedrijfsnaam + btw-nummer."""
    if request.method == "POST":
        btw = re.sub(r"[^A-Za-z0-9]", "", request.form.get("btw_nummer") or "").upper()
        if not re.match(r"^BE[01]\d{9}$", btw):
            flash("Geef een geldig Belgisch btw-nummer (BE0xxxxxxxxx).", "error")
            return redirect(url_for("uitbater.facturatie"))
        op.bedrijfsnaam = (request.form.get("bedrijfsnaam") or "").strip()[:160]
        op.btw_nummer = btw
        op.straat = (request.form.get("straat") or "").strip()[:160]
        op.postcode = re.sub(r"\D", "", request.form.get("postcode") or "")[:4]
        op.gemeente = (request.form.get("gemeente") or "").strip()[:80]
        op.contactpersoon = (request.form.get("contactpersoon") or "").strip()[:120]
        op.factuur_email = (request.form.get("factuur_email") or "").strip()[:255]
        op.telefoon = (request.form.get("telefoon") or "").strip()[:40]
        if not op.bedrijfsnaam:
            flash("Bedrijfsnaam is verplicht.", "error")
            return redirect(url_for("uitbater.facturatie"))
        db.session.commit()
        flash("Facturatiegegevens bewaard.", "ok")
        volgende = request.args.get("volgende")
        if volgende and volgende.isdigit():
            return redirect(url_for("uitbater.partner", event_id=int(volgende)))
        return redirect(url_for("uitbater.dashboard"))
    return render_template("uitbater/facturatie.html", op=op,
                           title="Facturatiegegevens", family=None, active=None)


def _founding_status():
    """(aan, bezet, max) van de founding-actie."""
    from ..models import get_bool, get_int, PartnerPayment
    aan = get_bool("founding_aan")
    maxi = get_int("founding_max", 20)
    bezet = PartnerPayment.query.filter_by(plan="founding").count()
    return aan, bezet, maxi


@bp.route("/founding/<int:event_id>", methods=["POST"])
@operator_required
@limiter.limit("5/hour")
def founding(op, event_id):
    """Gratis founding-partnerplaats claimen (eerste jaar, beperkt aantal)."""
    from datetime import datetime, timedelta
    from ..models import PartnerPayment
    ev = db.session.get(Event, event_id)
    if not ev or not _mijn_goedgekeurde_claim(op, event_id):
        abort(403)
    aan, bezet, maxi = _founding_status()
    if not aan or bezet >= maxi:
        flash("De founding-actie is afgelopen of volzet.", "error")
        return redirect(url_for("uitbater.partner", event_id=ev.id))
    al = PartnerPayment.query.filter_by(event_id=ev.id, plan="founding").first()
    if al:
        flash("Deze zaak is al founding partner.", "error")
        return redirect(url_for("uitbater.dashboard"))
    db.session.add(PartnerPayment(operator_id=op.id, event_id=ev.id,
                                  plan="founding", amount="0.00", status="paid",
                                  paid_at=datetime.utcnow()))
    basis = ev.partner_until or datetime.utcnow()
    if basis < datetime.utcnow():
        basis = datetime.utcnow()
    ev.partner_until = basis + timedelta(days=366)
    db.session.commit()
    flash("Welkom als founding partner — je zaak is een jaar lang ⭐ Partner, gratis! 🎉", "ok")
    return redirect(url_for("uitbater.dashboard"))


@bp.route("/kamp/nieuw", methods=["GET", "POST"])
@operator_required
def kamp_nieuw(op):
    """Een uitbater voegt een kamp toe (niveau 1: vermelding met datums +
    eigen inschrijflink). Kampen zijn een apart onderdeel, los van de gewone
    activiteiten. Gaat via het nazicht: pending tot de redactie goedkeurt."""
    from datetime import date as _date
    from ..models import get_bool
    from .account import _slugify
    import secrets
    if not get_bool("kampen_aan"):
        abort(404)
    if request.method == "POST":
        from ..models import KAMP_THEMAS, KAMP_TALEN, Photo
        from ..fotos import verwerk_upload
        titel = (request.form.get("titel") or "").strip()[:200]
        gemeente = (request.form.get("gemeente") or "").strip()[:80]
        postcode = (request.form.get("postcode") or "").strip()[:8]
        adres = (request.form.get("adres") or "").strip()[:200]
        organisator = (request.form.get("organisator") or "").strip()[:200]
        url = (request.form.get("inschrijf_url") or "").strip()[:500]
        prijs = (request.form.get("prijs") or "").strip()[:40]
        samenvatting = (request.form.get("samenvatting") or "").strip()[:2000]
        thema = request.form.get("thema") if request.form.get("thema") in KAMP_THEMAS else None
        taal = request.form.get("taal") if request.form.get("taal") in KAMP_TALEN else None

        def _pdate(v):
            try:
                return _date.fromisoformat(v)
            except (ValueError, TypeError):
                return None
        start = _pdate(request.form.get("start"))
        eind = _pdate(request.form.get("eind")) or start
        try:
            leeft_min = max(0, min(18, int(request.form.get("age_min") or 0)))
            leeft_max = max(leeft_min, min(18, int(request.form.get("age_max") or 12)))
        except ValueError:
            leeft_min, leeft_max = 0, 12
        if not titel or not start:
            flash("Vul minstens een titel en een startdatum in.", "error")
            return redirect(url_for("uitbater.kamp_nieuw"))
        ev = Event(
            slug=f"kamp-{_slugify(titel)}-{secrets.token_hex(3)}",
            title=titel, source="user", is_kamp=True, is_permanent=False,
            pending=True, curated=False, hidden=False,
            gemeente=gemeente or None, postcode=postcode or None,
            adres=adres or None, kamp_organisator=organisator or None,
            kamp_start=start, kamp_eind=eind, kamp_inschrijf_url=url or None,
            kamp_prijs=prijs or None, description=samenvatting or None,
            kamp_thema=thema, kamp_taal=taal,
            kamp_opvang=bool(request.form.get("opvang")),
            kamp_maaltijd=bool(request.form.get("maaltijd")),
            kamp_fiscaal=bool(request.form.get("fiscaal")),
            kamp_mutualiteit=bool(request.form.get("mutualiteit")),
            kamp_overnachting=bool(request.form.get("overnachting")),
            age_min=leeft_min, age_max=leeft_max, categories=[])
        db.session.add(ev)
        db.session.flush()
        # Max 4 foto's, elk automatisch verkleind + heringcodeerd (app/fotos.py).
        bestanden = request.files.getlist("fotos")[:4]
        for f in bestanden:
            naam = verwerk_upload(f)
            if naam:
                db.session.add(Photo(event_id=ev.id, filename=naam,
                                     soort="kamp", status="pending"))
        db.session.commit()
        flash("Kamp ingediend! Het verschijnt zodra onze redactie het nakeek.", "ok")
        return redirect(url_for("uitbater.dashboard"))
    from ..models import KAMP_THEMAS, KAMP_TALEN
    return render_template("uitbater/kamp_nieuw.html", title="Kamp toevoegen",
                           themas=KAMP_THEMAS, talen=KAMP_TALEN,
                           family=None, active=None)


@bp.route("/fiche/<int:event_id>/foto/<int:pid>/verwijder", methods=["POST"])
@operator_required
@limiter.limit("30/hour")
def fiche_foto_verwijder(op, event_id, pid):
    """Eigen foto verwijderen (patch 146). Enkel uploads zonder gezins-id —
    gezinsfoto's zijn van de gezinnen; daarvoor is er de meldknop."""
    from ..models import Photo
    from .. import fotos
    ev = db.session.get(Event, event_id)
    if not ev or not _mijn_goedgekeurde_claim(op, event_id):
        abort(403)
    p = db.session.get(Photo, pid)
    if not p or p.event_id != ev.id or p.family_id is not None:
        abort(404)
    was_hoofd = (ev.image_url == f"/foto/{p.id}")
    fotos.verwijder(p.filename)
    db.session.delete(p)
    db.session.flush()
    if was_hoofd:
        # volgende goedgekeurde eigen foto wordt hoofdbeeld, anders leeg
        volgende = (Photo.query.filter(Photo.event_id == ev.id,
                                       Photo.status == "approved",
                                       Photo.family_id.is_(None))
                    .order_by(Photo.created_at.asc()).first())
        ev.image_url = f"/foto/{volgende.id}" if volgende else None
    db.session.commit()
    flash("Foto verwijderd." + (" Het hoofdbeeld is bijgewerkt." if was_hoofd else ""), "ok")
    return redirect(url_for("uitbater.fiche", event_id=ev.id))


@bp.route("/fiche/<int:event_id>/foto/<int:pid>/focus", methods=["POST"])
@operator_required
@limiter.limit("60/hour")
def fiche_foto_focus(op, event_id, pid):
    """Uitsnede-focus van een eigen foto bewaren (patch 147): welk deel van
    het beeld zichtbaar blijft in hoofdbeeld en thumbnails."""
    from ..models import Photo
    ev = db.session.get(Event, event_id)
    if not ev or not _mijn_goedgekeurde_claim(op, event_id):
        abort(403)
    p = db.session.get(Photo, pid)
    if not p or p.event_id != ev.id or p.family_id is not None:
        abort(404)
    try:
        p.focus_y = max(0, min(100, int(request.form.get("focus_y") or 50)))
    except ValueError:
        p.focus_y = 50
    db.session.commit()
    flash("Uitsnede bewaard — zo staat je foto meteen goed op fiche en kaartjes.", "ok")
    return redirect(url_for("uitbater.fiche", event_id=ev.id))


@bp.route("/machtiging/<int:mid>/intrek", methods=["POST"])
@operator_required
def machtiging_intrek(op, mid):
    from ..models import VerkoperMachtiging
    from datetime import datetime
    m = db.session.get(VerkoperMachtiging, mid)
    if not m or m.operator_id != op.id:
        abort(404)
    m.tot = datetime.utcnow()
    db.session.commit()
    flash("Invulhulp ingetrokken.", "ok")
    return redirect(url_for("uitbater.dashboard"))
