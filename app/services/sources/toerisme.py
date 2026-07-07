"""Toerisme Vlaanderen (Linked Open Data) — adapter.

Permanente attracties via de JSON:API (geen key, licentie "Modellicentie Gratis
Hergebruik"). Toerisme Vlaanderen bevat ALLES — kerken, monumenten, brouwerijen,
oorlogssites — dus de kindvriendelijk-poort is hier cruciaal. We hanteren een
strikte whitelist: enkel wat duidelijk voor gezinnen met kinderen is, komt binnen.

POI's zonder vaste datum => is_permanent=True (zichtbaar op Kaart/Ontdek/gemeente,
niet in de gedateerde Vandaag/Weekend-feeds).
"""
import requests
from urllib.parse import urljoin, urlparse
from flask import current_app

from .base import child_safe, clean_postcode

UA = "Ravot/1.0 (+https://ravot.be; gezinsuitstappen)"


def _txt(v):
    """JSON:API-waarden komen soms als str, soms als lijst/dict met taalvarianten."""
    if isinstance(v, str):
        return v
    if isinstance(v, list) and v:
        return _txt(v[0])
    if isinstance(v, dict):
        return v.get("nl") or v.get("@value") or next(iter(v.values()), "")
    return ""


def fetch():
    """Yield ruwe attractie-resources uit de JSON:API, gepagineerd."""
    cfg = current_app.config
    from ...models import get_int
    base = (cfg.get("TOERISME_URL") or "https://linked.toerismevlaanderen.be").rstrip("/")
    if not urlparse(base).scheme:            # scheme-guard: nooit een relatieve base
        base = "https://linked.toerismevlaanderen.be"
    want = get_int("tv_max", 2000) or 2000
    url = f"{base}/tourist-attractions"
    params = {"page[size]": 100, "sort": "name"}
    headers = {"Accept": "application/vnd.api+json", "User-Agent": UA}
    seen = 0
    while url and seen < want:
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        for row in payload.get("data") or []:
            yield row
            seen += 1
            if seen >= want:
                return
        nxt = (payload.get("links") or {}).get("next")
        # 'next' kan absoluut of relatief zijn -> altijd absoluut maken
        url = urljoin(f"{base}/", nxt) if nxt else None
        params = None  # 'next' bevat de querystring al
TYPE_MAP = {
    "speeltuin": "buiten", "playground": "buiten", "speelbos": "buiten",
    "zoo": "natuur", "dieren": "natuur", "boerderij": "natuur", "natuur": "natuur",
    "pretpark": "buiten", "attractiepark": "buiten", "avontuur": "buiten",
    "museum": "cultuur", "science": "leren", "wetenschap": "leren",
    "zwem": "sport", "water": "sport", "klim": "sport",
}


def normalise(row):
    """Eén attractie -> genormaliseerde dict, of None als ze niet duidelijk
    kindvriendelijk is (kindveiligheidspoort: strikte whitelist)."""
    attrs = row.get("attributes") or row
    name = _txt(attrs.get("name")).strip()
    if not name:
        return None
    descr = _txt(attrs.get("description"))
    types = " ".join(_txt(t) for t in (attrs.get("types") or attrs.get("type") or []))
    haystack = f"{name} {types} {descr}"

    # POORT: enkel wat duidelijk voor kinderen is.
    if not child_safe(haystack):
        return None

    cats = sorted({v for k, v in TYPE_MAP.items() if k in haystack.lower()}) or ["buiten"]
    ext_id = row.get("id") or attrs.get("id")
    if not ext_id:
        return None

    addr = attrs.get("address") or {}
    geo = attrs.get("geo") or attrs.get("location") or {}
    lat = geo.get("latitude") or geo.get("lat")
    lng = geo.get("longitude") or geo.get("lng") or geo.get("long")
    return {
        "source": "tv",
        "ext_id": str(ext_id),
        "title": name,
        "description": (descr or "")[:2000],
        "start": None, "end": None,
        "is_permanent": True,
        "gemeente": _txt(addr.get("municipality") or addr.get("addressLocality")),
        "postcode": clean_postcode(addr.get("postalCode")),
        "lat": float(lat) if lat else None,
        "lng": float(lng) if lng else None,
        "age_min": 0, "age_max": 12,
        "categories": cats,
        "indoor": "museum" in haystack.lower() or "zwem" in haystack.lower(),
        "is_free": False,
        "price_info": [],
        "image_url": _txt(attrs.get("image") or attrs.get("mainImage")) or None,
        "source_url": _txt(attrs.get("url") or attrs.get("homepage")) or None,
        "attribution": "via Toerisme Vlaanderen (Gratis Hergebruik)",
        "venue_ext_id": str(ext_id),
        "venue_name": name,
    }
