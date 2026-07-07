"""Ticketmaster Discovery API — adapter.

Haalt UITSLUITChildveilig aanbod op: enkel het **Family**-segment, landcode BE.
Family is Ticketmaster's eigen classificatie voor voorstellingen op maat van
gezinnen (kinderproducties, familieshows, ...). Alles buiten dat segment wordt
verworpen — dat is per definitie geen Ravot-materiaal.

Gedateerde events: passen 1-op-1 in het Vandaag/Weekend-model.
"""
from datetime import datetime

import requests
from flask import current_app

from .base import clean_postcode

SEGMENT_FAMILY = "Family"          # Ticketmaster-segmentnaam
GENRE_MAP = {                      # Family-genres -> Ravot-categorieën
    "theatre": "cultuur", "theater": "cultuur", "music": "cultuur",
    "circus": "cultuur", "magic": "cultuur", "puppetry": "cultuur",
    "children's theatre": "cultuur", "dance": "cultuur",
    "art": "creatief", "craft": "creatief",
    "sports": "sport", "ice shows": "sport",
    "science": "leren", "educational": "leren",
    "nature": "natuur", "animals": "natuur",
}


def fetch():
    """Yield ruwe Ticketmaster-events (Family-segment, BE), gepagineerd."""
    cfg = current_app.config
    key = cfg.get("TICKETMASTER_API_KEY")
    if not key:
        return
    base = cfg["TICKETMASTER_URL"].rstrip("/")
    page = 0
    while page < 50:  # harde bovengrens
        params = {
            "apikey": key,
            "countryCode": "BE",
            "classificationName": "family",   # Family-segment
            "locale": "*",
            "size": 100,
            "page": page,
            "sort": "date,asc",
        }
        resp = requests.get(f"{base}/events.json", params=params, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        events = (payload.get("_embedded") or {}).get("events") or []
        if not events:
            return
        for ev in events:
            yield ev
        total_pages = (payload.get("page") or {}).get("totalPages", 1)
        page += 1
        if page >= total_pages:
            return


def _segment_and_categories(item):
    seg = None
    cats = set()
    for cl in item.get("classifications") or []:
        seg_name = ((cl.get("segment") or {}).get("name") or "")
        if seg_name:
            seg = seg or seg_name
        genre = ((cl.get("genre") or {}).get("name") or "").lower()
        subg = ((cl.get("subGenre") or {}).get("name") or "").lower()
        for k, v in GENRE_MAP.items():
            if k in genre or k in subg:
                cats.add(v)
    return seg, sorted(cats) or ["cultuur"]


def _dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def normalise(item):
    """Eén Ticketmaster-event -> genormaliseerde dict, of None als het GEEN
    Family-aanbod is (dan verwerpen we het — kindveiligheidspoort)."""
    seg, cats = _segment_and_categories(item)
    if (seg or "").lower() != SEGMENT_FAMILY.lower():
        return None  # POORT: enkel Family-segment

    ext_id = item.get("id")
    if not ext_id:
        return None
    title = (item.get("name") or "").strip() or "Familievoorstelling"

    dates = item.get("dates") or {}
    start = _dt((dates.get("start") or {}).get("dateTime")
                or (dates.get("start") or {}).get("localDate"))

    venue = (((item.get("_embedded") or {}).get("venues")) or [{}])[0]
    city = (venue.get("city") or {}).get("name")
    loc = venue.get("location") or {}
    lat = float(loc["latitude"]) if loc.get("latitude") else None
    lng = float(loc["longitude"]) if loc.get("longitude") else None

    prices = item.get("priceRanges") or []
    minprice = min((p.get("min", 0) or 0) for p in prices) if prices else None
    is_free = minprice == 0 if minprice is not None else False
    price_info = []
    if prices:
        p = prices[0]
        price_info = [{"name": "basis", "price": float(p.get("min", 0) or 0)}]

    images = item.get("images") or []
    image_url = None
    if images:
        best = max(images, key=lambda im: im.get("width", 0))
        image_url = best.get("url")

    return {
        "source": "tm",
        "ext_id": ext_id,
        "title": title,
        "description": (item.get("info") or item.get("pleaseNote") or "")[:2000],
        "start": start, "end": None,
        "is_permanent": False,
        "gemeente": city,
        "postcode": clean_postcode(venue.get("postalCode")),
        "lat": lat, "lng": lng,
        "age_min": 0, "age_max": 12,   # Family-segment => gezinsgericht
        "categories": cats,
        "indoor": True,                 # voorstellingen zijn doorgaans binnen
        "is_free": is_free,
        "price_info": price_info,
        "image_url": image_url,
        "source_url": item.get("url"),
        "attribution": "via Ticketmaster",
        "venue_ext_id": venue.get("id") or ext_id,
        "venue_name": venue.get("name") or city,
    }
