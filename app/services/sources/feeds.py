"""iCal/RSS-feeds — adapter voor lokale agenda's (cultuurcentra, toerisme, ...).

Anders dan de andere bronnen is dit niet één endpoint maar veel feeds: elke rij
in de Feed-tabel is een vertrouwde bron met een eigen URL. Dat sluit aan bij de
gecureerde richting: jij kiest welke agenda's je binnenhaalt, en die leveren
nette titels, datums en locaties — veel properder dan ruwe OSM-data.

Per feed:
- iCal (.ics): elk VEVENT -> een gedateerd event.
- RSS/Atom: elk item -> een gedateerd event (datum uit published/updated).
Een feed kan een standaardgemeente/postcode/categorie meegeven voor items die
zelf geen locatie vermelden.
"""
from datetime import datetime

import requests
from flask import current_app

from .base import clean_postcode

UA = {"User-Agent": "Ravot/1.0 (+https://ravot.be)"}


def _feeds():
    from ...models import Feed
    return Feed.query.filter_by(actief=True).all()


def _naive(dt):
    """Datetime -> naïef (zonder tz), zodat het in onze kolommen past."""
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    # date -> datetime op middernacht
    try:
        return datetime(dt.year, dt.month, dt.day)
    except Exception:
        return None


def _uit_ical(tekst, feed):
    from icalendar import Calendar
    cal = Calendar.from_ical(tekst)
    for comp in cal.walk("VEVENT"):
        titel = str(comp.get("summary") or "").strip()
        if not titel:
            continue
        start = _naive(getattr(comp.get("dtstart"), "dt", None))
        end = _naive(getattr(comp.get("dtend"), "dt", None))
        loc = str(comp.get("location") or "").strip()
        uid = str(comp.get("uid") or f"{feed.id}:{titel}:{start}")
        yield {
            "ext_id": f"{feed.id}:{uid}"[:120],
            "title": titel,
            "description": str(comp.get("description") or "")[:2000],
            "start": start, "end": end,
            "location": loc,
            "url": str(comp.get("url") or "") or None,
        }


def _uit_rss(tekst, feed):
    import feedparser
    d = feedparser.parse(tekst)
    for e in d.entries:
        titel = (getattr(e, "title", "") or "").strip()
        if not titel:
            continue
        start = None
        for veld in ("published_parsed", "updated_parsed"):
            tp = getattr(e, veld, None)
            if tp:
                start = datetime(*tp[:6])
                break
        yield {
            "ext_id": f"{feed.id}:{getattr(e, 'id', '') or getattr(e, 'link', '') or titel}"[:120],
            "title": titel,
            "description": (getattr(e, "summary", "") or "")[:2000],
            "start": start, "end": None,
            "location": "",
            "url": getattr(e, "link", None),
        }


def fetch():
    """Yield (feed, ruw_item) over alle actieve feeds. Eén stukke feed mag de
    rest niet breken."""
    from ...models import Feed, utcnow
    from ...extensions import db
    for feed in _feeds():
        try:
            r = requests.get(feed.url, headers=UA, timeout=25)
            r.raise_for_status()
            parser = _uit_ical if feed.kind == "ical" else _uit_rss
            n = 0
            for item in parser(r.text, feed):
                n += 1
                yield feed, item
            feed.last_run = utcnow()
            feed.last_result = f"{n} items"
            db.session.add(feed)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            try:
                feed.last_run = utcnow()
                feed.last_result = f"fout: {str(exc)[:120]}"
                db.session.add(feed)
                db.session.commit()
            except Exception:
                db.session.rollback()
            current_app.logger.warning("feed %s faalde: %s", feed.id, str(exc)[:150])


def normalise(pair):
    """(feed, item) -> genormaliseerd event-dict, of None."""
    feed, item = pair
    if not item.get("title") or not item.get("start"):
        return None
    gemeente = feed.gemeente
    postcode = clean_postcode(feed.postcode)
    cat = feed.categorie or "cultuur"
    lat = lng = None
    if postcode:
        from ...geo import postcode_coord
        coord = postcode_coord(postcode)
        if coord:
            lat, lng = coord
    return {
        "source": "feed",
        "ext_id": item["ext_id"],
        "title": item["title"],
        "description": item.get("description") or "",
        "start": item["start"], "end": item.get("end"),
        "is_permanent": False,
        "gemeente": gemeente, "postcode": postcode,
        "lat": lat, "lng": lng,
        "age_min": 0, "age_max": 12,
        "categories": [cat],
        "indoor": cat in ("cultuur", "creatief", "leren"),
        "is_free": False, "price_info": [],
        "image_url": None,
        "source_url": item.get("url"),
        "attribution": f"via {feed.naam}",
        "pending": not feed.trusted,       # niet-vertrouwde feeds -> wachtrij
        "venue_ext_id": f"feed:{feed.id}",
        "venue_name": item.get("location") or feed.naam,
    }
