"""Welkomstmail (patch 248).

Een gezin dat zich registreert en daarna niets hoort, vergeet Ravot. Deze mail
vertrekt één dag na de registratie — bewust niet meteen, want dan komt hij
binnen naast de inlogcode en verdrinkt hij in de drukte van het aanmelden.

Dit is een dienstgerelateerde mail (uitleg over het eigen profiel), geen
nieuwsbrief: hij vertrekt één keer, ongeacht de nieuwsbriefvoorkeur. De
uitnodiging voor de weekendmail zit erin als één klik — dat is de wettelijk
juiste weg naar toestemming, en meteen het moment waarop de waarde zichtbaar is.
"""
from datetime import timedelta

from flask import render_template, url_for

from ..extensions import db
from ..models import Event, Family, utcnow


def _tips_in_de_buurt(fam, maxi=3):
    """Twee of drie concrete plekken dichtbij — een mail met echte namen erin
    leest als een antwoord, niet als reclame."""
    if not fam.postcode:
        return []
    from ..geo import postcode_coord
    from ..scoring import haversine_km
    coord = postcode_coord(fam.postcode)
    if not coord:
        return []
    lat, lng = coord
    marge = 0.25                       # ruwweg 25 km
    kandidaten = (Event.query
                  .filter(Event.is_permanent.is_(True),
                          Event.pending.is_(False), Event.hidden.is_(False),
                          Event.lat.between(lat - marge, lat + marge),
                          Event.lng.between(lng - marge * 1.6, lng + marge * 1.6))
                  .limit(300).all())
    uit = []
    for ev in kandidaten:
        if ev.lat is None or not ev.title:
            continue
        d = haversine_km(lat, lng, ev.lat, ev.lng)
        uit.append((d, ev))
    # dichtbij én deftig gescoord eerst
    uit.sort(key=lambda p: (p[0] - min((p[1].quality or 0) / 40.0, 2.0)))
    return [{"ev": ev, "km": round(d, 1)} for d, ev in uit[:maxi]]


def kandidaten(nu=None):
    """Gezinnen die gisteren registreerden en de mail nog niet kregen."""
    nu = nu or utcnow().replace(tzinfo=None)
    vanaf = nu - timedelta(days=2)
    tot = nu - timedelta(hours=20)
    return (Family.query
            .filter(Family.welkomstmail_op.is_(None),
                    Family.created_at.isnot(None),
                    Family.created_at >= vanaf,
                    Family.created_at <= tot)
            .all())


def bouw(fam):
    """(onderwerp, html, tekst) voor één gezin."""
    from .. import punten as pas
    from ..models import get_int
    tips = _tips_in_de_buurt(fam)
    from .weekendmail import maak_aanzet_token
    aanzet = url_for("auth.weekendmail_aan",
                     token=maak_aanzet_token(fam.id), _external=True)
    profiel = url_for("account.instellingen", _external=True)
    onderwerp = "Welkom bij Ravot 🦊 — zo haal je er het meeste uit"
    html = render_template(
        "mail/welkom.html", fam=fam, tips=tips, aanzet=aanzet,
        profiel=profiel,
        punten={"geweest": get_int("punt_geweest", 5),
                "review": get_int("punt_review", 10),
                "foto": get_int("punt_foto", 15)},
        heeft_kinderen=bool(fam.children), heeft_postcode=bool(fam.postcode))
    tekst = (
        f"Welkom bij Ravot!\n\n"
        f"Je profiel staat klaar. Drie dingen die meteen de moeite zijn:\n\n"
        f"1. Spaar ravotpunten — {get_int('punt_geweest', 5)} punten als je "
        f"bevestigt dat je ergens was, {get_int('punt_review', 10)} voor een "
        f"Ravotscore, {get_int('punt_foto', 15)} voor een foto. Wissel ze in "
        f"voor cadeaubonnen.\n"
        f"2. Help mee: elke fiche heeft vragen zoals 'is er een toilet?' of "
        f"'is het omheind?'. Eén tik en je maakt de fiche juister voor elk "
        f"gezin na jou.\n"
        f"3. Vul je profiel aan (postcode en geboortejaren van je kinderen) "
        f"voor tips op maat: {profiel}\n\n"
        f"Elke donderdag uitstappen in jullie buurt in je mailbox? "
        f"Zet het aan met één klik: {aanzet}\n\n"
        f"Veel ravotplezier,\nRavot.be")
    return onderwerp, html, tekst


def send_all(send_mail, nu=None):
    """Verstuur de welkomstmail; retourneert het aantal verstuurde mails."""
    n = 0
    for fam in kandidaten(nu):
        if not fam.email:
            continue
        try:
            onderwerp, html, tekst = bouw(fam)
            send_mail(fam.email, onderwerp, html, text=tekst)
            fam.welkomstmail_op = utcnow().replace(tzinfo=None)
            n += 1
        except Exception:
            db.session.rollback()
            continue
    db.session.commit()
    return n
