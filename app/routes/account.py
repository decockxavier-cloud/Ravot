"""Account: onboarding (≤60 sec), profiel, feedbackloop, Ravotscore,
interesse delen (opt-in per event), vriendenkoppeling via code,
en de AVG-selfservice: export (JSON) + verwijderen met één knop."""
import json
import secrets
from datetime import datetime, timezone
from functools import wraps

from flask import (Blueprint, Response, abort, flash, redirect, render_template,
                   request, session, url_for)

from ..extensions import db, limiter
from ..models import (CATEGORIES, COST_RANGES, REVIEW_TAGS, Child, Connection,
                      Event, Family, Interaction, Interest, Review, SavedEvent,
                      Share)
from ..scoring import adjust_weight

bp = Blueprint("account", __name__, url_prefix="/mijn")


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("family_id"):
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return wrapper


def me():
    return db.session.get(Family, session["family_id"])


# ---------------------------------------------------------------- onboarding --

@bp.route("/start", methods=["GET", "POST"])
def onboarding():
    email = session.get("pending_email")
    if not email and not session.get("family_id"):
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        years = [int(y) for y in request.form.getlist("birth_year") if y.strip().isdigit()]
        postcode = request.form.get("postcode", "").strip()[:4]
        if not years or len(postcode) != 4:
            flash("Vul minstens één geboortejaar en je postcode in.", "error")
            return render_template("account/onboarding.html", categories=CATEGORIES,
                                   title="Welkom bij Ravot", family=None, active=None)
        fam = Family(
            email=email,
            postcode=postcode,
            radius_km=int(request.form.get("radius", 25)),
            budget_pref=request.form.get("budget", "all"),
            newsletter_opt_in=request.form.get("newsletter") == "on",  # expliciete opt-in
            display_name=(request.form.get("display_name") or "").strip()[:80] or None,
        )
        db.session.add(fam)
        db.session.flush()
        current_year = datetime.now(timezone.utc).year
        for y in years[:6]:
            if 1900 < y <= current_year:
                db.session.add(Child(family_id=fam.id, birth_year=y))
        for cat in request.form.getlist("interest"):
            if cat in CATEGORIES:
                db.session.add(Interest(family_id=fam.id, category=cat, weight=1.3))
        db.session.commit()
        session.pop("pending_email", None)
        session["family_id"] = fam.id
        session.permanent = True
        return redirect(url_for("public.vandaag"))
    return render_template("account/onboarding.html", categories=CATEGORIES,
                           title="Welkom bij Ravot", family=None, active=None)


# ------------------------------------------------------------------- profiel --

@bp.route("/profiel", methods=["GET", "POST"])
@login_required
def profiel():
    fam = me()
    if request.method == "POST":
        fam.postcode = request.form.get("postcode", fam.postcode).strip()[:4]
        fam.radius_km = int(request.form.get("radius", fam.radius_km))
        fam.budget_pref = request.form.get("budget", fam.budget_pref)
        fam.newsletter_opt_in = request.form.get("newsletter") == "on"
        fam.monday_opt_in = request.form.get("monday") == "on"
        fam.display_name = (request.form.get("display_name") or "").strip()[:80] or None
        Child.query.filter_by(family_id=fam.id).delete()
        current_year = datetime.now(timezone.utc).year
        for y in request.form.getlist("birth_year"):
            if y.strip().isdigit() and 1900 < int(y) <= current_year:
                db.session.add(Child(family_id=fam.id, birth_year=int(y)))
        db.session.commit()
        flash("Profiel bewaard.", "ok")
        return redirect(url_for("account.profiel"))
    saved = SavedEvent.query.filter_by(family_id=fam.id).order_by(SavedEvent.created_at.desc()).all()
    return render_template("account/profiel.html", family=fam, saved=saved,
                           categories=CATEGORIES, title="Mijn Ravot", active="profiel")


# ------------------------------------------------------- feedback & bewaren --

@bp.route("/feedback/<int:event_id>/<verdict>", methods=["POST"])
@login_required
@limiter.limit("60/hour")
def feedback(event_id, verdict):
    if verdict not in ("like", "dismiss"):
        abort(400)
    fam = me()
    ev = db.session.get(Event, event_id) or abort(404)
    for cat in ev.categories or []:
        interest = Interest.query.filter_by(family_id=fam.id, category=cat).first()
        if interest is None:
            interest = Interest(family_id=fam.id, category=cat, weight=1.0)
            db.session.add(interest)
        interest.weight = adjust_weight(interest.weight, liked=(verdict == "like"))
    db.session.add(Interaction(family_id=fam.id, event_id=event_id, type=verdict))
    db.session.commit()
    return redirect(request.referrer or url_for("public.vandaag"))


@bp.route("/bewaar/<int:event_id>", methods=["POST"])
@login_required
@limiter.limit("60/hour")
def save(event_id):
    fam = me()
    existing = SavedEvent.query.filter_by(family_id=fam.id, event_id=event_id).first()
    if existing:
        db.session.delete(existing)
    else:
        db.session.add(SavedEvent(family_id=fam.id, event_id=event_id))
        db.session.add(Interaction(family_id=fam.id, event_id=event_id, type="save"))
    db.session.commit()
    return redirect(request.referrer or url_for("public.vandaag"))


# ---------------------------------------------------------------- Ravotscore --

@bp.route("/review/<int:event_id>", methods=["GET", "POST"])
@login_required
@limiter.limit("30/hour", methods=["POST"])
def review(event_id):
    fam = me()
    ev = db.session.get(Event, event_id) or abort(404)
    existing = Review.query.filter_by(family_id=fam.id, event_id=event_id).first()
    if request.method == "POST":
        if existing:  # één per gezin per event — score niet kapot te trekken
            flash("Jullie gaven dit al een Ravotscore. Bedankt!", "ok")
            return redirect(url_for("public.event", slug=ev.slug))
        kid = int(request.form.get("kid_score", 0))
        parent = int(request.form.get("parent_score", 0))
        if not (1 <= kid <= 5 and 1 <= parent <= 3):
            abort(400)
        cost = request.form.get("cost_range") or None
        if cost is not None and cost not in COST_RANGES:
            abort(400)
        tags = [t for t in request.form.getlist("tag") if t in REVIEW_TAGS][:5]
        db.session.add(Review(
            family_id=fam.id, event_id=event_id, kid_score=kid, parent_score=parent,
            cost_range=cost, tags=tags, child_ages=fam.child_ages(),
        ))
        db.session.add(Interaction(family_id=fam.id, event_id=event_id, type="review"))
        db.session.commit()
        flash("Ravotscore bewaard — bedankt om andere gezinnen te helpen!", "ok")
        return redirect(url_for("public.event", slug=ev.slug))
    return render_template("account/review.html", ev=ev, existing=existing,
                           tags=REVIEW_TAGS, cost_ranges=COST_RANGES,
                           family=fam, title=f"Ravotscore voor {ev.title}", active=None)


# ------------------------------------------------- delen (opt-in, per event) --

@bp.route("/deel/<int:event_id>", methods=["POST"])
@login_required
def share(event_id):
    """Standaard bestaat er géén Share-rij (= niet gedeeld). Deze toggle is de
    enige weg naar delen — bewust, per event."""
    fam = me()
    existing = Share.query.filter_by(family_id=fam.id, event_id=event_id).first()
    if existing:
        db.session.delete(existing)
        flash("Interesse niet langer gedeeld.", "ok")
    else:
        db.session.add(Share(family_id=fam.id, event_id=event_id))
        flash("Interesse gedeeld met je vrienden.", "ok")
    db.session.commit()
    ev = db.session.get(Event, event_id)
    return redirect(url_for("public.event", slug=ev.slug))


# --------------------------------------------------------- vrienden koppelen --

@bp.route("/vrienden", methods=["GET", "POST"])
@login_required
def friends():
    fam = me()
    if request.method == "POST":
        code = request.form.get("code", "").strip().upper()
        other = Family.query.filter(Family.id == _decode_invite(code)).first() if code else None
        if other is None or other.id == fam.id:
            flash("Die code klopt niet.", "error")
        else:
            a, b_ = sorted((fam.id, other.id))
            if not Connection.query.filter_by(family_a=a, family_b=b_).first():
                db.session.add(Connection(family_a=a, family_b=b_))
                db.session.commit()
            flash(f"Gekoppeld met {other.display_name or 'een gezin'}!", "ok")
        return redirect(url_for("account.friends"))
    conns = Connection.query.filter(
        (Connection.family_a == fam.id) | (Connection.family_b == fam.id)).all()
    friends_ = []
    for c in conns:
        other = db.session.get(Family, c.family_b if c.family_a == fam.id else c.family_a)
        friends_.append(other.display_name or "Een gezin")
    return render_template("account/vrienden.html", family=fam, friends=friends_,
                           invite_code=_invite_code(fam.id),
                           title="Vrienden", active=None)


def _invite_code(family_id):
    return f"RAV{family_id:05d}"


def _decode_invite(code):
    return int(code[3:]) if code.startswith("RAV") and code[3:].isdigit() else -1


# --------------------------------------------------------- AVG-selfservice --

@bp.route("/export")
@login_required
def export():
    fam = me()
    data = {
        "email": fam.email, "display_name": fam.display_name,
        "postcode": fam.postcode, "radius_km": fam.radius_km,
        "budget_pref": fam.budget_pref,
        "newsletter_opt_in": fam.newsletter_opt_in,
        "children_birth_years": [c.birth_year for c in fam.children],
        "interests": {i.category: i.weight for i in fam.interests},
        "saved_events": [s.event.title for s in
                         SavedEvent.query.filter_by(family_id=fam.id).all() if s.event],
        "reviews": [{"event_id": r.event_id, "kid_score": r.kid_score,
                     "parent_score": r.parent_score, "tags": r.tags}
                    for r in Review.query.filter_by(family_id=fam.id).all()],
    }
    return Response(json.dumps(data, ensure_ascii=False, indent=2),
                    mimetype="application/json",
                    headers={"Content-Disposition": "attachment; filename=ravot-export.json"})


@bp.route("/verwijderen", methods=["POST"])
@login_required
def delete_account():
    """Harde delete van het profiel. Reviews blijven als reeds-anonieme
    bijdragen, definitief losgekoppeld (family_id kan niet meer herleid worden
    omdat het gezin verdwijnt; de snapshot bevat enkel leeftijden)."""
    fam = me()
    Review.query.filter_by(family_id=fam.id).update({"family_id": None})
    Interaction.query.filter_by(family_id=fam.id).update({"family_id": None})
    Share.query.filter_by(family_id=fam.id).delete()
    SavedEvent.query.filter_by(family_id=fam.id).delete()
    Connection.query.filter(
        (Connection.family_a == fam.id) | (Connection.family_b == fam.id)
    ).delete(synchronize_session=False)
    db.session.delete(fam)
    db.session.commit()
    session.clear()
    flash("Je account en gegevens zijn verwijderd. Tot ziens!", "ok")
    return redirect(url_for("public.vandaag"))
