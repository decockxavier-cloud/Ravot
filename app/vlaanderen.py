"""Bepalen of een plek in Vlaanderen of Brussel ligt.

Een bounding box volstaat niet: een rechthoek rond Vlaanderen bevat ook
Rijsel, Eindhoven, Maastricht en Zeeland. We combineren daarom twee toetsen:
de plaatsnaam moet een Vlaamse/Brusselse naam zijn (1.169 namen incl.
deelgemeenten), én de plek moet binnen 15 km van een Vlaams postcode-
zwaartepunt liggen (vangt dubbele namen zoals het Zeeuwse Middelburg).
"""
import json
import os
import unicodedata

from .scoring import haversine_km

_DATA = os.path.join(os.path.dirname(__file__), "data")
MAX_KM = 15.0

with open(os.path.join(_DATA, "gemeenten_vl.json")) as f:
    NAMEN = set(json.load(f))
with open(os.path.join(_DATA, "postcodes_vl.json")) as f:
    _PC = [(v["lat"], v["lng"]) for v in json.load(f).values()]


def normaliseer(naam):
    s = unicodedata.normalize("NFKD", naam or "").encode("ascii", "ignore").decode()
    s = s.lower().split("(")[0]
    for teken in ("-", ".", "'"):
        s = s.replace(teken, " ")
    return " ".join(s.split())


def km_tot_vlaanderen(lat, lng):
    """Afstand tot het dichtstbijzijnde Vlaamse/Brusselse postcode-zwaartepunt."""
    if lat is None or lng is None:
        return None                      # niet te beoordelen
    best = float("inf")                  # niets in de buurt = ver weg
    for clat, clng in _PC:
        if abs(clat - lat) > 0.30 or abs(clng - lng) > 0.45:
            continue
        d = haversine_km(lat, lng, clat, clng)
        if d < best:
            best = d
    return best


def is_vlaams(gemeente, lat, lng, max_km=MAX_KM):
    """True als de plek in Vlaanderen/Brussel hoort. Bij twijfel: True
    (liever een grensgeval te veel dan een Vlaamse plek wissen)."""
    afstand = km_tot_vlaanderen(lat, lng)
    if afstand is None:
        return True                      # geen coördinaten: niet beoordelen
    if afstand > max_km:
        return False                     # ver van elk Vlaams zwaartepunt
    if afstand <= 3.0:
        return True                      # pal op een Vlaamse kern (bv. Voeren,
                                         # waar enkel deelgemeenten in de lijst staan)
    naam = normaliseer(gemeente)
    if not naam:
        return True                      # geen naam: enkel op afstand geoordeeld
    return naam in NAMEN
