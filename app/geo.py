"""Geo-resolutie: zet een postcode of plaatsnaam om naar (lat, lng).

Bron-onafhankelijk: postcodes komen uit de statische POSTCODE_COORDS (heel België),
plaatsnamen uit de event-afgeleide centroids of — als laatste redmiddel — uit een
geocoder (Nominatim), met cache zodat we per plaats maar één keer bevragen.

Zo blijven kaart-zoeken, 'in de buurt' en de afstands-personalisatie werken, ook
als de geladen data (OSM/Wikidata) weinig postcodes bevat.
"""
import requests
from flask import current_app

from .extensions import db
from .models import PostcodeCentroid
from .postcodes import POSTCODE_COORDS

_UA = "Ravot/1.0 (+https://ravot.be; gezinsuitstappen)"
_NOMINATIM = "https://nominatim.openstreetmap.org/search"


def postcode_coord(postcode):
    """(lat, lng) voor een postcode: eerst event-afgeleid (precies), anders de
    statische Belgische tabel."""
    if not postcode:
        return None
    pc = db.session.get(PostcodeCentroid, str(postcode))
    if pc and pc.lat is not None:
        return (pc.lat, pc.lng)
    coord = POSTCODE_COORDS.get(str(postcode))
    if coord:
        return coord
    from .plaatsen import PLAATSEN
    for zc, _naam, lat, lng in PLAATSEN:
        if zc == str(postcode):
            return (lat, lng)
    return None


def _geocode(term):
    """Plaatsnaam -> (lat, lng) via Nominatim, met cache. Faalt stil (-> None)."""
    from .models import GeoCache
    sleutel = term.strip().lower()[:120]
    row = db.session.get(GeoCache, sleutel)
    if row:
        return (row.lat, row.lng) if row.lat is not None else None
    coord = None
    try:
        url = current_app.config.get("NOMINATIM_URL", _NOMINATIM)
        r = requests.get(url, params={"q": term, "countrycodes": "be,nl,fr",
                                      "format": "json", "limit": 1},
                         headers={"User-Agent": _UA}, timeout=8)
        data = r.json() if r.status_code == 200 else []
        if data:
            coord = (float(data[0]["lat"]), float(data[0]["lon"]))
    except Exception:
        return None   # transiente fout: niet cachen, volgende keer opnieuw proberen
    if coord:
        try:
            db.session.add(GeoCache(term=sleutel, lat=coord[0], lng=coord[1]))
            db.session.commit()
        except Exception:
            db.session.rollback()
    return coord


def _offline_plaats(z):
    """Plaatsnaam -> coord via de canonieke offline lijst (exact, dan prefix)."""
    import unicodedata
    from .plaatsen import PLAATSEN
    def plat(t):
        return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    zp = plat(z)
    prefix = None
    for _zc, naam, lat, lng in PLAATSEN:
        n = plat(naam)
        if n == zp:
            return (lat, lng)
        if prefix is None and n.startswith(zp):
            prefix = (lat, lng)
    return prefix


def zoek_centrum(term):
    """Zoekterm (postcode of plaatsnaam) -> (lat, lng)-middelpunt, of None."""
    if not term:
        return None
    z = term.strip().lower()
    if z.isdigit() and len(z) == 4:
        return postcode_coord(z)
    offline = _offline_plaats(z)          # canonieke lijst eerst (geen netwerk)
    if offline:
        return offline
    pc = PostcodeCentroid.query.filter(db.func.lower(PostcodeCentroid.gemeente) == z).first()
    if pc:
        return (pc.lat, pc.lng)
    pc = PostcodeCentroid.query.filter(
        db.func.lower(PostcodeCentroid.gemeente).like(f"{z}%")).first()
    if pc:
        return (pc.lat, pc.lng)
    return _geocode(term)   # laatste redmiddel: geocoder
