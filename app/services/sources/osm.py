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
    # tag: (categorie, (leeftijd), binnen?, blacklist-check?, naam verplicht?)
    "playground":     ("buiten", (1, 12), False, False, False),
    "park":           ("buiten", (1, 12), False, False, True),
    "nature_reserve": ("natuur", (3, 12), False, False, True),
    "water_park":     ("sport",  (3, 12), False, False, False),
    "swimming_area":  ("sport",  (3, 12), False, False, True),
    "miniature_golf": ("sport",  (4, 12), False, False, False),
    "theme_park":     ("buiten", (2, 12), False, False, False),
    "zoo":            ("natuur", (1, 12), False, False, False),
    "aquarium":       ("natuur", (2, 12), True,  False, False),
    "museum":         ("cultuur", (4, 12), True, True,  False),
    "viewpoint":      ("buiten", (4, 12), False, False, True),
    "attraction":     ("buiten", (2, 12), False, True,  True),
    "castle":         ("cultuur", (4, 12), False, True,  True),
}
# Welke OSM-key hoort bij de tag.
OSM_KEY = {
    "playground": "leisure", "park": "leisure", "nature_reserve": "leisure",
    "water_park": "leisure", "swimming_area": "leisure", "miniature_golf": "leisure",
    "theme_park": "tourism", "zoo": "tourism", "aquarium": "tourism",
    "museum": "tourism", "viewpoint": "tourism", "attraction": "tourism",
    "castle": "historic",
}
LABELS = {"playground": "Speeltuin", "theme_park": "Pretpark",
          "water_park": "Waterpretpark", "zoo": "Dierenpark", "museum": "Museum",
          "aquarium": "Aquarium", "park": "Park", "nature_reserve": "Natuurgebied",
          "swimming_area": "Zwemplek", "miniature_golf": "Minigolf",
          "viewpoint": "Uitzichtpunt", "attraction": "Attractie", "castle": "Kasteel"}

# Regio -> bounding box (zuid, west, noord, oost).
REGIOS = {
    "vlaanderen": (50.67, 2.53, 51.51, 5.94),
    "brussel":    (50.76, 4.24, 50.91, 4.48),
    "wallonie":   (49.49, 2.84, 50.78, 6.41),
    "nederland":  (50.75, 3.36, 53.60, 7.23),
    "fr-nord":    (49.00, 1.55, 51.10, 4.27),   # Hauts-de-France
}

UA = "Ravot/1.0 (+https://ravot.be; gezinsuitstappen)"

# Servers die ons net afremden (429/503/504) even overslaan binnen dit proces.
_cooldown = {}   # url -> epoch tot wanneer we hem mijden


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
    return (f'[out:json][timeout:90];'
            f'(nwr["{key}"="{tag}"]({s},{w},{n},{e}););out center tags;')


# --- Kindvriendelijke horeca -------------------------------------------------
# We halen bewust NIET alle horeca binnen (dat zijn tienduizenden fiches zonder
# meerwaarde), enkel zaken die in OSM expliciet kindvriendelijke voorzieningen
# aangeven. Die tags zijn meteen goud voor de ouder-filters.
HORECA_AMENITY = ("restaurant", "cafe", "fast_food", "ice_cream")
HORECA_SIGNALEN = ("kids_area", "highchair", "changing_table", "playground")


def _query_horeca(bbox):
    s, w, n, e = bbox
    am = "|".join(HORECA_AMENITY)
    delen = "".join(
        f'nwr["amenity"~"^({am})$"]["{sig}"]["{sig}"!="no"]({s},{w},{n},{e});'
        for sig in HORECA_SIGNALEN)
    return f'[out:json][timeout:90];({delen});out center tags;'


def _run(query):
    """Voer één query uit; probeer de servers op volgorde, maar sla servers over
    die ons recent afremden (429/503/504). Faalt alles, dan geven we [] terug."""
    headers = {"User-Agent": UA, "Accept": "application/json"}
    nu = time.time()
    kandidaten = [u for u in _endpoints() if _cooldown.get(u, 0) < nu] or _endpoints()
    for url in kandidaten:
        try:
            r = requests.post(url, data={"data": query}, headers=headers, timeout=90)
            if r.status_code == 200:
                return r.json().get("elements") or []
            if r.status_code in (429, 503, 504):   # afgeremd/overbelast -> 2 min mijden
                _cooldown[url] = time.time() + 120
                current_app.logger.warning("overpass %s -> %s (2 min afkoelen)",
                                           url, r.status_code)
                time.sleep(3)
                continue
            current_app.logger.warning("overpass %s -> status %s", url, r.status_code)
        except Exception as exc:
            current_app.logger.warning("overpass %s -> %s", url, str(exc)[:100])
        time.sleep(1)
    return []


def fetch():
    from ...models import get_setting
    ruw = [t.strip() for t in (get_setting("osm_tags") or "").split(",")]
    tags = [t for t in ruw if t in TAG_CATEGORIE]
    horeca = "horeca" in ruw
    regios = [r.strip() for r in (get_setting("osm_regios") or "vlaanderen").split(",")
              if r.strip() in REGIOS]
    if (not tags and not horeca) or not regios:
        return
    for regio in regios:
        for tag in tags:
            for bbox in _grid(REGIOS[regio]):
                for el in _run(_query(tag, bbox)):
                    yield el
                time.sleep(0.5)   # vriendelijk blijven voor de gratis servers
        if horeca:
            for bbox in _grid(REGIOS[regio]):
                for el in _run(_query_horeca(bbox)):
                    yield el
                time.sleep(0.5)


def normalise(el):
    tags = el.get("tags") or {}
    # Gesloten/verlaten/voormalige plekken weren (bestaan niet meer).
    if (tags.get("disused") == "yes" or tags.get("abandoned") == "yes"
            or tags.get("end_date")
            or any(k.split(":", 1)[0] in ("disused", "abandoned", "was", "razed",
                                          "demolished", "removed") for k in tags)):
        return None
    kind = next((t for t, key in OSM_KEY.items() if tags.get(key) == t), None)
    if kind not in TAG_CATEGORIE:
        if tags.get("amenity") in HORECA_AMENITY:
            return _normalise_horeca(el, tags)
        return None  # POORT: enkel kindvriendelijke tags
    cat, (age_min, age_max), indoor, check, naam_verplicht = TAG_CATEGORIE[kind]

    naam = (tags.get("name") or tags.get("name:nl") or tags.get("official_name")
            or tags.get("name:fr") or tags.get("alt_name") or tags.get("loc_name"))
    if naam_verplicht and not naam:
        return None  # bv. een naamloos parkje -> geen bruikbare fiche
    if naam:
        title = naam
    else:
        # Geen echte naam: maak het generieke label herkenbaar met straat of
        # gemeente, zodat het geen kaal "Park" of "Speeltuin" wordt.
        label = LABELS.get(kind, "Uitstap")
        plek = tags.get("addr:street") or tags.get("addr:city")
        title = f"{label} — {plek}" if plek else label
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
        "adres": " ".join(p for p in (tags.get("addr:street"),
                                       tags.get("addr:housenumber")) if p) or None,
        "lat": float(lat), "lng": float(lng),
        "age_min": age_min, "age_max": age_max,
        "categories": [cat],
        "subtype": kind,
        "indoor": indoor,
        "is_free": kind == "playground",
        "price_info": [{"name": "basis", "price": 0}] if kind == "playground" else [],
        "image_url": beeld,
        "source_url": website,
        "attribution": "© OpenStreetMap-bijdragers (ODbL)",
        "venue_ext_id": ext_id,
        "venue_name": title,
    }


def _normalise_horeca(el, tags):
    """Kindvriendelijke horeca: enkel mét naam én minstens één expliciet
    kind-signaal. De OSM-tags vullen meteen de ouder-filters."""
    signalen = {sig: tags.get(sig) for sig in HORECA_SIGNALEN
                if tags.get(sig) and tags.get(sig) != "no"}
    naam = (tags.get("name") or tags.get("name:nl") or tags.get("official_name"))
    if not signalen or not naam:
        return None
    if any(bad in naam.lower() for bad in NIET_KINDVRIENDELIJK):
        return None
    ext_id = f"{el.get('type')}/{el.get('id')}"
    lat = el.get("lat") or (el.get("center") or {}).get("lat")
    lng = el.get("lon") or (el.get("center") or {}).get("lon")
    if lat is None or lng is None:
        return None
    troeven = []
    if "kids_area" in signalen:
        troeven.append("speelhoek voor de kinderen")
    if "playground" in signalen:
        troeven.append("speeltuin")
    if "highchair" in signalen:
        troeven.append("kinderstoelen")
    if "changing_table" in signalen:
        troeven.append("verschoontafel")
    descr = f"Kindvriendelijke zaak met {', '.join(troeven)}."
    oh = tags.get("opening_hours")
    if oh:
        descr += f" Openingsuren: {oh}"
    soort = {"restaurant": "restaurant", "cafe": "café",
             "fast_food": "eethuis", "ice_cream": "ijssalon"}.get(
                 tags.get("amenity"), "zaak")
    website = tags.get("website") or tags.get("contact:website") or None
    uit = {
        "source": "osm",
        "ext_id": ext_id,
        "title": naam,
        "description": f"{soort.capitalize()}. {descr}"[:2000],
        "start": None, "end": None,
        "is_permanent": True,
        "gemeente": tags.get("addr:city"),
        "postcode": clean_postcode(tags.get("addr:postcode")),
        "adres": " ".join(p for p in (tags.get("addr:street"),
                                      tags.get("addr:housenumber")) if p) or None,
        "lat": float(lat), "lng": float(lng),
        "age_min": 0, "age_max": 12,
        "categories": [],
        "subtype": "horeca",
        "indoor": True,
        "is_free": False,
        "price_info": [],
        "image_url": None,
        "source_url": website,
        "attribution": "© OpenStreetMap-bijdragers (ODbL)",
        "venue_ext_id": ext_id,
        "venue_name": naam,
    }
    # Ouder-filters rechtstreeks uit OSM (enkel positieve signalen):
    if signalen.get("changing_table"):
        uit["verzorgingstafel"] = True
    if tags.get("wheelchair") == "yes":
        uit["buggy_ok"] = True
    return uit
