"""OpenStreetMap (Overpass API) — adapter.

Haalt inherent kindvriendelijke POI's op: speeltuinen, dierentuinen, pretparken,
waterpretparken. De tags zelf zijn de poort — we vragen enkel kindvriendelijke
tags op, dus alles wat terugkomt hoort thuis in Ravot.

Data © OpenStreetMap-bijdragers, licentie ODbL — bronvermelding verplicht
(zie 'attribution' + de footer/over-pagina).
"""
import requests
from flask import current_app

from .base import clean_postcode

# OSM-tag -> (Ravot-categorie, leeftijdsbereik, binnen?)
TAG_CATEGORIE = {
    "playground": ("buiten", (1, 12), False),
    "theme_park": ("buiten", (2, 12), False),
    "water_park": ("sport", (3, 12), False),
    "zoo": ("natuur", (1, 12), False),
}
# Vlaamse + Brusselse bounding box (zuid,west,noord,oost)
BBOX_VL = (50.67, 2.53, 51.51, 5.94)


def _tag_query(tags):
    """Overpass QL voor de gekozen tags binnen Vlaanderen/Brussel."""
    s, w, n, e = BBOX_VL
    parts = []
    for t in tags:
        if t == "playground":
            parts.append(f'nwr["leisure"="playground"]({s},{w},{n},{e});')
        elif t in ("theme_park", "water_park", "zoo"):
            parts.append(f'nwr["tourism"="{t}"]({s},{w},{n},{e});')
    return f"[out:json][timeout:60];({''.join(parts)});out center tags;"


def fetch():
    cfg = current_app.config
    from ...models import get_setting
    tags = [t.strip() for t in (get_setting("osm_tags") or "").split(",") if t.strip()]
    tags = [t for t in tags if t in TAG_CATEGORIE]
    if not tags:
        return
    # Overpass blokkeert requests zonder net User-Agent (vandaar soms een 406).
    headers = {"User-Agent": "Ravot/1.0 (+https://ravot.be; gezinsuitstappen)",
               "Accept": "application/json"}
    resp = requests.post(cfg["OVERPASS_URL"], data={"data": _tag_query(tags)},
                         headers=headers, timeout=90)
    resp.raise_for_status()
    for el in resp.json().get("elements") or []:
        yield el


def normalise(el):
    tags = el.get("tags") or {}
    kind = tags.get("leisure") or tags.get("tourism")
    if kind not in TAG_CATEGORIE:
        return None  # POORT: enkel kindvriendelijke tags
    cat, (age_min, age_max), indoor = TAG_CATEGORIE[kind]

    ext_id = f"{el.get('type')}/{el.get('id')}"
    lat = el.get("lat") or (el.get("center") or {}).get("lat")
    lng = el.get("lon") or (el.get("center") or {}).get("lon")
    if lat is None or lng is None:
        return None  # zonder coördinaten nutteloos op de kaart

    naam = tags.get("name")
    labels = {"playground": "Speeltuin", "theme_park": "Pretpark",
              "water_park": "Waterpretpark", "zoo": "Dierenpark"}
    title = naam or labels.get(kind, "Speelplek")

    return {
        "source": "osm",
        "ext_id": ext_id,
        "title": title,
        "description": "",
        "start": None, "end": None,
        "is_permanent": True,
        "gemeente": tags.get("addr:city"),
        "postcode": clean_postcode(tags.get("addr:postcode")),
        "lat": float(lat), "lng": float(lng),
        "age_min": age_min, "age_max": age_max,
        "categories": [cat],
        "indoor": indoor,
        "is_free": kind == "playground",  # speeltuinen zijn doorgaans gratis
        "price_info": [{"name": "basis", "price": 0}] if kind == "playground" else [],
        "image_url": None,
        "source_url": tags.get("website") or tags.get("contact:website")
                      or f"https://www.openstreetmap.org/{ext_id}",
        "attribution": "© OpenStreetMap-bijdragers (ODbL)",
        "venue_ext_id": ext_id,
        "venue_name": title,
    }
