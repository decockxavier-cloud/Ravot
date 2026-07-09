"""UiTdatabank Search API — client + nachtelijke sync.

- Haalt enkel gezinsrelevante events op (Vlieg-label of leeftijdsgetagd).
- Filtert privacygevoelige velden (contactinfo organisatoren) weg vóór opslag,
  conform de publiq-gebruiksvoorwaarden.
- Vult PostcodeCentroid-tabel (afstandsberekening zonder externe geocoding).
- Koppelt events aan permanente editie-reeksen (SEO-troef §2.3).
"""
import re
import unicodedata
from datetime import datetime

import requests
from flask import current_app

from ..extensions import db
from ..models import Event, Organizer, Venue, EditionSeries, PostcodeCentroid


def slugify(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:200] or "event"


# ---------------------------------------------------------------- API-client --

def fetch_events(page_start=0, limit=50):
    """Eén pagina uit de Search API. Query: goedgekeurd, toekomstig,
    gezinsgericht (Vlieg-label OF typicalAgeRange die kinderen dekt)."""
    cfg = current_app.config
    from ..models import get_setting
    query = get_setting("uit_query") or "typicalAgeRange:[0 TO 12]"
    params = {
        "clientId": cfg["UIT_API_KEY"],  # publiq Search API: client id als query-param
        "start": page_start,
        "limit": limit,
        "embed": "true",
        "addressCountry": "BE",
        "q": query,
    }
    headers = {"Accept": "application/ld+json"}
    resp = requests.get(f"{cfg['UIT_SEARCH_URL']}/events", params=params,
                        headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ------------------------------------------------------------- normalisatie --

def _first_nl(val):
    if isinstance(val, dict):
        return val.get("nl") or next(iter(val.values()), None)
    return val


def parse_age_range(raw):
    """UiT typicalAgeRange komt als '3-12', '6-', '-12' of ontbreekt."""
    if not raw:
        return 0, 99
    m = re.match(r"^(\d*)-(\d*)$", str(raw))
    if not m:
        return 0, 99
    lo = int(m.group(1)) if m.group(1) else 0
    hi = int(m.group(2)) if m.group(2) else 99
    return lo, hi


def parse_prices(price_list):
    """Naar ons interne tarief-formaat; GEEN persoonsgegevens hierin."""
    out = []
    for t in price_list or []:
        name = (_first_nl(t.get("name")) or "").strip() or "basis"
        cat = t.get("category", "")
        entry = {"name": "basis" if cat == "base" else name.lower(),
                 "price": float(t.get("price", 0) or 0)}
        m = re.search(r"(\d+)\s*(?:tot|-)\s*(\d+)", name)
        if m:
            entry["min_age"], entry["max_age"] = int(m.group(1)), int(m.group(2))
        elif re.search(r"kind|jeugd", name, re.I):
            entry["min_age"], entry["max_age"] = 0, 12
        out.append(entry)
    return out


THEME_MAP = {  # UiT-termen → Ravot-categorieën
    "sport": "sport", "natuur": "natuur", "kunst": "creatief", "creativiteit": "creatief",
    "film": "cultuur", "theater": "cultuur", "muziek": "cultuur", "erfgoed": "cultuur",
    "wetenschap": "leren", "educatie": "leren", "wandel": "buiten", "fiets": "buiten",
    "speel": "buiten", "kamp": "buiten",
}

INDOOR_TYPES = {"film", "theater", "museum", "tentoonstelling", "workshop", "cursus"}


def normalise(item):
    """Eén UiT-event → dict voor ons Event-model. Contact-/persoonsvelden
    worden hier bewust NIET overgenomen (booking/contactPoint blijven achter)."""
    title = _first_nl(item.get("name")) or "Naamloos"
    uit_id = (item.get("@id") or "").rsplit("/", 1)[-1] or item.get("cdbid")
    loc = item.get("location") or {}
    addr = ((loc.get("address") or {}).get("nl")
            or (loc.get("address") or {})) or {}
    geo = loc.get("geo") or {}
    terms = []
    eventtype_lbl = None
    for t in item.get("terms", []) or []:
        lbl = t.get("label")
        lbl = _first_nl(lbl) if isinstance(lbl, dict) else lbl
        if lbl:
            terms.append(str(lbl).lower())
            if t.get("domain") == "eventtype" and not eventtype_lbl:
                eventtype_lbl = str(lbl)
    cats = sorted({v for term in terms for k, v in THEME_MAP.items() if k in term}) or ["buiten"]
    indoor = any(t in " ".join(terms) for t in INDOOR_TYPES)
    # Fijn type uit het UiT eventType (bv. 'Theatervoorstelling' -> uit_theater,
    # 'Kinderboerderij' -> kinderboerderij). Maakt UiT gelijkwaardig aan OSM.
    from ..types import uit_subtype
    subtype = uit_subtype(eventtype_lbl)
    labels = item.get("labels") or []
    hidden = item.get("hiddenLabels") or []
    all_labels = [str(l).lower() for l in (labels + hidden)]
    has_vlieg = any("vlieg" in l for l in all_labels)
    lo, hi = parse_age_range(item.get("typicalAgeRange"))
    prices = parse_prices(item.get("priceInfo"))
    is_free = bool(prices) and all(p["price"] == 0 for p in prices)
    # UiT levert beelden meestal via mediaObject[].contentUrl; 'image' is soms een directe URL.
    media = None
    mo = item.get("mediaObject") or []
    if isinstance(mo, list) and mo:
        media = mo[0].get("contentUrl") or mo[0].get("thumbnailUrl")
    if not media:
        img = item.get("image")
        media = img if isinstance(img, str) else None
    start = item.get("startDate")
    end = item.get("endDate")

    def _dt(s):
        if not s:
            return None
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)

    org = item.get("organizer") or {}
    raw_postcode = addr.get("postalCode")
    # Sommige (test)events hebben 'B-1853' e.d.; onze kolom is 4 tekens.
    postcode = None
    if raw_postcode:
        digits = re.sub(r"\D", "", str(raw_postcode))
        postcode = digits[:4] or None
    return {
        "uit_id": uit_id,
        "title": title,
        "description": (_first_nl(item.get("description")) or "")[:2000],
        "start": _dt(start), "end": _dt(end),
        "gemeente": addr.get("addressLocality"),
        "postcode": postcode,
        "lat": geo.get("latitude"), "lng": geo.get("longitude"),
        "age_min": lo, "age_max": hi,
        "categories": cats, "indoor": indoor, "has_vlieg": has_vlieg,
        "subtype": subtype,
        "is_free": is_free, "price_info": prices,
        "image_url": media,
        "venue": {"uit_id": (loc.get("@id") or "").rsplit("/", 1)[-1],
                  "name": _first_nl(loc.get("name")) or addr.get("addressLocality") or "Onbekend"},
        "organizer": {"uit_id": (org.get("@id") or "").rsplit("/", 1)[-1],
                      "name": _first_nl(org.get("name"))},
    }


# -------------------------------------------------------------------- upsert --

def _unieke_slug(model, basis, uit_id):
    """Geef een slug die geen bestaande (andere) rij botst. De unieke index op
    Organizer.slug botste anders wanneer twee verschillende organisatoren
    dezelfde naam hadden -> events werden overgeslagen."""
    basis = (basis or "org").strip("-") or "org"
    if not model.query.filter_by(slug=basis).first():
        return basis
    kandidaat = f"{basis}-{(uit_id or '')[:8]}"
    n = 2
    while model.query.filter_by(slug=kandidaat).first():
        kandidaat = f"{basis}-{(uit_id or '')[:8]}-{n}"
        n += 1
    return kandidaat


def _get_or_create(model, uit_id, defaults):
    if not uit_id:
        return None
    obj = model.query.filter_by(uit_id=uit_id).first()
    if obj is None:
        obj = model(uit_id=uit_id, **defaults)
        db.session.add(obj)
        db.session.flush()
    return obj


def find_or_create_series(title, organizer, venue):
    """Editie-matching: zelfde organisator + locatie + genormaliseerde titel
    (jaartallen en editienummers gestript) = zelfde reeks."""
    base = re.sub(r"\b(19|20)\d{2}\b", "", title)
    base = re.sub(r"\b\d+(ste|de|e)\b", "", base, flags=re.I).strip(" -–")
    slug = slugify(f"{base}-{(venue.gemeente if venue else '') or ''}")
    series = EditionSeries.query.filter_by(slug=slug).first()
    if series is None:
        series = EditionSeries(slug=slug, name=base.strip() or title,
                               organizer_id=organizer.id if organizer else None,
                               venue_id=venue.id if venue else None)
        db.session.add(series)
        db.session.flush()
    return series


def upsert_event(data):
    org = None
    if data["organizer"]["name"]:
        org = _get_or_create(Organizer, data["organizer"]["uit_id"],
                             {"name": data["organizer"]["name"],
                              "slug": _unieke_slug(Organizer,
                                                   slugify(data["organizer"]["name"]),
                                                   data["organizer"]["uit_id"])})
    venue = _get_or_create(Venue, data["venue"]["uit_id"], {
        "name": data["venue"]["name"], "gemeente": data["gemeente"],
        "postcode": data["postcode"], "lat": data["lat"], "lng": data["lng"],
    })
    series = find_or_create_series(data["title"], org, venue)

    ev = Event.query.filter_by(uit_id=data["uit_id"]).first()
    if ev is None:
        ev = Event(uit_id=data["uit_id"], source="uit")
        db.session.add(ev)
    for f in ("title", "description", "start", "end", "gemeente", "postcode",
              "lat", "lng", "age_min", "age_max", "categories", "indoor",
              "is_free", "price_info", "image_url", "has_vlieg"):
        setattr(ev, f, data[f])
    if data.get("subtype"):
        ev.subtype = data["subtype"]
    ev.slug = ev.slug or f"{slugify(data['title'])}-{data['uit_id'][:8]}"
    ev.organizer_id = org.id if org else None
    ev.venue_id = venue.id if venue else None
    ev.series_id = series.id
    from ..kwaliteit import bereken_kwaliteit
    ev.quality = bereken_kwaliteit(ev, heeft_reviews=False)
    return ev


def update_centroids():
    """Postcode-zwaartepunten herberekenen uit de eventdata."""
    rows = db.session.query(
        Event.postcode, Event.gemeente,
        db.func.avg(Event.lat), db.func.avg(Event.lng), db.func.count(Event.id),
    ).filter(Event.postcode.isnot(None), Event.lat.isnot(None)) \
     .group_by(Event.postcode, Event.gemeente).all()
    for postcode, gemeente, lat, lng, n in rows:
        pc = db.session.get(PostcodeCentroid, postcode) or PostcodeCentroid(postcode=postcode)
        pc.gemeente, pc.lat, pc.lng, pc.n_events = gemeente, lat, lng, n
        db.session.add(pc)


def backfill_geo_from_postcode():
    """Events zonder eigen coördinaten (komt voor in testdata en soms live)
    krijgen de coördinaten van hun postcode-zwaartepunt, zodat kaart en
    afstandsberekening blijven werken."""
    from ..postcodes import POSTCODE_COORDS
    # 1) events zonder geo maar met postcode → centroid of ingebouwde tabel
    events = Event.query.filter(Event.lat.is_(None), Event.postcode.isnot(None)).all()
    filled = 0
    for ev in events:
        pc = db.session.get(PostcodeCentroid, ev.postcode)
        if pc and pc.lat:
            ev.lat, ev.lng = pc.lat, pc.lng
            filled += 1
        elif ev.postcode in POSTCODE_COORDS:
            ev.lat, ev.lng = POSTCODE_COORDS[ev.postcode]
            filled += 1
    return filled


def run_sync(max_pages=None, page_size=50):
    from ..models import get_int
    if max_pages is None:
        max_pages = get_int("sync_max_pages", 200) or 200
    """Volledige nachtelijke sync. Retourneert aantal verwerkte events."""
    total = 0
    for page in range(max_pages):
        payload = fetch_events(page_start=page * page_size, limit=page_size)
        members = payload.get("member", [])
        if not members:
            break
        for item in members:
            try:
                upsert_event(normalise(item))
                db.session.flush()
                total += 1
            except Exception as exc:  # één slecht event mag de sync niet breken
                db.session.rollback()
                current_app.logger.warning("sync: event overgeslagen: %s", str(exc)[:120])
        db.session.commit()
        if len(members) < page_size:
            break
    update_centroids()
    db.session.commit()
    backfill_geo_from_postcode()   # kaart werkt ook bij events zonder eigen geo
    update_centroids()             # nu ook centroids voor die opgevulde postcodes
    db.session.commit()
    return total
