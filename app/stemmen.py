"""Zelf-curerende velden — fase 0: het fundament.

Eén teller per veld per plek. De bron (OSM/Overture) is de startstem; gebruikers
voegen hun gewicht toe. De getoonde waarde is simpelweg welke kant zwaarder
weegt. Er is nooit meer dan één waarde per veld — geen twee cijfers die
tegenspreken.

In deze fase verandert er nog NIETS aan wat de gebruiker ziet: we lezen de
bestaande booleans op Event als bronstem in en de uitkomst is identiek aan de
huidige waarde. De weergave-, verouderings- en vertrouwenslogica komt in latere
fasen; dit bestand houdt het bewust simpel (KISS).
"""
from datetime import datetime

from .extensions import db
from .models import (VeldStem, ZACHTE_VELDEN, BRON_GEWICHT,
                     GEBRUIKER_BASIS_GEWICHT)


def _stemmer_id(family):
    return "bron" if family is None else str(family.id)


def leg_stem_vast(event_id, veld, waarde, family=None, gewicht=None):
    """Registreer of wijzig één stem. Eén stem per (plek, veld, stemmer): wie
    van gedacht verandert, past zijn stem aan i.p.v. te stapelen.

    Retourneert de VeldStem-rij. Commit gebeurt door de aanroeper (zodat meerdere
    stemmen in één transactie kunnen).
    """
    stemmer = _stemmer_id(family)
    if gewicht is None:
        gewicht = BRON_GEWICHT if family is None else GEBRUIKER_BASIS_GEWICHT
    rij = VeldStem.query.filter_by(event_id=event_id, veld=veld,
                                   stemmer=stemmer).first()
    if rij is None:
        rij = VeldStem(event_id=event_id, veld=veld, stemmer=stemmer,
                       waarde=bool(waarde), gewicht=gewicht)
        db.session.add(rij)
    else:
        rij.waarde = bool(waarde)
        rij.gewicht = gewicht
        rij.updated_at = datetime.utcnow()
    return rij


def _weeg(stemmen):
    """Splits een lijst stemmen in (gewicht_ja, gewicht_nee).

    In fase 0 is het gewicht statisch. Veroudering (fase 2) en vertrouwen
    (fase 3) grijpen later hier in — bewust op één plek, zodat de rest van de
    code niet hoeft te veranderen.
    """
    ja = sum(s.gewicht for s in stemmen if s.waarde)
    nee = sum(s.gewicht for s in stemmen if not s.waarde)
    return ja, nee


def veld_status(event_id, veld, stemmen=None):
    """De uitkomst voor één veld, als dict:

        {waarde: True|False|None, ja: float, nee: float, herkomst: str}

    - waarde None  → niemand heeft iets gezegd (veld onbekend).
    - herkomst     → 'bezoekers' als er minstens één gebruikersstem is,
                     anders 'bron', anders 'geen'.

    De weergave (bijschrift, twijfelpercentage) gebeurt pas in latere fasen;
    hier leveren we enkel de kale uitkomst.
    """
    if stemmen is None:
        stemmen = VeldStem.query.filter_by(event_id=event_id, veld=veld).all()
    if not stemmen:
        return {"waarde": None, "ja": 0.0, "nee": 0.0, "herkomst": "geen"}
    ja, nee = _weeg(stemmen)
    heeft_gebruiker = any(s.stemmer != "bron" for s in stemmen)
    herkomst = "bezoekers" if heeft_gebruiker else "bron"
    if ja == nee:
        # Gelijkspel: val terug op de bronstem als die er is, anders onbekend.
        bron = next((s for s in stemmen if s.stemmer == "bron"), None)
        waarde = bron.waarde if bron is not None else None
    else:
        waarde = ja > nee
    return {"waarde": waarde, "ja": ja, "nee": nee, "herkomst": herkomst}


def alle_velden(event_id):
    """Alle velden met stemmen voor één plek → {veld: status-dict}. Eén query."""
    rijen = VeldStem.query.filter_by(event_id=event_id).all()
    per_veld = {}
    for r in rijen:
        per_veld.setdefault(r.veld, []).append(r)
    return {veld: veld_status(event_id, veld, stemmen)
            for veld, stemmen in per_veld.items()}


def zaai_bronstemmen(event, velden=None):
    """Lees de bestaande booleans op een Event in als bronstemmen. Idempotent:
    draai je het twee keer, dan wordt de bestaande bronstem gewoon bijgewerkt.

    Enkel expliciet gezette booleans worden een stem — een veld dat None is
    (onbekend) levert geen stem op, want 'onbekend' is geen 'nee'.
    """
    velden = velden or ZACHTE_VELDEN
    n = 0
    for veld in velden:
        huidig = getattr(event, veld, None)
        if huidig is None:
            continue
        leg_stem_vast(event.id, veld, bool(huidig), family=None)
        n += 1
    return n
