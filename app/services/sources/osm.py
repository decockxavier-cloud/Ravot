"""OpenStreetMap (Overpass API) — adapter.

Haalt inherent kindvriendelijke POI's op: speeltuinen, dierentuinen, pretparken,
waterpretparken. De tags zelf zijn de poort — we vragen enkel kindvriendelijke
tags op, dus alles wat terugkomt hoort thuis in Ravot.

Data © OpenStreetMap-bijdragers, licentie ODbL — bronvermelding verplicht
(zie 'attribution' + de footer/over-pagina).
"""
import time

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

# Per provincie een bbox (zuid,west,noord,oost). Kleine stukken i.p.v. één
# reuzenquery over heel Vlaanderen -> veel minder kans op een 504-timeout.
PROVINCIE_BBOXES = [
    (50.68, 2.53, 51.38, 3.56),   # West-Vlaanderen
    (50.72, 3.35, 51.27, 4.30),   # Oost-Vlaanderen
    (50.95, 4.00, 51.51, 5.36),   # Antwerpen
    (50.66, 3.90, 51.10, 5.12),   # Vlaams-Brabant + Brussel
    (50.68, 4.88, 51.35, 5.94),   # Limburg
]

# Overpass-servers: primair (uit config) + mirrors als terugval.
def _endpoints():
    primair = current_app.config["OVERPASS_URL"]
    mirrors = ["https://overpass.kumi.systems/api/interpreter",
               "https://overpass.private.coffee/api/interpreter"]
    return [primair] + [m for m in mirrors if m != primair]


UA = "Ravot/1.0 (+https://ravot.be; gezinsuitstappen)"


def _query(tag, bbox):
    s, w, n, e = bbox
    if tag == "playground":
        sel = f'nwr["leisure"="playground"]({s},{w},{n},{e});'
    else:
        sel = f'nwr["tourism"="{tag}"]({s},{w},{n},{e});'
    return f"[out:json][timeout:120];({sel});out center tags;"


def _run(query):
    """Voer één query uit; probeer de servers op volgorde. Faalt een stuk,
    dan geven we [] terug (de rest van de sync loopt gewoon door)."""
    headers = {"User-Agent": UA, "Accept": "application/json"}
    for url in _endpoints():
        try:
            r = requests.post(url, data={"data": query}, headers=headers, timeout=150)
            if r.status_code == 200:
                return r.json().get("elements") or []
            current_app.logger.warning("overpass %s -> status %s", url, r.status_code)
        except Exception as exc:
            current_app.logger.warning("overpass %s -> %s", url, str(exc)[:100])
        time.sleep(1)  # even ademen voor de volgende server
    return []


def fetch():
    from ...models import get_setting
    tags = [t.strip() for t in (get_setting("osm_tags") or "").split(",") if t.strip()]
    tags = [t for t in tags if t in TAG_CATEGORIE]
    if not tags:
        return
    for tag in tags:
        for bbox in PROVINCIE_BBOXES:
            for el in _run(_query(tag, bbox)):
                yield el
            time.sleep(0.5)  # vriendelijk blijven voor de gratis servers


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

    # Echte website enkel als die er is — NOOIT terugvallen op osm.org (lelijk).
    website = tags.get("website") or tags.get("contact:website") or None

    # Foto: sommige OSM-plekken hebben een image- of wikimedia_commons-tag.
    from urllib.parse import quote
    beeld = tags.get("image")
    wm = tags.get("wikimedia_commons")
    if not beeld and wm and wm.lower().startswith("file:"):
        beeld = ("https://commons.wikimedia.org/wiki/Special:FilePath/"
                 + quote(wm[5:]) + "?width=800")

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
        "image_url": beeld,
        "source_url": website,            # None => geen (lelijke) link op de fiche
        "attribution": "© OpenStreetMap-bijdragers (ODbL)",
        "venue_ext_id": ext_id,
        "venue_name": title,
    }
