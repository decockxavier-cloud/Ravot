"""OpenStreetMap (Overpass API) — adapter.

Haalt vast te bezoeken, kindvriendelijke plekken op: speeltuinen, dierentuinen,
pretparken, waterpretparken, MUSEA en aquaria. De tag is de poort; voor musea
weren we bovendien duidelijk niet-kindvriendelijke gevallen (bv. erotisch museum).

Dekking is instelbaar per regio: Vlaanderen, Brussel, Wallonië, Nederland en
Noord-Frankrijk. Grote gebieden worden in kleine rastercellen opgevraagd zodat
de gratis Overpass-servers niet timen; per cel proberen we meerdere servers.

Data © OpenStreetMap-bijdragers, licentie ODbL — bronvermelding verplicht.
"""
import time

import requests
from flask import current_app

from .base import clean_postcode, NIET_KINDVRIENDELIJK

# tag -> (Ravot-categorie, (leeftijd_min, leeftijd_max), binnen?, blacklist-check?)
TAG_CATEGORIE = {
    "playground": ("buiten", (1, 12), False, False),
    "theme_park": ("buiten", (2, 12), False, False),
    "water_park": ("sport", (3, 12), False, False),
    "zoo": ("natuur", (1, 12), False, False),
    "museum": ("cultuur", (4, 12), True, True),
    "aquarium": ("natuur", (2, 12), True, False),
}
# Welke OSM-key hoort bij de tag.
OSM_KEY = {
    "playground": "leisure", "water_park": "leisure",
    "theme_park": "tourism", "zoo": "tourism", "museum": "tourism", "aquarium": "tourism",
}
LABELS = {"playground": "Speeltuin", "theme_park": "Pretpark",
          "water_park": "Waterpretpark", "zoo": "Dierenpark",
          "museum": "Museum", "aquarium": "Aquarium"}

# Regio -> bounding box (zuid, west, noord, oost).
REGIOS = {
    "vlaanderen": (50.67, 2.53, 51.51, 5.94),
    "brussel":    (50.76, 4.24, 50.91, 4.48),
    "wallonie":   (49.49, 2.84, 50.78, 6.41),
    "nederland":  (50.75, 3.36, 53.60, 7.23),
    "fr-nord":    (49.00, 1.55, 51.10, 4.27),   # Hauts-de-France
}

UA = "Ravot/1.0 (+https://ravot.be; gezinsuitstappen)"


def _endpoints():
    primair = current_app.config["OVERPASS_URL"]
    mirrors = ["https://overpass.kumi.systems/api/interpreter",
               "https://overpass.private.coffee/api/interpreter"]
    return [primair] + [m for m in mirrors if m != primair]


def _grid(bbox, step=0.7):
    """Deel een bbox op in kleine cellen (graden), zodat elke Overpass-query
    klein blijft en niet timet."""
    s, w, n, e = bbox
    lat = s
    while lat < n - 1e-9:
        lng = w
        while lng < e - 1e-9:
            yield (round(lat, 4), round(lng, 4),
                   round(min(lat + step, n), 4), round(min(lng + step, e), 4))
            lng += step
        lat += step


def _query(tag, bbox):
    s, w, n, e = bbox
    key = OSM_KEY[tag]
    return (f'[out:json][timeout:120];'
            f'(nwr["{key}"="{tag}"]({s},{w},{n},{e}););out center tags;')


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
        time.sleep(1)
    return []


def fetch():
    from ...models import get_setting
    tags = [t.strip() for t in (get_setting("osm_tags") or "").split(",")
            if t.strip() in TAG_CATEGORIE]
    regios = [r.strip() for r in (get_setting("osm_regios") or "vlaanderen").split(",")
              if r.strip() in REGIOS]
    if not tags or not regios:
        return
    for regio in regios:
        for tag in tags:
            for bbox in _grid(REGIOS[regio]):
                for el in _run(_query(tag, bbox)):
                    yield el
                time.sleep(0.5)   # vriendelijk blijven voor de gratis servers


def normalise(el):
    tags = el.get("tags") or {}
    kind = next((t for t, key in OSM_KEY.items() if tags.get(key) == t), None)
    if kind not in TAG_CATEGORIE:
        return None  # POORT: enkel kindvriendelijke tags
    cat, (age_min, age_max), indoor, check = TAG_CATEGORIE[kind]

    naam = tags.get("name")
    title = naam or LABELS.get(kind, "Uitstap")
    if check and any(bad in title.lower() for bad in NIET_KINDVRIENDELIJK):
        return None  # bv. erotisch museum weren

    ext_id = f"{el.get('type')}/{el.get('id')}"
    lat = el.get("lat") or (el.get("center") or {}).get("lat")
    lng = el.get("lon") or (el.get("center") or {}).get("lon")
    if lat is None or lng is None:
        return None

    website = tags.get("website") or tags.get("contact:website") or None
    oh = tags.get("opening_hours")
    descr = f"Openingsuren: {oh}" if oh else ""

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
        "description": descr[:2000],
        "start": None, "end": None,
        "is_permanent": True,
        "gemeente": tags.get("addr:city"),
        "postcode": clean_postcode(tags.get("addr:postcode")),
        "lat": float(lat), "lng": float(lng),
        "age_min": age_min, "age_max": age_max,
        "categories": [cat],
        "indoor": indoor,
        "is_free": kind == "playground",
        "price_info": [{"name": "basis", "price": 0}] if kind == "playground" else [],
        "image_url": beeld,
        "source_url": website,
        "attribution": "© OpenStreetMap-bijdragers (ODbL)",
        "venue_ext_id": ext_id,
        "venue_name": title,
    }
