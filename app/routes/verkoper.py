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
    return render_template("verkoper/dashboard.html", v=v,
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
        vrij = {"zichtbaar": max(0, get_int("cap_zichtbaar_gemeente", 5)
                                 - mollie.plekken_bezet(gemeente, "zichtbaar")),
                "feest": max(0, get_int("cap_feest_gemeente", 3)
                             - mollie.plekken_bezet(gemeente, "feest"))}
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
