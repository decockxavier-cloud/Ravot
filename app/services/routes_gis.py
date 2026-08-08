"""Fietsroutes: GPX-verwerking en de 'leuk onderweg'-koppeling (patch 160).

Bewust zonder PostGIS en zonder externe GPX-bibliotheek: de schaal (duizenden
events x honderden routes) rekent Python ruim binnen een seconde door, en GPX
is eenvoudige XML. De service is puur (punten in, resultaten uit) zodat hij
databank-onafhankelijk testbaar is; alleen `koppel_route` raakt de databank.
"""
import math
import xml.etree.ElementTree as ET

from ..extensions import db
from ..models import Event, FietsRoute, RouteBuurt, get_int
from ..scoring import haversine_km


# ---------------------------------------------------------------- GPX lezen

def parse_gpx(data):
    """GPX-bytes -> [(lat, lng, ele|None), ...]. Leest trkpt en rtept,
    namespace-ongevoelig. Gooit ValueError bij onbruikbare inhoud."""
    try:
        wortel = ET.fromstring(data)
    except ET.ParseError as e:
        raise ValueError(f"Geen geldig GPX-bestand: {e}")
    punten = []
    for el in wortel.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("trkpt", "rtept"):
            try:
                lat = float(el.attrib["lat"])
                lng = float(el.attrib["lon"])
            except (KeyError, ValueError):
                continue
            ele = None
            for kind in el:
                if kind.tag.rsplit("}", 1)[-1] == "ele":
                    try:
                        ele = float(kind.text)
                    except (TypeError, ValueError):
                        pass
            punten.append((lat, lng, ele))
    if len(punten) < 2:
        raise ValueError("Het GPX-bestand bevat geen route (minder dan 2 punten).")
    return punten


# ------------------------------------------------------- afgeleide velden

def route_stats(punten):
    """Alles wat automatisch uit de punten volgt (ontwerpdoc: auto-velden)."""
    afstand = 0.0
    stijging = 0.0
    for a, b in zip(punten, punten[1:]):
        afstand += haversine_km(a[0], a[1], b[0], b[1])
        if a[2] is not None and b[2] is not None and b[2] > a[2]:
            stijging += b[2] - a[2]
    lats = [p[0] for p in punten]
    lngs = [p[1] for p in punten]
    start, eind = punten[0], punten[-1]
    is_lus = haversine_km(start[0], start[1], eind[0], eind[1]) < 0.25
    return {
        "afstand_km": round(afstand, 1),
        "hoogte_m": int(stijging),
        "start_lat": start[0], "start_lng": start[1],
        "eind_lat": eind[0], "eind_lng": eind[1],
        "is_lus": is_lus,
        "bbox_n": max(lats), "bbox_z": min(lats),
        "bbox_o": max(lngs), "bbox_w": min(lngs),
    }


def duur_suggestie(afstand_km, tempo_kmu=None):
    tempo = tempo_kmu or get_int("route_tempo_kmu", 10) or 10
    return int(round(afstand_km / tempo * 60 / 5) * 5)   # op 5 min afgerond


def moeilijkheid_suggestie(afstand_km, hoogte_m):
    """Voorstel, nooit beslissing: de redacteur bevestigt of overschrijft."""
    if hoogte_m > 150 or afstand_km > 35:
        return "pittig"
    if hoogte_m > 60 or afstand_km > 25:
        return "licht"
    return "vlak"


# ------------------------------------------------ geometrie vereenvoudigen

def _punt_lijn_afstand(p, a, b):
    """Loodrechte afstand van punt p tot segment a-b, in graden-benadering
    (voldoende voor Douglas-Peucker binnen Vlaanderen)."""
    if a == b:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = (((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1]))
         / ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2))
    t = max(0.0, min(1.0, t))
    px = a[0] + t * (b[0] - a[0])
    py = a[1] + t * (b[1] - a[1])
    return math.hypot(p[0] - px, p[1] - py)


def vereenvoudig(punten, tolerantie=0.00035, maxi=300):
    """Douglas-Peucker naar een compacte weergavelijn (±150-300 punten,
    6-8 kB JSON). Tolerantie 0.00035 graden is ±30 m."""
    xy = [(p[0], p[1]) for p in punten]

    def dp(lijst):
        if len(lijst) < 3:
            return lijst
        a, b = lijst[0], lijst[-1]
        idx, verste = 0, 0.0
        for i in range(1, len(lijst) - 1):
            d = _punt_lijn_afstand(lijst[i], a, b)
            if d > verste:
                idx, verste = i, d
        if verste <= tolerantie:
            return [a, b]
        links = dp(lijst[:idx + 1])
        rechts = dp(lijst[idx:])
        return links[:-1] + rechts

    uit = dp(xy)
    while len(uit) > maxi:            # zeldzaam: extreem gedetailleerde GPX
        uit = uit[::2]
        if uit[-1] != xy[-1]:
            uit.append(xy[-1])
    return [[round(la, 5), round(ln, 5)] for la, ln in uit]


def sample(punten, stap_km=0.2):
    """Punten om de ±200 m langs de lijn, met hun km-positie —
    de meetlat voor de buurt-koppeling."""
    uit = [(punten[0][0], punten[0][1], 0.0)]
    gelopen = 0.0
    sinds = 0.0
    for a, b in zip(punten, punten[1:]):
        d = haversine_km(a[0], a[1], b[0], b[1])
        gelopen += d
        sinds += d
        if sinds >= stap_km:
            uit.append((b[0], b[1], round(gelopen, 2)))
            sinds = 0.0
    if uit[-1][:2] != (punten[-1][0], punten[-1][1]):
        uit.append((punten[-1][0], punten[-1][1], round(gelopen, 2)))
    return uit


# ---------------------------------------------------------- de koppeling

def zichtbare_buurt(route_id, limiet=None):
    """Buurt van een route, mét de actuele zichtbaarheid (patch 219).

    De koppeling wordt één keer gelegd bij het promoveren. Wordt een plek
    daarna geschrapt (verborgen of terug in nazicht), dan blijft die rij
    bestaan — en stond de plek dus nog op de route. Daarom filteren we hier
    opnieuw, zodat elke weergave de waarheid van vandaag toont.
    """
    from ..extensions import db
    from ..models import Event, RouteBuurt
    q = (db.session.query(RouteBuurt, Event)
         .join(Event, RouteBuurt.event_id == Event.id)
         .filter(RouteBuurt.route_id == route_id,
                 Event.hidden.is_(False), Event.pending.is_(False))
         .order_by(RouteBuurt.route_km.asc()))
    if limiet:
        q = q.limit(limiet)
    rijen = []
    for b, ev in q.all():
        b.event = ev            # relatie al gevuld: geen extra query per rij
        rijen.append(b)
    return rijen


def koppel_route(route):
    """Herbouw route_buurt voor één route: bbox-prefilter in SQL, haversine
    tegen de gesamplede lijn in Python. Retourneert het aantal koppelingen."""
    if not route.geometrie:
        return 0
    meter = get_int("route_buurt_meter", 400) or 400
    partner_meter = max(meter, get_int("route_partner_meter", 800) or 800)
    marge = partner_meter / 1000 / 111 + 0.002      # graden-marge om de bbox
    punten = sample([(p[0], p[1], None) for p in route.geometrie])

    kandidaten = (Event.query
                  .filter(Event.is_permanent.is_(True),
                          Event.hidden.is_(False), Event.pending.is_(False),
                          Event.lat.isnot(None),
                          Event.lat.between(route.bbox_z - marge, route.bbox_n + marge),
                          Event.lng.between(route.bbox_w - marge, route.bbox_o + marge))
                  .all())

    from .. import mollie
    RouteBuurt.query.filter_by(route_id=route.id).delete()
    n = 0
    for ev in kandidaten:
        beste_m, beste_km = None, 0.0
        for (la, ln, km) in punten:
            d = haversine_km(ev.lat, ev.lng, la, ln) * 1000
            if beste_m is None or d < beste_m:
                beste_m, beste_km = d, km or 0.0
        grens = partner_meter if mollie.is_zichtbaar_partner(ev) else meter
        if beste_m is not None and beste_m <= grens:
            db.session.add(RouteBuurt(route_id=route.id, event_id=ev.id,
                                      afstand_m=int(beste_m),
                                      route_km=round(beste_km, 1)))
            n += 1
    db.session.commit()
    return n


def routes_bij_event(event, limiet=3):
    """Omgekeerde lookup voor de eventfiche: 'ligt langs route X'."""
    rijen = (RouteBuurt.query
             .filter_by(event_id=event.id)
             .join(FietsRoute, FietsRoute.id == RouteBuurt.route_id)
             .filter(FietsRoute.pending.is_(False), FietsRoute.hidden.is_(False))
             .order_by(RouteBuurt.afstand_m.asc()).limit(limiet).all())
    return [(db.session.get(FietsRoute, r.route_id), r) for r in rijen]


def ruim_buurt_op():
    """Verweesde buurt-koppelingen wissen (patch 219).

    De weergaven filteren geschrapte plekken al weg, maar de rijen zelf laten
    staan geeft misleidende tellingen in ruwe queries en laat de tabel groeien.
    Retourneert het aantal opgeruimde rijen.
    """
    from ..extensions import db
    from ..models import Event, RouteBuurt
    # RouteBuurt heeft een samengestelde sleutel (route_id + event_id).
    dood = [(b.route_id, b.event_id) for b in
            db.session.query(RouteBuurt)
            .outerjoin(Event, RouteBuurt.event_id == Event.id)
            .filter(db.or_(Event.id.is_(None),
                           Event.hidden.is_(True),
                           Event.pending.is_(True))).all()]
    for rid, eid in dood:
        (RouteBuurt.query.filter_by(route_id=rid, event_id=eid)
         .delete(synchronize_session=False))
    if dood:
        db.session.commit()
    return len(dood)
