"""Wikidata (SPARQL) — adapter.

Gezaghebbende attracties met officiële naam, officiële website (P856) en een
echte Wikimedia-foto (P18): musea, pretparken, dierentuinen en aquaria. Dit is
de rijke laag die OSM mist. Permanente POI's => is_permanent=True.

Geen key nodig, wel een nette User-Agent (Wikidata-beleid). Bevraging per type
per regio, met een tijdslimiet; faalt een deelquery, dan loopt de rest door.

Data uit Wikidata (CC0). Foto's via Wikimedia Commons (licentie per beeld).
"""
import requests
from flask import current_app

from .base import NIET_KINDVRIENDELIJK

UA = "Ravot/1.0 (https://ravot.be; gezinsuitstappen; contact hallo@ravot.be)"

# osm_tags-soort -> (Wikidata-klasse QID, categorie, (leeftijd), binnen?, blacklist?)
KLASSEN = {
    "museum":     ("Q33506",  "cultuur", (4, 12), True,  True),
    "theme_park": ("Q194195", "buiten",  (2, 12), False, False),
    "zoo":        ("Q43501",  "natuur",  (1, 12), False, False),
    "aquarium":   ("Q1424628", "natuur", (2, 12), True,  False),
}

# Regio (uit osm_regios) -> SPARQL-scope-triple.
#   BE-regio's -> land België; nederland -> land NL; fr-nord -> regio Hauts-de-France.
SCOPES = {
    "vlaanderen": "?item wdt:P17 wd:Q31 .",
    "brussel":    "?item wdt:P17 wd:Q31 .",
    "wallonie":   "?item wdt:P17 wd:Q31 .",
    "nederland":  "?item wdt:P17 wd:Q55 .",
    "fr-nord":    "?item wdt:P131* wd:Q18677875 .",   # Hauts-de-France
}


def _regio_scopes(regios):
    """Ontdubbel scopes (vlaanderen+brussel+wallonie => één keer België)."""
    gezien, uit = set(), []
    for r in regios:
        scope = SCOPES.get(r)
        if scope and scope not in gezien:
            gezien.add(scope)
            uit.append(scope)
    return uit


def _query(klasse_qid, scope):
    return f"""
SELECT ?item ?itemLabel ?coord ?website ?image WHERE {{
  ?item wdt:P31/wdt:P279* wd:{klasse_qid} .
  ?item wdt:P625 ?coord .
  {scope}
  FILTER NOT EXISTS {{ ?item wdt:P576 ?ontbonden. }}   # ontbonden/afgebroken -> weg
  FILTER NOT EXISTS {{ ?item wdt:P3999 ?gesloten. }}   # datum van sluiting -> weg
  OPTIONAL {{ ?item wdt:P856 ?website. }}
  OPTIONAL {{ ?item wdt:P18 ?image. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "nl,en,fr". }}
}} LIMIT 5000"""


def fetch():
    cfg = current_app.config
    from ...models import get_setting
    soorten = [t.strip() for t in (get_setting("osm_tags") or "").split(",")
               if t.strip() in KLASSEN]
    regios = [r.strip() for r in (get_setting("osm_regios") or "vlaanderen").split(",")]
    scopes = _regio_scopes(regios)
    if not soorten or not scopes:
        return
    url = cfg.get("WIKIDATA_SPARQL_URL", "https://query.wikidata.org/sparql")
    headers = {"User-Agent": UA, "Accept": "application/sparql-results+json"}
    for soort in soorten:
        qid = KLASSEN[soort][0]
        for scope in scopes:
            try:
                r = requests.post(url, data={"query": _query(qid, scope)},
                                  headers=headers, timeout=120)
                r.raise_for_status()
                rows = (r.json().get("results") or {}).get("bindings") or []
            except Exception as exc:
                current_app.logger.warning("wikidata %s faalde: %s", soort, str(exc)[:120])
                continue
            for row in rows:
                row["_soort"] = soort
                yield row


def _coord(row):
    """P625 komt als 'Point(lng lat)'."""
    val = ((row.get("coord") or {}).get("value")) or ""
    if not val.startswith("Point("):
        return None, None
    try:
        lng, lat = val[6:-1].split()
        return float(lat), float(lng)
    except (ValueError, IndexError):
        return None, None


def normalise(row):
    soort = row.get("_soort")
    meta = KLASSEN.get(soort)
    if not meta:
        return None
    _, cat, (age_min, age_max), indoor, check = meta

    item = ((row.get("item") or {}).get("value")) or ""
    qid = item.rsplit("/", 1)[-1]
    if not qid.startswith("Q"):
        return None
    title = ((row.get("itemLabel") or {}).get("value")) or ""
    if not title or title == qid:      # geen bruikbare naam
        return None
    if check and any(bad in title.lower() for bad in NIET_KINDVRIENDELIJK):
        return None

    lat, lng = _coord(row)
    if lat is None:
        return None

    website = ((row.get("website") or {}).get("value")) or None
    image = ((row.get("image") or {}).get("value")) or None
    if image and "?" not in image:
        image = image + "?width=800"   # nette thumbnail via Commons FilePath

    return {
        "source": "wd",
        "ext_id": qid,
        "title": title,
        "description": "",
        "start": None, "end": None,
        "is_permanent": True,
        "gemeente": None, "postcode": None,
        "lat": lat, "lng": lng,
        "age_min": age_min, "age_max": age_max,
        "categories": [cat],
        "indoor": indoor,
        "is_free": False,
        "price_info": [],
        "image_url": image,
        "source_url": website,
        "attribution": "via Wikidata",
        "venue_ext_id": qid,
        "venue_name": title,
    }
