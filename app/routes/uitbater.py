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
    return {"maand": _f("partner_prijs_maand", 15),
            "jaar": _f("partner_prijs_jaar", 150)}


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
    claims = OperatorClaim.query.filter_by(operator_id=op.id) \
        .order_by(OperatorClaim.created_at.desc()).all()
    voorstellen = EditProposal.query.filter_by(operator_id=op.id) \
        .order_by(EditProposal.created_at.desc()).limit(20).all()
    # Statistieken per Partner-zaak (weergaven / bewaard / doorgeklikt).
    stats = {}
    for c in claims:
        if c.status == "approved" and c.event and mollie.is_partner(c.event):
            rijen = db.session.query(Interaction.type, db.func.count(Interaction.id)) \
                .filter(Interaction.event_id == c.event_id) \
                .group_by(Interaction.type).all()
            per = dict(rijen)
            stats[c.event_id] = {"views": per.get("view", 0),
                                 "saves": per.get("save", 0),
                                 "clicks": per.get("click", 0)}
    return render_template("uitbater/dashboard.html", op=op, claims=claims,
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
        db.session.add(OperatorClaim(
            operator_id=op.id, event_id=ev.id,
            note=(request.form.get("note") or "").strip()[:500]))
        db.session.commit()
        flash("Claim ingediend! We kijken ze na — je krijgt toegang zodra ze is goedgekeurd.", "ok")
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
        for veld in ("description", "adres", "gemeente", "source_url"):
            waarde = (request.form.get(veld) or "").strip()
            if waarde and waarde != (getattr(ev, veld) or ""):
                maxlen = {"description": 2000, "adres": 255,
                          "gemeente": 80, "source_url": 500}[veld]
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
        if not wijzigingen:
            flash("Geen wijzigingen gevonden.", "error")
            return redirect(url_for("uitbater.fiche", event_id=ev.id))
        db.session.add(EditProposal(operator_id=op.id, event_id=ev.id,
                                    changes=wijzigingen))
        db.session.commit()
        flash("Wijziging ingediend! Ze verschijnt op de fiche zodra ze is nagekeken.", "ok")
        return redirect(url_for("uitbater.dashboard"))
    return render_template("uitbater/fiche.html", ev=ev, title=f"Fiche: {ev.title}",
                           family=None, active=None)


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
        if not mollie.actief():
            flash("Online betalen is nog niet geconfigureerd. Mail info@ravot.be "
                  "om Partner te worden.", "error")
            return redirect(url_for("uitbater.partner", event_id=ev.id))
        plan = request.form.get("plan")
        if plan not in ("maand", "jaar"):
            plan = "maand"
        betaling = PartnerPayment(operator_id=op.id, event_id=ev.id, plan=plan,
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
    return render_template("uitbater/partner.html", ev=ev,
                           founding_aan=f_aan, founding_over=max(0, f_max - f_bezet),
                           al_founding=al_founding,
                           prijs_maand=mollie.prijs("maand"),
                           prijs_jaar=mollie.prijs("jaar"),
                           prijs_maand_incl=mollie.prijs_incl("maand"),
                           prijs_jaar_incl=mollie.prijs_incl("jaar"),
                           heeft_facturatie=bool(op.bedrijfsnaam and op.btw_nummer),
                           betalen_actief=mollie.actief(),
                           is_partner=mollie.is_partner(ev),
                           title="Word Ravot Partner", family=None, active=None)


@bp.route("/partner/klaar/<int:pid>")
@operator_required
def partner_klaar(op, pid):
    """Terug van de Mollie-checkout: status tonen (webhook doet het echte werk)."""
    from ..models import PartnerPayment
    from .. import mollie
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
