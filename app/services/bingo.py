"""Fietsbingo (patch 200): een afdrukbare zoekkaart per route.

Zestien vakjes die de rit een spel maken. Drie soorten, in deze volgorde van
betrouwbaarheid:
- knooppuntbordjes van de route zelf — altijd waar;
- dingen afgeleid uit wat er ÉCHT langs ligt (een glijbaan als er een
  speeltuin langs ligt, "een ijsje of frietje" als er te smullen valt) —
  zonder zaaknamen, conform de partnerregel;
- generieke spot-items voor onderweg (tractor, kerktoren, zwaaiende
  fietser) — bewust dingen die je *kunt* zien, nooit beloften over wat er
  *is*.

De kaart wisselt per maand (vaste seed per route+maand), zodat een
maandelijkse wedstrijd vanzelf een verse kaart heeft.
"""
import random
import re

from ..models import RouteBuurt
from ..types import groep_van

GENERIEK = [
    ("🚜", "een tractor"),
    ("🐄", "een koe"),
    ("🐴", "een paard"),
    ("⛪", "een kerktoren"),
    ("🌻", "een gele bloem"),
    ("🐕", "een hond"),
    ("🐈", "een kat"),
    ("🚩", "een vlag"),
    ("🌉", "een brug"),
    ("🪑", "een bankje"),
    ("🐦", "een vogel op een paal"),
    ("👋", "iemand die terugzwaait"),
    ("🚗", "een blauwe auto"),
    ("🌳", "een boom dikker dan papa"),
    ("🚲", "een fietser met een helm"),
    ("🏠", "een huis met rode luiken"),
    ("💧", "een beekje of gracht"),
    ("🐓", "een kip of haan"),
    ("📮", "een brievenbus"),
    ("🦋", "een vlinder of lieveheersbeestje"),
]


def _knooppunten_van(route):
    m = re.search(r"Knooppunten:\s*(.+)", route.routebeschrijving or "")
    if not m:
        return []
    return [n.strip() for n in m.group(1).replace("–", "-").split("-")
            if n.strip().isdigit()]


def items_voor_route(route, maand):
    """16 bingo-vakjes (emoji, label), deterministisch per route + maand."""
    rnd = random.Random(f"{route.id}-{maand}")
    items = []

    knopen = _knooppunten_van(route)
    if knopen:
        for nr in rnd.sample(knopen, min(3, len(knopen))):
            items.append(("🔢", f"knooppuntbordje {nr}"))

    tel = {"ravotten": 0, "smullen": 0, "beleven": 0}
    from .routes_gis import zichtbare_buurt
    for b in zichtbare_buurt(route.id, limiet=120):
        if b.event is not None:
            g = groep_van(b.event)
            if g in tel:
                tel[g] += 1
    if tel["ravotten"]:
        items.append(("🛝", "een glijbaan of schommel"))
    if tel["smullen"]:
        items.append(("🍦", "een ijsje of frietje"))
    if tel["beleven"]:
        items.append(("🎭", "een museum- of infobord"))

    rest = rnd.sample(GENERIEK, 16 - len(items))
    items.extend(rest)
    rnd.shuffle(items)
    return items[:16]
