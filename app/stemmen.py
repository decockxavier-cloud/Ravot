"""Zelf-curerende velden — fase 0 t/m 2.

Eén teller per veld per plek. De bron (OSM/Overture) is de startstem; gebruikers
voegen hun gewicht toe. De getoonde waarde is welke kant zwaarder weegt. Er is
nooit meer dan één waarde per veld — geen twee cijfers die tegenspreken.

Fase 2 voegt toe:
- Veroudering: oude stemmen wegen geleidelijk lichter, zodat de fiche meebeweegt
  met de werkelijkheid en een gekantelde situatie zichzelf corrigeert.
- Drie toestanden: onbekend → voorlopig (getoond, nog te versterken) →
  bevestigd (staat vast). Zo vergrendelt één losse stem een veld niet meer.
- Eerlijke twijfel: bij tegenspraak leveren we de verhouding (bv. 8 van 10),
  zodat de weergave "meestal ja" kan tonen i.p.v. stellig te doen.

De weergavelogica (KISS) houdt dit bewust op één plek; de rest van de code vraagt
enkel veld_status() en hoeft niets te weten van gewichten of halfwaardetijden.
"""
import math
from datetime import datetime

from .extensions import db
from .models import (VeldStem, ZACHTE_VELDEN, BRON_GEWICHT,
                     GEBRUIKER_BASIS_GEWICHT)


# Halfwaardetijd per soort veld, in dagen: na deze periode weegt een stem nog
# half. Vluchtige info veroudert sneller dan stabiele. Bewust aan de voorzichtige
# (trage) kant — bij te lang blijven hangen kan dit later omlaag.
HALFWAARDE_DAGEN = {
    # vluchtig: kan snel veranderen
    "terras": 400, "overdekt_terras": 400, "kindermenu": 400,
    # stabiel: verandert zelden
    "toilet": 900, "drinkwater": 900, "picknick": 900, "parking": 900,
    "speelhoek": 700, "kinderstoel": 700, "omheind": 1100,
    "toegankelijk": 1100, "verzorgingstafel": 900, "buggy_ok": 900,
    "allergievriendelijk": 500, "babyvoeding": 700, "huisdieren": 900,
}
STANDAARD_HALFWAARDE = 800

# Drempels voor de drie toestanden (netto gewicht = |ja - nee|).
# Eén verse gebruikersstem (gewicht ~1) tilt een veld al naar 'voorlopig':
# het wordt getoond, maar blijft vragen om bevestiging tot het stevig staat.
DREMPEL_BEVESTIGD = 2.5   # genoeg overeenstemming → staat vast, geen vraag meer
# twijfel: als de minderheid een noemenswaardig deel is, tonen we "meestal ja"
TWIJFEL_AANDEEL = 0.20    # ≥20% tegenstem → eerlijk als "meestal" tonen


def _stemmer_id(family):
    return "bron" if family is None else str(family.id)


def leg_stem_vast(event_id, veld, waarde, family=None, gewicht=None):
    """Registreer of wijzig één stem. Eén stem per (plek, veld, stemmer): wie
    van gedacht verandert, past zijn stem aan i.p.v. te stapelen.

    Retourneert de VeldStem-rij. Commit gebeurt door de aanroeper.
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


def _verval_factor(veld, stem, nu=None):
    """Hoeveel weegt deze stem nog, na veroudering? 1.0 vers, → 0 met de tijd.
    Exponentieel verval met een halfwaardetijd die van het veld afhangt."""
    nu = nu or datetime.utcnow()
    moment = stem.updated_at or stem.created_at or nu
    dagen = max(0.0, (nu - moment).total_seconds() / 86400.0)
    halfwaarde = HALFWAARDE_DAGEN.get(veld, STANDAARD_HALFWAARDE)
    return 0.5 ** (dagen / halfwaarde)


def _weeg(stemmen, veld, nu=None):
    """Splits in (gewicht_ja, gewicht_nee), mét veroudering per stem.

    Dit is de enige plek waar veroudering (fase 2) en straks vertrouwen (fase 3)
    ingrijpen — de rest van de code merkt er niets van.
    """
    nu = nu or datetime.utcnow()
    ja = nee = 0.0
    for s in stemmen:
        g = s.gewicht * _verval_factor(veld, s, nu)
        if s.waarde:
            ja += g
        else:
            nee += g
    return ja, nee


def veld_status(event_id, veld, stemmen=None, nu=None):
    """De uitkomst voor één veld, als dict:

        {waarde, ja, nee, herkomst, toestand, meerderheid_pct}

    - waarde     True|False|None  (None = onbekend)
    - toestand   'onbekend' | 'voorlopig' | 'bevestigd'
    - herkomst   'bezoekers' | 'bron' | 'geen'
    - meerderheid_pct  0-100: aandeel van de winnende kant (voor "meestal ja").
                       None als er geen twijfel is (eenparig of onbekend).
    """
    if stemmen is None:
        stemmen = VeldStem.query.filter_by(event_id=event_id, veld=veld).all()
    if not stemmen:
        return {"waarde": None, "ja": 0.0, "nee": 0.0, "herkomst": "geen",
                "toestand": "onbekend", "meerderheid_pct": None}

    nu = nu or datetime.utcnow()
    ja, nee = _weeg(stemmen, veld, nu)
    heeft_gebruiker = any(s.stemmer != "bron" for s in stemmen)
    herkomst = "bezoekers" if heeft_gebruiker else "bron"

    if ja == nee:
        bron = next((s for s in stemmen if s.stemmer == "bron"), None)
        waarde = bron.waarde if bron is not None else None
    else:
        waarde = ja > nee

    netto = abs(ja - nee)
    totaal = ja + nee
    if waarde is None or totaal <= 0:
        toestand = "onbekend"
    elif netto >= DREMPEL_BEVESTIGD:
        toestand = "bevestigd"
    else:
        toestand = "voorlopig"

    # Twijfelpercentage: enkel tonen als de minderheid noemenswaardig is.
    meerderheid_pct = None
    if totaal > 0 and waarde is not None:
        winnend = max(ja, nee)
        aandeel_minderheid = (totaal - winnend) / totaal
        if aandeel_minderheid >= TWIJFEL_AANDEEL:
            meerderheid_pct = round(100 * winnend / totaal)

    return {"waarde": waarde, "ja": ja, "nee": nee, "herkomst": herkomst,
            "toestand": toestand, "meerderheid_pct": meerderheid_pct}


def alle_velden(event_id, nu=None):
    """Alle velden met stemmen voor één plek → {veld: status-dict}. Eén query,
    één gedeeld 'nu'-moment zodat de veroudering consistent is."""
    nu = nu or datetime.utcnow()
    rijen = VeldStem.query.filter_by(event_id=event_id).all()
    per_veld = {}
    for r in rijen:
        per_veld.setdefault(r.veld, []).append(r)
    return {veld: veld_status(event_id, veld, stemmen, nu=nu)
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
