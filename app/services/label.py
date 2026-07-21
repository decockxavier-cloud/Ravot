"""Het Ravot-label — een ONKOOPBAAR kwaliteitslabel.

Filosofie: het niveau wordt *verdiend*, niet gekocht. Partnerstatus bepaalt
ranking (waar een zaak in de lijst staat); het label bepaalt kwaliteit (of een
zaak kindvriendelijk genoeg is). Die twee staan streng los van elkaar — een
betalende partner zonder voorzieningen krijgt géén label, een gratis zaak met
alle voorzieningen wél.

Drie niveaus, zodat er per jaar maar een handvol sticker-ontwerpen nodig zijn:
  1 = brons  (voldoet aan de basiscriteria)
  2 = zilver (ruim voldaan + bevestigd door genoeg goede reviews)
  3 = goud   (topniveau: alle criteria + veel hoge scores)

Het label draagt een jaartal, zodat het jaarlijks opnieuw verdiend wordt en
"vers" blijft. Fysiek druk je dus 3 ontwerpen × jaartal; digitaal mag de
weergave rijker zijn.
"""
from datetime import datetime

from ..models import db, Event, Review, get_bool, get_int

# De kindvriendelijke voorzieningen die meetellen voor de criteria-basis.
_VOORZIENINGEN = ("omheind", "verzorgingstafel", "buggy_ok",
                  "kinderstoel", "speelhoek", "kindermenu")


def _telt_voorzieningen(ev):
    return sum(1 for v in _VOORZIENINGEN if getattr(ev, v, None) is True)


def _review_stats(event_id):
    """Gemiddelde kindscore (1-5) en aantal reviews voor een event."""
    rijen = db.session.query(Review.kid_score).filter(
        Review.event_id == event_id).all()
    n = len(rijen)
    if not n:
        return 0.0, 0
    return sum(r[0] for r in rijen) / n, n


def bereken_niveau(ev):
    """Bepaal het labelniveau (0-3) voor één event op basis van criteria +
    reviews. Zuiver op merites — partnerstatus speelt geen enkele rol."""
    voorz = _telt_voorzieningen(ev)
    min_basis = get_int("label_min_voorzieningen", 3) or 3
    # Basisdrempel: genoeg voorzieningen + een redelijk volledige fiche.
    if voorz < min_basis or (ev.quality or 0) < 50:
        return 0
    gem, aantal = _review_stats(ev.id)
    drempel_reviews = get_int("label_min_reviews", 3) or 3
    # Goud: (bijna) alle voorzieningen én stevig bevestigd door reviews.
    if voorz >= 5 and aantal >= drempel_reviews and gem >= 4.3:
        return 3
    # Zilver: ruim voldaan én bevestigd door genoeg reviews.
    if voorz >= 4 and aantal >= drempel_reviews and gem >= 3.8:
        return 2
    # Brons: voldoet aan de basiscriteria (reviews nog niet vereist).
    return 1


def herbereken_labels(log=print, jaar=None):
    """Herbereken het label voor alle zichtbare, gecureerde fiches. Draai dit
    periodiek (bv. jaarlijks) en na grote review-instroom. Idempotent."""
    jaar = jaar or datetime.utcnow().year
    q = Event.query.filter(Event.hidden.is_(False), Event.curated.is_(True))
    veranderd = telling = {1: 0, 2: 0, 3: 0}
    telling = {0: 0, 1: 0, 2: 0, 3: 0}
    for ev in q.all():
        niveau = bereken_niveau(ev)
        if niveau != (ev.label_niveau or 0):
            ev.label_niveau = niveau
            ev.label_jaar = jaar if niveau else None
        elif niveau:
            ev.label_jaar = ev.label_jaar or jaar
        telling[niveau] = telling.get(niveau, 0) + 1
    db.session.commit()
    log(f"Labels herberekend (jaar {jaar}): "
        f"{telling[3]} goud, {telling[2]} zilver, {telling[1]} brons, "
        f"{telling[0]} geen.")
    return telling


LABELS = {
    1: ("🦊", "Ravot-label", "brons"),
    2: ("🦊", "Ravot-label Zilver", "zilver"),
    3: ("🦊", "Ravot-label Goud", "goud"),
}


def label_info(ev):
    """(emoji, naam, klasse, jaar) voor weergave, of None als geen label /
    module uit."""
    if not get_bool("label_aan"):
        return None
    niveau = ev.label_niveau or 0
    if not niveau:
        return None
    emoji, naam, klasse = LABELS[niveau]
    return {"emoji": emoji, "naam": naam, "klasse": klasse,
            "niveau": niveau, "jaar": ev.label_jaar}


def kamp_fotos(event_id, limiet=4):
    """Goedgekeurde kampfoto's (max 4). Kampfoto's zijn Photo-records met
    soort='kamp'."""
    from ..models import Photo
    return Photo.query.filter_by(event_id=event_id, soort="kamp",
                                 status="approved").order_by(
        Photo.created_at.asc()).limit(limiet).all()


def kamp_thumb(ev):
    """URL van de eerste goedgekeurde kampfoto, of None. Voor de zoekkaart."""
    from flask import url_for
    fotos = kamp_fotos(ev.id, limiet=1)
    if fotos:
        return url_for("public.foto", pid=fotos[0].id)
    return None


def detecteer_voorziening_conflicten(log=print, drempel=3):
    """Vind zaken waar meerdere gezinnen een aangevinkte voorziening betwisten.

    Een betwisting = een review met een tag "geen <label>" (bv. "geen
    verzorgingstafel"). Bij >= drempel betwistingen terwijl de partner het veld
    aan heeft: maak één Report (voor de admin-to-do) en mail de uitbater met
    vriendelijke, helpende feedback. We markeren de zaak zodat we niet blijven
    herhalen (via een reeds-gemelde-Report per veld).
    """
    from ..models import (db, Event, Review, Report, Operator, OperatorClaim,
                          VOORZIENING_LABELS)
    from . import uitbater_mail
    from flask import url_for

    gemeld = 0
    # Enkel zaken met minstens één aangevinkte voorziening zijn kandidaat.
    velden = list(VOORZIENING_LABELS.keys())
    kandidaten = Event.query.filter(
        db.or_(*[getattr(Event, v).is_(True) for v in velden])).all()
    for ev in kandidaten:
        reviews = Review.query.filter_by(event_id=ev.id).all()
        for veld, label in VOORZIENING_LABELS.items():
            if not getattr(ev, veld, None):
                continue
            betw_tag = f"geen {label}"
            n = sum(1 for r in reviews
                    if r.tags and betw_tag in r.tags)
            if n < drempel:
                continue
            # Al eens gemeld voor dit veld? Dan niet opnieuw.
            reason = f"voorziening:{veld}"
            bestaat = Report.query.filter_by(
                event_id=ev.id, reason=reason).first()
            if bestaat:
                continue
            db.session.add(Report(
                event_id=ev.id, reason=reason, handled=False,
                note=f"{n} gezinnen geven aan dat {label} ontbreekt, "
                     f"terwijl de fiche het wel vermeldt."))
            gemeld += 1
            # Mail de uitbater (indien geclaimd) — als hulp, niet als kritiek.
            claim = OperatorClaim.query.filter_by(
                event_id=ev.id, status="approved").first()
            if claim:
                op = db.session.get(Operator, claim.operator_id)
                if op:
                    try:
                        uitbater_mail.voorziening_feedback(
                            op.email, ev.title, label,
                            url_for("uitbater.fiche", event_id=ev.id,
                                    _external=True), n)
                    except Exception:
                        pass
    db.session.commit()
    log(f"Voorziening-conflicten: {gemeld} nieuwe melding(en) aangemaakt.")
    return gemeld
