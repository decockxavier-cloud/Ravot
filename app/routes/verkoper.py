"""Verkopersportaal (patch 153).

De zelfstandige verkoper logt in met een e-mailcode (zelfde beveiligde
magic-flow als uitbaters) en ziet:
- zijn persoonlijke code + de welkomstpakket-pitch voor bij de klant
- zijn verkopen en opgebouwde commissie per maand (basis voor zijn factuur)
- een prospectielijst: horeca-zaken per gemeente met partnerstatus en het
  aantal vrije partnerplekken (het schaarste-argument)

Bewust NIET in v1: schrijftoegang tot klantfiches. De uitbater is en blijft
de enige die zijn fiche bewerkt (via zijn eigen login, desnoods met de
verkoper ernaast) — dat houdt het mandaat en de AVG-verantwoordelijkheid
zuiver. Een expliciet machtigingssysteem kan later, als er vraag naar is.
"""
import re

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, session, url_for)

from ..extensions import db, limiter
from ..models import Event, PartnerPayment, Verkoper, get_int
from ..services import magic

bp = Blueprint("verkoper", __name__, url_prefix="/verkoper")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _huidige():
    vid = session.get("verkoper_id")
    if not vid:
        return None
    v = db.session.get(Verkoper, vid)
    return v if v and v.actief else None


@bp.route("/", methods=["GET"])
def start():
    v = _huidige()
    if not v:
        return redirect(url_for("verkoper.login"))
    from .. import mollie
    btw = 1 + mollie.btw_pct() / 100
    betalingen = (PartnerPayment.query
                  .filter_by(verkoper_id=v.id, status="paid")
                  .order_by(PartnerPayment.paid_at.desc()).limit(200).all())
    maanden = {}
    for b in betalingen:
        if not b.paid_at:
            continue
        m = b.paid_at.strftime("%Y-%m")
        r = maanden.setdefault(m, {"n": 0, "excl": 0.0, "commissie": 0.0})
        excl = float(b.amount or 0) / btw
        r["n"] += 1
        r["excl"] += excl
        r["commissie"] += excl * (v.commissie_pct / 100)
    overzicht = sorted(maanden.items(), reverse=True)
    from datetime import datetime as _dt
    from ..models import VerkoperMachtiging
    machtigingen = (VerkoperMachtiging.query
                    .filter(VerkoperMachtiging.verkoper_id == v.id,
                            VerkoperMachtiging.tot > _dt.utcnow()).all())
    return render_template("verkoper/dashboard.html", v=v,
                           machtigingen=machtigingen,
                           betalingen=betalingen, overzicht=overzicht,
                           prijzen={p: mollie.prijs(p) for p in mollie.PLANNEN},
                           title="Verkopersportaal", family=None, active=None)


@bp.route("/prospectie")
def prospectie():
    v = _huidige()
    if not v:
        return redirect(url_for("verkoper.login"))
    from .. import mollie
    from ..types import GROEP_SMULLEN
    gemeente = (request.args.get("gemeente") or "").strip()
    zaken, vrij = [], None
    if gemeente:
        zaken = (Event.query
                 .filter(Event.gemeente.ilike(gemeente),
                         Event.is_permanent.is_(True),
                         Event.hidden.is_(False), Event.pending.is_(False),
                         Event.subtype.in_(tuple(GROEP_SMULLEN)))
                 .order_by(Event.quality.desc().nullslast()).limit(60).all())
        if zaken:
            gemeente = zaken[0].gemeente        # nette hoofdletters
        cap_z = get_int("cap_zichtbaar_gemeente", 4)
        cap_f = get_int("cap_feest_gemeente", 0)
        vrij = {"zichtbaar": max(0, cap_z - mollie.plekken_bezet(gemeente, "zichtbaar"))
                             if cap_z else None,
                "feest": max(0, cap_f - mollie.plekken_bezet(gemeente, "feest"))
                         if cap_f else None}
    return render_template("verkoper/prospectie.html", v=v, gemeente=gemeente,
                           zaken=zaken, vrij=vrij, is_partner=mollie.is_partner,
                           title="Prospectie", family=None, active=None)


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("20/hour")
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        v = Verkoper.query.filter_by(email=email, actief=True).first()
        if not EMAIL_RE.match(email) or not v:
            flash("Dat adres kennen we niet als actieve verkoper.", "error")
            return render_template("verkoper/login.html", title="Verkoperslogin",
                                   family=None, active=None)
        if magic.recent_requests(email) >= current_app.config["MAGIC_REQUESTS_PER_HOUR"]:
            flash("Er zijn al codes verstuurd — kijk in je mailbox (ook spam).", "error")
            return render_template("verkoper/login.html", title="Verkoperslogin",
                                   family=None, active=None)
        code = magic.issue_code(email, purpose="verkoper")
        magic.send_mail(email, f"Jouw Ravot-verkoperscode: {code}",
                        render_template("mail/inlogcode.html", code=code),
                        text=f"Jouw inlogcode voor het Ravot-verkopersportaal is {code}. "
                             f"Ze is 15 minuten geldig.")
        session["verkoper_code_email"] = email
        return render_template("verkoper/code.html", email=email,
                               title="Voer je code in", family=None, active=None)
    return render_template("verkoper/login.html", title="Verkoperslogin",
                           family=None, active=None)


@bp.route("/code", methods=["POST"])
@limiter.limit("30/hour")
def code():
    email = session.get("verkoper_code_email") or ""
    ingave = (request.form.get("code") or "").strip()
    if not email or not magic.verify_code(email, ingave, purpose="verkoper"):
        flash("Die code klopt niet (meer). Vraag een nieuwe aan.", "error")
        return redirect(url_for("verkoper.login"))
    v = Verkoper.query.filter_by(email=email, actief=True).first()
    if not v:
        abort(403)
    session.pop("verkoper_code_email", None)
    session["verkoper_id"] = v.id
    session.permanent = True
    return redirect(url_for("verkoper.start"))


@bp.route("/uitloggen", methods=["POST"])
def uitloggen():
    session.pop("verkoper_id", None)
    flash("Je bent uitgelogd.", "ok")
    return redirect(url_for("verkoper.login"))


def _machtiging(v, event_id):
    from datetime import datetime
    from ..models import VerkoperMachtiging
    return (VerkoperMachtiging.query
            .filter(VerkoperMachtiging.verkoper_id == v.id,
                    VerkoperMachtiging.event_id == event_id,
                    VerkoperMachtiging.tot > datetime.utcnow()).first())


@bp.route("/fiche/<int:event_id>", methods=["GET", "POST"])
@limiter.limit("60/hour")
def fiche(event_id):
    """Invulhulp (patch 155): de gemachtigde verkoper vult de kernvelden van
    de fiche mee in. Wijzigingen volgen exact dezelfde weg als die van de
    uitbater zelf (wachtrij of auto-toepassen), mét verkoper-label."""
    v = _huidige()
    if not v:
        return redirect(url_for("verkoper.login"))
    m = _machtiging(v, event_id)
    if not m:
        abort(403)
    ev = db.session.get(Event, event_id) or abort(404)
    from ..models import EditProposal, EDIT_VELDEN, get_bool
    from ..types import TYPES
    from ..services.openingsuren import DAGEN, DAG_LABELS, parse_dagtekst, dag_tekst

    if request.method == "POST":
        wijzigingen = {}
        besch = (request.form.get("beschrijving") or "").strip()[:2000]
        if besch and besch != (ev.description or ""):
            wijzigingen["description"] = besch
        soort = request.form.get("soort") or ""
        if soort in TYPES and soort != (ev.subtype or ""):
            wijzigingen["subtype"] = soort
        uren, anders = {}, False
        for dag in DAGEN:
            w, _ok = parse_dagtekst(request.form.get(f"uren_{dag}"))
            uren[dag] = w
            if dag_tekst(w) != dag_tekst((ev.openingsuren or {}).get(dag)):
                anders = True
        if anders and any(uren.values()):
            wijzigingen["openingsuren"] = uren
        for veld in ("kinderstoel", "speelhoek", "kindermenu", "verzorgingstafel",
                     "buggy_ok", "omheind", "terras", "parking", "toegankelijk"):
            nieuw = request.form.get(veld) == "1"
            if nieuw != bool(getattr(ev, veld)):
                wijzigingen[veld] = nieuw
        try:
            n_lat = float(request.form.get("lat") or "")
            n_lng = float(request.form.get("lng") or "")
        except ValueError:
            n_lat = None
        if n_lat is not None and (ev.lat is None or abs(n_lat - ev.lat) > 1e-6):
            wijzigingen["lat"], wijzigingen["lng"] = n_lat, n_lng
        if not wijzigingen:
            flash("Geen wijzigingen gevonden.", "error")
            return redirect(url_for("verkoper.fiche", event_id=ev.id))
        voorstel = EditProposal(operator_id=m.operator_id, event_id=ev.id,
                                verkoper_id=v.id, changes=wijzigingen)
        if get_bool("uitbater_auto_ok"):
            for veld, waarde in wijzigingen.items():
                if veld in EDIT_VELDEN:
                    setattr(ev, veld, waarde)
            from ..kwaliteit import bereken_kwaliteit
            ev.quality = bereken_kwaliteit(ev)
            voorstel.status = "approved"
        db.session.add(voorstel)
        db.session.commit()
        flash("Bewaard — bedankt om de fiche mee op punt te zetten!", "ok")
        return redirect(url_for("verkoper.fiche", event_id=ev.id))

    uren_nu = {dag: dag_tekst((ev.openingsuren or {}).get(dag)) for dag in DAGEN}
    soorten = {k: t for k, t in TYPES.items() if not k.startswith(("ev_", "uit_"))}
    return render_template("verkoper/fiche.html", v=v, ev=ev, m=m,
                           soorten=soorten, dagen=DAGEN, dag_labels=DAG_LABELS,
                           uren_nu=uren_nu, title=f"Invulhulp: {ev.title}",
                           family=None, active=None)
