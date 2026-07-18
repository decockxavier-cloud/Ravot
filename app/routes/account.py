"""Account: onboarding (≤60 sec), profiel, feedbackloop, Ravotscore,
interesse delen (opt-in per event), vriendenkoppeling via code,
en de AVG-selfservice: export (JSON) + verwijderen met één knop."""
import json
import re
import secrets
from datetime import datetime, timezone
from functools import wraps

from flask import (Blueprint, Response, abort, flash, redirect, render_template,
                   request, session, url_for)

from ..extensions import db, limiter
from ..models import (CATEGORIES, COST_RANGES, FEEST_SOORTEN,
                      FEESTJE_STATUSSEN, REVIEW_TAGS, TAG_NAAR_VELD, Child,
                      Connection, DagUitstap, DagUitstapItem, Event, Family,
                      Feestje, FeestjeAanvraag, FriendInvite, Interaction,
                      Interest, Review, SavedEvent, Share, get_bool, get_int)
from ..scoring import adjust_weight
from .. import punten as pas

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


def _geboortejaren(form):
    """Geboortejaren uit het formulier (nieuw), met terugval op de oude
    leeftijd-velden (verouderde/gecachte PWA-formulieren). Gevalideerd op
    0-17 jaar, max. 12 kinderen."""
    huidig = datetime.now(timezone.utc).year
    jaren = []
    for j in form.getlist("birth_year"):
        j = j.strip()
        if j.isdigit() and huidig - 17 <= int(j) <= huidig:
            jaren.append(int(j))
    for a in form.getlist("age"):          # legacy-terugval
        a = a.strip()
        if a.isdigit() and 0 <= int(a) <= 17:
            jaren.append(huidig - int(a))
    return jaren[:12]


# ---------------------------------------------------------------- onboarding --

@bp.route("/start", methods=["GET", "POST"])
def onboarding():
    email = session.get("pending_email")
    if not email and not session.get("family_id"):
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        jaren = _geboortejaren(request.form)
        postcode = re.sub(r"\D", "", request.form.get("postcode", ""))[:4]
        if not jaren or len(postcode) != 4:
            flash("Vul minstens één geboortejaar en je postcode in.", "error")
            return render_template("account/onboarding.html", categories=CATEGORIES,
                                   current_year=datetime.now(timezone.utc).year,
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
        for jaar in jaren:
            db.session.add(Child(family_id=fam.id, birth_year=jaar))
        for cat in request.form.getlist("interest"):
            if cat in CATEGORIES:
                db.session.add(Interest(family_id=fam.id, category=cat, weight=1.3))
        db.session.commit()
        session.pop("pending_email", None)
        session["family_id"] = fam.id
        session.permanent = True
        return redirect(url_for("public.vandaag"))
    return render_template("account/onboarding.html", categories=CATEGORIES,
                           current_year=datetime.now(timezone.utc).year,
                           title="Welkom bij Ravot", family=None, active=None)


# ------------------------------------------------------------------- profiel --

@bp.route("/")
def mijn_home():
    """Wie /mijn intikt, hoort op zijn dashboard te landen."""
    return redirect(url_for("account.profiel"))


@bp.route("/profiel", methods=["GET", "POST"])
@login_required
def profiel():
    """Mijn Ravot — dashboard met bewaard, te reviewen, scores en vrienden."""
    fam = me()
    if request.method == "POST":
        fam.postcode = re.sub(r"\D", "", request.form.get("postcode", fam.postcode or ""))[:4] or fam.postcode
        fam.radius_km = int(request.form.get("radius", fam.radius_km))
        fam.budget_pref = request.form.get("budget", fam.budget_pref)
        fam.newsletter_opt_in = request.form.get("newsletter") == "on"
        fam.monday_opt_in = request.form.get("monday") == "on"
        fam.display_name = (request.form.get("display_name") or "").strip()[:80] or None
        Child.query.filter_by(family_id=fam.id).delete()
        for jaar in _geboortejaren(request.form):
            db.session.add(Child(family_id=fam.id, birth_year=jaar))
        fam.gegevens_nagekeken = True
        db.session.commit()
        flash("Profiel bewaard.", "ok")
        return redirect(url_for("account.instellingen"))

    now = datetime.utcnow()
    # Bewaarde activiteiten (komende eerst)
    saved_rows = SavedEvent.query.filter_by(family_id=fam.id) \
        .order_by(SavedEvent.created_at.desc()).all()
    bewaard = [s.event for s in saved_rows if s.event]
    bewaard_komend = [e for e in bewaard if not e.start or e.start >= now]
    bewaard_voorbij = [e for e in bewaard if e.start and e.start < now]

    # Te reviewen: bewaarde events die al voorbij zijn en nog geen review kregen
    reeds = {r.event_id for r in Review.query.filter_by(family_id=fam.id)}
    te_reviewen = [e for e in bewaard_voorbij if e.id not in reeds]

    # Eigen gegeven Ravotscores
    mijn_reviews = Review.query.filter_by(family_id=fam.id) \
        .order_by(Review.created_at.desc()).limit(20).all()

    # Vrienden
    from ..models import Connection
    conns = Connection.query.filter(
        db.or_(Connection.family_a == fam.id, Connection.family_b == fam.id)).all()

    daguitstappen_n = DagUitstap.query.filter_by(family_id=fam.id).count()
    feestjes_open = Feestje.query.filter_by(family_id=fam.id, status="open").count()

    return render_template("account/mijn_ravot.html", family=fam,
                           bewaard_komend=bewaard_komend, bewaard_voorbij=bewaard_voorbij,
                           te_reviewen=te_reviewen, mijn_reviews=mijn_reviews,
                           aantal_vrienden=len(conns),
                           daguitstappen_n=daguitstappen_n,
                           feestjes_open=feestjes_open,
                           title="Mijn Ravot", active="profiel")


@bp.route("/instellingen", methods=["GET", "POST"])
@login_required
def instellingen():
    """Gezinsinstellingen (postcode, kinderen, mails). Los van het dashboard."""
    fam = me()
    if request.method == "POST":
        fam.postcode = re.sub(r"\D", "", request.form.get("postcode", fam.postcode or ""))[:4] or fam.postcode
        fam.radius_km = int(request.form.get("radius", fam.radius_km))
        fam.budget_pref = request.form.get("budget", fam.budget_pref)
        fam.newsletter_opt_in = request.form.get("newsletter") == "on"
        fam.monday_opt_in = request.form.get("monday") == "on"
        fam.display_name = (request.form.get("display_name") or "").strip()[:80] or None
        Child.query.filter_by(family_id=fam.id).delete()
        for jaar in _geboortejaren(request.form):
            db.session.add(Child(family_id=fam.id, birth_year=jaar))
        fam.gegevens_nagekeken = True
        db.session.commit()
        flash("Instellingen bewaard.", "ok")
        return redirect(url_for("account.instellingen"))
    return render_template("account/instellingen.html", family=fam,
                           categories=CATEGORIES, current_year=datetime.now(timezone.utc).year,
                           title="Gezinsinstellingen", active="profiel")


# ------------------------------------------------------- feedback & bewaren --

@bp.route("/feedback/<int:event_id>/<verdict>", methods=["POST"])
@login_required
@limiter.limit("60/hour")
def feedback(event_id, verdict):
    if verdict not in ("like", "dismiss"):
        abort(400)
    fam = me()
    ev = db.session.get(Event, event_id) or abort(404)
    if verdict == "like":
        bestaand = Interaction.query.filter_by(
            family_id=fam.id, event_id=event_id, type="like").first()
        if bestaand:  # al leuk → toggle uit
            db.session.delete(bestaand)
            db.session.commit()
            flash("Niet meer als leuk gemarkeerd.", "ok")
            return redirect(request.referrer or url_for("public.vandaag"))
    # smaakprofiel bijwerken
    for cat in ev.categories or []:
        interest = Interest.query.filter_by(family_id=fam.id, category=cat).first()
        if interest is None:
            interest = Interest(family_id=fam.id, category=cat, weight=1.0)
            db.session.add(interest)
        interest.weight = adjust_weight(interest.weight, liked=(verdict == "like"))
    db.session.add(Interaction(family_id=fam.id, event_id=event_id, type=verdict))
    db.session.commit()
    if verdict == "like":
        flash("Leuk gevonden! We tonen je meer van dit soort activiteiten. 💚", "ok")
    else:
        flash("Genoteerd — dit tonen we je minder. Je vindt het niet meer in je suggesties.", "ok")
    return redirect(request.referrer or url_for("public.vandaag"))


@bp.route("/bewaar/<int:event_id>", methods=["POST"])
@login_required
@limiter.limit("60/hour")
def save(event_id):
    fam = me()
    existing = SavedEvent.query.filter_by(family_id=fam.id, event_id=event_id).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        flash("Uit je bewaarde activiteiten gehaald.", "ok")
    else:
        db.session.add(SavedEvent(family_id=fam.id, event_id=event_id))
        db.session.add(Interaction(family_id=fam.id, event_id=event_id, type="save"))
        db.session.commit()
        flash("Bewaard! Terug te vinden onder Mijn Ravot → Bewaard. ❤️", "ok")
    return redirect(request.referrer or url_for("public.vandaag"))


# ---------------------------------------------------------------- Ravotscore --

@bp.route("/geweest/<int:event_id>", methods=["POST"])
@login_required
@limiter.limit("60/hour")
def geweest(event_id):
    """Gezin bevestigt dat ze bij het event waren → daarna pas scoorbaar."""
    fam = me()
    ev = db.session.get(Event, event_id) or abort(404)
    sv = SavedEvent.query.filter_by(family_id=fam.id, event_id=event_id).first()
    if sv is None:  # niet bewaard? dan toch aanmaken zodat we de status kennen
        sv = SavedEvent(family_id=fam.id, event_id=event_id, wil_heen=False)
        db.session.add(sv)
    antwoord = request.form.get("antwoord")
    sv.gevraagd_geweest = True
    if antwoord == "ja":
        sv.geweest = True
        extra = pas.ken_toe(fam.id, "geweest", event_id)
        db.session.commit()
        if extra:
            flash(f"🦊 +{extra} ravotpunten — nieuwe stempel op jullie Ravotpas!", "ok")
        return redirect(url_for("account.review", event_id=event_id))
    sv.geweest = False
    db.session.commit()
    flash("Geen probleem — misschien een andere keer!", "ok")
    return redirect(request.referrer or url_for("account.profiel"))


@bp.route("/review/<int:event_id>", methods=["GET", "POST"])
@login_required
@limiter.limit("30/hour", methods=["POST"])
def review(event_id):
    fam = me()
    ev = db.session.get(Event, event_id) or abort(404)
    existing = Review.query.filter_by(family_id=fam.id, event_id=event_id).first()
    # Wraak-preventie: enkel scoorbaar als het gezin bevestigd "geweest" is.
    sv = SavedEvent.query.filter_by(family_id=fam.id, event_id=event_id).first()
    mag_scoren = existing is not None or (sv is not None and sv.geweest)
    if not mag_scoren:
        # Nog niet bevestigd geweest → vraag dat eerst.
        return render_template("account/geweest.html", ev=ev, family=fam,
                               title="Waren jullie erbij?", active=None)
    if request.method == "POST":
        if existing:  # één per gezin per event — score niet kapot te trekken
            flash("Jullie gaven dit al een Ravotscore. Bedankt!", "ok")
            return redirect(url_for("public.event", slug=ev.slug))
        kid = int(request.form.get("kid_score", 0))
        parent = int(request.form.get("parent_score", 0))
        if not (1 <= kid <= 5 and 1 <= parent <= 3):
            abort(400)

        def _schuif(naam):
            v = request.form.get(naam)
            if v and v.isdigit() and 1 <= int(v) <= 5:
                return int(v)
            return None

        tags = [t for t in request.form.getlist("tag") if t in REVIEW_TAGS][:5]
        db.session.add(Review(
            family_id=fam.id, event_id=event_id, kid_score=kid, parent_score=parent,
            sfeer_rustig_actief=_schuif("sfeer_rustig_actief"),
            sfeer_prijs=_schuif("sfeer_prijs"),
            sfeer_leeftijd=_schuif("sfeer_leeftijd"),
            tags=tags, child_ages=fam.child_ages(),
        ))
        db.session.add(Interaction(family_id=fam.id, event_id=event_id, type="review"))
        extra = pas.ken_toe(fam.id, "review", event_id)
        _tags_naar_velden(ev, tags)
        db.session.commit()
        boodschap = "Ravotscore bewaard — bedankt om andere gezinnen te helpen! 🌟"
        if extra:
            boodschap += f" (+{extra} ravotpunten 🦊)"
        flash(boodschap, "ok")
        return redirect(url_for("public.event", slug=ev.slug))
    return render_template("account/review.html", ev=ev, existing=existing,
                           tags=REVIEW_TAGS,
                           family=fam, title=f"Ravotscore voor {ev.title}", active=None)


def _tags_naar_velden(ev, nieuwe_tags):
    """Community vult de ouder-filters: zodra genoeg gezinnen dezelfde
    praktische tag geven (verzorgingstafel, buggy, omheind), zetten we het
    fiche-veld aan. Nooit uit — enkel positieve bevestiging telt."""
    drempel = get_int("tag_drempel", 2) or 2
    relevant = set(nieuwe_tags) & set(TAG_NAAR_VELD)
    if not relevant:
        return
    alle = Review.query.filter_by(event_id=ev.id).all()
    for tag in relevant:
        veld = TAG_NAAR_VELD[tag]
        if getattr(ev, veld, None):
            continue
        # +1 voor de review die nu wordt toegevoegd (zit nog niet in `alle`)
        n = 1 + sum(1 for r in alle if tag in (r.tags or []))
        if n >= drempel:
            setattr(ev, veld, True)


@bp.route("/gegevens-ok", methods=["POST"])
@login_required
def gegevens_ok():
    """Eénmalige bevestiging na de overstap naar geboortejaren: 'klopt al'."""
    fam = me()
    fam.gegevens_nagekeken = True
    db.session.commit()
    flash("Top, bedankt om het na te kijken! 🦊", "ok")
    return redirect(request.referrer or url_for("account.mijn_home"))


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
        code = re.sub(r"[^A-Z0-9]", "", request.form.get("code", "").strip().upper())
        other_id = magic_friend_lookup(code)
        if other_id is None or other_id == fam.id:
            flash("Die code klopt niet of is verlopen. Vraag een nieuwe aan je vriend.", "error")
        else:
            a, b_ = sorted((fam.id, other_id))
            existing = Connection.query.filter_by(family_a=a, family_b=b_).first()
            if existing and existing.status == "accepted":
                flash("Jullie zijn al gekoppeld.", "ok")
            elif existing and existing.status == "pending":
                # De andere partij had al aangevraagd → dit ís de wederzijdse bevestiging
                if existing.requested_by != fam.id:
                    existing.status = "accepted"
                    db.session.commit()
                    flash("Gekoppeld! Jullie zijn nu vrienden. 🎉", "ok")
                else:
                    flash("Je aanvraag loopt al. Wacht tot het andere gezin bevestigt.", "ok")
            else:
                # Nieuwe aanvraag: pending tot de ander ook koppelt
                db.session.add(Connection(family_a=a, family_b=b_,
                                          status="pending", requested_by=fam.id))
                db.session.commit()
                flash("Aanvraag verstuurd! Zodra het andere gezin jouw code invoert, "
                      "zijn jullie gekoppeld.", "ok")
        return redirect(url_for("account.friends"))

    # Actieve vrienden + openstaande aanvragen tonen
    conns = Connection.query.filter(
        (Connection.family_a == fam.id) | (Connection.family_b == fam.id)).all()
    vrienden, inkomend, uitgaand = [], [], []
    for c in conns:
        other = db.session.get(Family, c.family_b if c.family_a == fam.id else c.family_a)
        naam = other.display_name or "Een gezin"
        if c.status == "accepted":
            vrienden.append(naam)
        elif c.requested_by == fam.id:
            uitgaand.append(naam)
        else:
            inkomend.append(naam)
    return render_template("account/vrienden.html", family=fam, friends=vrienden,
                           inkomend=inkomend, uitgaand=uitgaand,
                           invite_code=nieuwe_vriendcode(fam.id),
                           title="Vrienden", active="profiel")


def nieuwe_vriendcode(family_id):
    """Genereer een verse, willekeurige code die 24u geldig is. Enkel de hash
    wordt bewaard, gekoppeld aan het gezin. Niet te raden, niet voorspelbaar."""
    from ..models import FriendInvite
    from datetime import datetime, timezone, timedelta
    # Bestaande geldige code hergebruiken zodat de pagina stabiel blijft bij refresh
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    code = _leesbare_code()
    db.session.add(FriendInvite(
        family_id=family_id, code_hash=_hash_friend(code),
        expires_at=now + timedelta(hours=24)))
    db.session.commit()
    return code


def _leesbare_code(n=6):
    alfabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # zonder verwarrende 0/O/1/I
    return "".join(secrets.choice(alfabet) for _ in range(n))


def _hash_friend(code):
    import hashlib
    return hashlib.sha256(f"vriend:{code}".encode()).hexdigest()


def magic_friend_lookup(code):
    """Zoek het gezin achter een geldige, niet-verlopen, ongebruikte code."""
    from ..models import FriendInvite
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    inv = FriendInvite.query.filter_by(code_hash=_hash_friend(code), used_at=None).first()
    if inv is None or inv.expires_at < now:
        return None
    return inv.family_id


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


# ------------------------------------------------ gebruikersbijdragen --

def _slugify(text):
    import unicodedata
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return t[:200] or "plek"


@bp.route("/toevoegen", methods=["GET", "POST"])
@login_required
@limiter.limit("20/hour", methods=["POST"])
def toevoegen():
    """Gebruiker voegt een plek toe die er nog niet is. Gaat naar de
    moderatiewachtrij (pending) — pas zichtbaar na goedkeuring."""
    from ..models import CATEGORIES
    from ..geo import postcode_coord
    fam = db.session.get(Family, session["family_id"])
    if request.method == "POST":
        titel = (request.form.get("titel") or "").strip()[:255]
        if not titel:
            flash("Geef minstens een naam voor de plek.", "error")
            return redirect(url_for("account.toevoegen"))
        postcode = re.sub(r"\D", "", request.form.get("postcode") or "")[:4] or None
        cats = [c for c in request.form.getlist("categorie") if c in CATEGORIES] or ["buiten"]
        try:
            lo = max(0, min(18, int(request.form.get("age_min") or 0)))
            hi = max(lo, min(18, int(request.form.get("age_max") or 12)))
        except ValueError:
            lo, hi = 0, 12
        coord = postcode_coord(postcode) if postcode else None
        ev = Event(
            source="user", pending=True, is_permanent=True, hidden=False,
            submitted_by=fam.id,
            title=titel,
            description=(request.form.get("beschrijving") or "").strip()[:2000],
            gemeente=(request.form.get("gemeente") or "").strip()[:80] or None,
            postcode=postcode,
            adres=(request.form.get("adres") or "").strip()[:255] or None,
            lat=coord[0] if coord else None, lng=coord[1] if coord else None,
            age_min=lo, age_max=hi, categories=cats,
            indoor=bool(request.form.get("indoor")),
            is_free=bool(request.form.get("gratis")),
            source_url=(request.form.get("website") or "").strip()[:500] or None,
            attribution="toegevoegd door een gezin",
            slug=f"{_slugify(titel)}-u{secrets.token_hex(3)}",
        )
        db.session.add(ev)
        db.session.commit()
        flash("Bedankt! Je plek is ingediend en verschijnt zodra we ze nagekeken hebben.", "ok")
        return redirect(url_for("public.vandaag"))
    return render_template("account/toevoegen.html", categories=CATEGORIES,
                           family=fam, active=None, title="Plek toevoegen")


@bp.route("/melden/<int:event_id>", methods=["POST"])
@limiter.limit("10/hour")
def melden(event_id):
    """Meld een plek als gesloten / foute info / ongepast -> naar de admin.
    Mag anoniem (nuttig vanaf de eerste bezoeker); wel rate-limited tegen spam."""
    from ..models import Report, REPORT_REASONS, get_int
    ev = db.session.get(Event, event_id)
    if not ev:
        abort(404)
    reason = request.form.get("reason")
    if reason not in REPORT_REASONS:
        reason = "anders"
    db.session.add(Report(event_id=ev.id, family_id=session.get("family_id"),
                          reason=reason,
                          note=(request.form.get("note") or "").strip()[:500]))
    db.session.flush()
    # Community-bewaking: genoeg onbehandelde meldingen -> een goedgekeurde plek
    # verliest automatisch zijn ✓ en gaat terug naar nazicht, tot jij ze herbekijkt.
    # Anti-misbruik: álle anonieme meldingen samen tellen als één stem; enkel
    # verschillende ingelogde gezinnen tellen elk apart. Zo kan één persoon
    # niet in z'n eentje de gecureerde kern leegtrekken.
    drempel = get_int("report_drempel", 3) or 3
    open_q = Report.query.filter_by(event_id=ev.id, handled=False)
    distinct_fams = (open_q.filter(Report.family_id.isnot(None))
                     .with_entities(Report.family_id).distinct().count())
    heeft_anoniem = open_q.filter(Report.family_id.is_(None)).count() > 0
    stemmen = distinct_fams + (1 if heeft_anoniem else 0)
    if ev.curated and stemmen >= drempel:
        ev.curated = False
        ev.curated_by = None
        ev.curated_at = None
    db.session.commit()
    flash("Bedankt voor je melding — we kijken ernaar.", "ok")
    return redirect(url_for("public.event", slug=ev.slug))


@bp.route("/foto/<int:event_id>", methods=["POST"])
@login_required
@limiter.limit("15/hour", methods=["POST"])
def upload_foto(event_id):
    """Gebruiker uploadt een foto van een plek -> moderatiewachtrij (pending)."""
    from ..models import Photo
    from ..fotos import verwerk_upload
    ev = db.session.get(Event, event_id)
    if not ev:
        abort(404)
    if not request.form.get("akkoord"):
        flash("Vink aan dat je akkoord gaat met het delen van je foto.", "error")
        return redirect(url_for("public.event", slug=ev.slug))
    naam = verwerk_upload(request.files.get("foto"))
    if not naam:
        flash("Dat lijkt geen geldige foto (jpg, png of webp).", "error")
        return redirect(url_for("public.event", slug=ev.slug))
    db.session.add(Photo(event_id=ev.id, family_id=session.get("family_id"),
                         filename=naam, status="pending"))
    db.session.commit()
    flash("Bedankt! Je foto is ingediend en verschijnt zodra we ze nagekeken hebben.", "ok")
    return redirect(url_for("public.event", slug=ev.slug))


# ------------------------------------------------------------- daguitstappen --

def _mijn_daguitstap(uid, fam):
    d = db.session.get(DagUitstap, uid)
    if not d or d.family_id != fam.id:
        abort(404)
    return d


@bp.route("/daguitstappen")
@login_required
def daguitstappen():
    fam = me()
    lijst = DagUitstap.query.filter_by(family_id=fam.id) \
        .order_by(DagUitstap.updated_at.desc()).all()
    return render_template("account/daguitstappen.html", lijst=lijst, family=fam,
                           title="Mijn daguitstappen", active=None)


@bp.route("/daguitstap/nieuw", methods=["POST"])
@login_required
@limiter.limit("20/hour")
def daguitstap_nieuw():
    fam = me()
    titel = (request.form.get("titel") or "").strip()[:120] or "Onze daguitstap"
    datum = None
    if request.form.get("datum"):
        try:
            datum = datetime.strptime(request.form["datum"], "%Y-%m-%d").date()
        except ValueError:
            datum = None
    d = DagUitstap(family_id=fam.id, titel=titel, datum=datum)
    db.session.add(d)
    db.session.flush()
    # Rechtstreeks vanaf een fiche gestart? Voeg die plek meteen toe.
    event_id = request.form.get("event_id")
    if event_id and event_id.isdigit() and db.session.get(Event, int(event_id)):
        db.session.add(DagUitstapItem(daguitstap_id=d.id, event_id=int(event_id),
                                      volgorde=1))
    extra = pas.ken_toe(fam.id, "daguitstap", d.id)
    db.session.commit()
    boodschap = f"Daguitstap '{d.titel}' aangemaakt!"
    if extra:
        boodschap += f" (+{extra} ravotpunten 🦊)"
    flash(boodschap, "ok")
    return redirect(url_for("account.daguitstap", uid=d.id))


@bp.route("/daguitstap/<int:uid>")
@login_required
def daguitstap(uid):
    fam = me()
    d = _mijn_daguitstap(uid, fam)
    return render_template("account/daguitstap.html", d=d, family=fam,
                           title=d.titel, active=None)


@bp.route("/daguitstap/<int:uid>/voeg/<int:event_id>", methods=["POST"])
@login_required
@limiter.limit("60/hour")
def daguitstap_voeg(uid, event_id):
    fam = me()
    d = _mijn_daguitstap(uid, fam)
    ev = db.session.get(Event, event_id) or abort(404)
    if not DagUitstapItem.query.filter_by(daguitstap_id=d.id, event_id=ev.id).first():
        volgende = (db.session.query(db.func.coalesce(
            db.func.max(DagUitstapItem.volgorde), 0))
            .filter_by(daguitstap_id=d.id).scalar() or 0) + 1
        db.session.add(DagUitstapItem(daguitstap_id=d.id, event_id=ev.id,
                                      volgorde=volgende))
        db.session.commit()
        flash(f"'{ev.title}' toegevoegd aan {d.titel}.", "ok")
    return redirect(request.referrer or url_for("account.daguitstap", uid=d.id))


@bp.route("/daguitstap/<int:uid>/item/<int:item_id>/<actie>", methods=["POST"])
@login_required
def daguitstap_item(uid, item_id, actie):
    """Item verwijderen of van plaats wisselen (op/neer) — geen JS nodig."""
    fam = me()
    d = _mijn_daguitstap(uid, fam)
    item = db.session.get(DagUitstapItem, item_id)
    if not item or item.daguitstap_id != d.id:
        abort(404)
    if actie == "weg":
        db.session.delete(item)
    elif actie in ("op", "neer"):
        items = sorted(d.items, key=lambda i: i.volgorde)
        idx = items.index(item)
        ruil = idx - 1 if actie == "op" else idx + 1
        if 0 <= ruil < len(items):
            items[idx].volgorde, items[ruil].volgorde = \
                items[ruil].volgorde, items[idx].volgorde
    else:
        abort(400)
    # Nota bijwerken mag altijd meekomen
    db.session.commit()
    return redirect(url_for("account.daguitstap", uid=d.id))


@bp.route("/daguitstap/<int:uid>/deel", methods=["POST"])
@login_required
def daguitstap_deel(uid):
    fam = me()
    d = _mijn_daguitstap(uid, fam)
    if d.share_token:
        d.share_token = None      # delen weer uitzetten
        flash("Deellink uitgeschakeld — enkel jullie zien deze daguitstap nog.", "ok")
    else:
        d.share_token = secrets.token_urlsafe(12)[:24]
        flash("Deellink aangemaakt! Kopieer de link en stuur ze door.", "ok")
    db.session.commit()
    return redirect(url_for("account.daguitstap", uid=d.id))


@bp.route("/daguitstap/<int:uid>/verwijder", methods=["POST"])
@login_required
def daguitstap_verwijder(uid):
    fam = me()
    d = _mijn_daguitstap(uid, fam)
    db.session.delete(d)
    db.session.commit()
    flash("Daguitstap verwijderd.", "ok")
    return redirect(url_for("account.daguitstappen"))


# ------------------------------------------------------------------ feestjes --

@bp.route("/feestjes")
@login_required
def feestjes():
    fam = me()
    lijst = Feestje.query.filter_by(family_id=fam.id) \
        .order_by(Feestje.datum.desc()).all()
    return render_template("account/feestjes.html", lijst=lijst, family=fam,
                           statussen=FEESTJE_STATUSSEN,
                           title="Mijn feestjes", active=None)


@bp.route("/feestje/nieuw", methods=["GET", "POST"])
@login_required
@limiter.limit("10/hour", methods=["POST"])
def feestje_nieuw():
    """Stap 1 van de wizard: de feestgegevens. Leeftijd rekenen we uit het
    geboortejaar van het gekozen kind — nooit meer manueel aanpassen."""
    fam = me()
    if not get_bool("feestjes_aan"):
        abort(404)
    kinderen = Child.query.filter_by(family_id=fam.id).all()
    if request.method == "POST":
        try:
            datum = datetime.strptime(request.form.get("datum", ""), "%Y-%m-%d").date()
        except ValueError:
            flash("Kies een geldige feestdatum.", "error")
            return redirect(url_for("account.feestje_nieuw"))
        leeftijd = None
        kind_id = request.form.get("kind")
        if kind_id and kind_id.isdigit():
            kind = db.session.get(Child, int(kind_id))
            if kind and kind.family_id == fam.id:
                leeftijd = datum.year - kind.birth_year
        if leeftijd is None and (request.form.get("leeftijd") or "").isdigit():
            leeftijd = int(request.form["leeftijd"])
        try:
            aantal = max(1, min(60, int(request.form.get("aantal", 8))))
        except ValueError:
            aantal = 8
        postcode = re.sub(r"\D", "", request.form.get("postcode") or fam.postcode)[:4]
        f = Feestje(family_id=fam.id, leeftijd=leeftijd, datum=datum,
                    aantal_kinderen=aantal, postcode=postcode,
                    gemeente=(request.form.get("gemeente") or "").strip()[:80] or None,
                    straal_km=get_int("feest_straal_km", 20) or 20,
                    budget=(request.form.get("budget") or "").strip()[:12] or None,
                    wensen=(request.form.get("wensen") or "").strip()[:600] or None)
        db.session.add(f)
        db.session.commit()
        return redirect(url_for("account.feestje_partners", fid=f.id))
    vandaag = datetime.utcnow().date()
    return render_template("account/feestje_nieuw.html", family=fam,
                           kinderen=kinderen, vandaag=vandaag,
                           soorten=FEEST_SOORTEN,
                           title="Verjaardagsfeestje plannen", active=None)


def _mijn_feestje(fid, fam):
    f = db.session.get(Feestje, fid)
    if not f or f.family_id != fam.id:
        abort(404)
    return f


@bp.route("/feestje/<int:fid>/partners", methods=["GET", "POST"])
@login_required
@limiter.limit("10/hour", methods=["POST"])
def feestje_partners(fid):
    """Stap 2: passende partners in de buurt aanvinken en offertes versturen."""
    from ..services import feestjes as fs
    fam = me()
    f = _mijn_feestje(fid, fam)
    gewenst = request.args.getlist("soort") or request.form.getlist("soort") or None
    partners = fs.zoek_partners(f.postcode, f.straal_km, gewenst)
    al_gevraagd = {a.event_id for a in f.aanvragen}
    if request.method == "POST":
        maxi = get_int("feest_max_aanvragen", 6) or 6
        gekozen = [int(x) for x in request.form.getlist("partner") if x.isdigit()]
        toegestaan = {p["event"].id: p for p in partners}
        verzonden = 0
        for eid in gekozen:
            if eid in al_gevraagd or eid not in toegestaan or verzonden >= maxi:
                continue
            ev = toegestaan[eid]["event"]
            try:
                ok = fs.stuur_offerte(f, ev, fam)
            except Exception:
                ok = False
            if ok:
                db.session.add(FeestjeAanvraag(feestje_id=f.id, event_id=eid))
                verzonden += 1
        if verzonden:
            pas.ken_toe(fam.id, "feestje", f.id)
            db.session.commit()
            flash(f"🎉 {verzonden} offerteaanvra{'gen' if verzonden > 1 else 'ag'} "
                  "verstuurd! Antwoorden komen rechtstreeks in jullie mailbox; "
                  "volg hier de status op.", "ok")
            return redirect(url_for("account.feestje", fid=f.id))
        flash("Geen aanvragen verstuurd — vink minstens één partner aan.", "error")
    return render_template("account/feestje_partners.html", f=f, family=fam,
                           partners=partners, al_gevraagd=al_gevraagd,
                           soorten=FEEST_SOORTEN, gewenst=gewenst or [],
                           title="Kies feestpartners", active=None)


@bp.route("/feestje/<int:fid>")
@login_required
def feestje(fid):
    fam = me()
    f = _mijn_feestje(fid, fam)
    return render_template("account/feestje.html", f=f, family=fam,
                           statussen=FEESTJE_STATUSSEN,
                           title="Mijn feestje", active=None)


@bp.route("/feestje/<int:fid>/aanvraag/<int:aid>", methods=["POST"])
@login_required
def feestje_status(fid, aid):
    """Gezin werkt zelf de status van een aanvraag bij (beantwoord/bevestigd)."""
    fam = me()
    f = _mijn_feestje(fid, fam)
    a = db.session.get(FeestjeAanvraag, aid)
    if not a or a.feestje_id != f.id:
        abort(404)
    status = request.form.get("status")
    if status in FEESTJE_STATUSSEN:
        a.status = status
        if status == "bevestigd":
            f.status = "geregeld"
        db.session.commit()
    return redirect(url_for("account.feestje", fid=f.id))


# ------------------------------------------------------------------ ravotpas --

@bp.route("/ravotpas")
@login_required
def ravotpas():
    """De speelse kant van Ravot: niveau, stempels (verzamelde plekken) en
    badges — leuk voor de kinderen, retentie voor het gezin."""
    fam = me()
    punten_totaal = pas.totaal(fam.id)
    return render_template("account/ravotpas.html", family=fam,
                           niveau=pas.niveau(punten_totaal),
                           stempels=pas.stempelkaart(fam.id),
                           badges=pas.badges(fam.id),
                           title="Onze Ravotpas", active=None)
