"""Verrijking van fiches met open data (patch 163).

Twee taken:
1. Straatnamen voor naamloze plekken (Nominatim reverse geocoding, met
   GeoCache-hergebruik en 1 verzoek/seconde conform de gebruiksvoorwaarden).
2. Vrije foto's van Wikimedia Commons (geosearch rond de plek, alleen
   vrije licenties, import via de bestaande fotopijplijn, mét bronvermelding).
"""
import io
import time

import requests

from ..extensions import db
from ..models import Event

NOMINATIM = "https://nominatim.openstreetmap.org/reverse"
COMMONS = "https://commons.wikimedia.org/w/api.php"
UA = {"User-Agent": "Ravot.be gezinsplatform (contact: info@ravot.be)"}
VRIJE_LICENTIES = ("cc0", "cc by", "cc-by", "public domain", "pd")

# Generieke labels waarvan de titel een straat verdient (zelfde als osm.LABELS
# maar hier alleen de veelvoorkomende naamloze soorten).
GENERIEK = ("Speeltuin", "Park", "Speelbos", "Skatepark", "Picknickplek",
            "Hondenweide", "Trapveldje")


def straat_bij(lat, lng, wacht=1.1):
    """Dichtstbijzijnde straatnaam via Nominatim. Retourneert None bij
    ontbreken of netwerkfout (nooit crashen). Geen cache nodig: zodra
    Event.adres gevuld is, valt de plek uit de kandidatenselectie."""
    try:
        antw = requests.get(NOMINATIM, params={
            "lat": lat, "lon": lng, "format": "jsonv2", "zoom": 17,
            "accept-language": "nl"}, headers=UA, timeout=10)
        time.sleep(wacht)   # beleefdheidsregel Nominatim: max 1/s
        adres = (antw.json() or {}).get("address", {})
        straat = adres.get("road") or adres.get("pedestrian") or None
    except Exception:
        return None
    return straat


def vul_straatnamen(limiet=150):
    """Naamloze OSM-plekken (kaal generiek label, of label — gemeente) een
    straat geven: Event.adres invullen en de titel verrijken. Behoedzaam:
    alleen source=osm, nooit handmatig hernoemde fiches."""
    q = Event.query.filter(Event.source == "osm", Event.adres.is_(None),
                           Event.lat.isnot(None), Event.is_permanent.is_(True))
    kandidaten = []
    for ev in q.limit(2000).all():
        kale = ev.title in GENERIEK
        met_gemeente = any(ev.title == f"{g} — {ev.gemeente}" for g in GENERIEK
                           if ev.gemeente)
        if kale or met_gemeente:
            kandidaten.append((ev, kale))
        if len(kandidaten) >= limiet:
            break
    n = 0
    for ev, kale in kandidaten:
        straat = straat_bij(ev.lat, ev.lng)
        if not straat:
            continue
        ev.adres = straat
        label = ev.title.split(" — ")[0]
        ev.title = f"{label} — {straat}"
        n += 1
    db.session.commit()
    return n, len(kandidaten)


# ------------------------------------------------------------ vrije foto's

def _licentie_vrij(naam):
    n = (naam or "").lower()
    return any(v in n for v in VRIJE_LICENTIES) and "nc" not in n and "nd" not in n


def commons_zoek(lat, lng, straal=250, maximum=8):
    """Vrije foto's rond een punt via de Commons-geosearch. Retourneert een
    lijst dicts (thumb, url, titel, fotograaf, licentie, pagina)."""
    try:
        antw = requests.get(COMMONS, params={
            "action": "query", "format": "json",
            "generator": "geosearch", "ggscoord": f"{lat}|{lng}",
            "ggsradius": min(max(straal, 50), 1000), "ggslimit": 20,
            "ggsnamespace": 6,
            "prop": "imageinfo", "iiprop": "url|extmetadata",
            "iiurlwidth": 640}, headers=UA, timeout=12).json()
    except Exception:
        return []
    uit = []
    for p in (antw.get("query", {}).get("pages", {}) or {}).values():
        info = (p.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {}) or {}
        licentie = (meta.get("LicenseShortName", {}) or {}).get("value", "")
        if not _licentie_vrij(licentie):
            continue
        if not (info.get("url", "").lower().endswith((".jpg", ".jpeg", ".png"))):
            continue
        uit.append({
            "titel": p.get("title", "").replace("File:", ""),
            "thumb": info.get("thumburl") or info.get("url"),
            "url": info.get("url"),
            "fotograaf": _strip_html((meta.get("Artist", {}) or {}).get("value", ""))[:120],
            "licentie": licentie[:40],
            "pagina": info.get("descriptionshorturl") or info.get("descriptionurl") or "",
        })
        if len(uit) >= maximum:
            break
    return uit


def _strip_html(s):
    import re
    return re.sub(r"<[^>]+>", "", s or "").strip()


def commons_import(url):
    """Download één Commons-beeld en geef (bytes, fout). Alleen van het
    officiële uploads-domein — voorkomt dat de importroute misbruikt wordt
    om willekeurige URL's op te halen."""
    if not url.startswith("https://upload.wikimedia.org/"):
        return None, "Alleen afbeeldingen van Wikimedia zelf kunnen geïmporteerd worden."
    try:
        antw = requests.get(url, headers=UA, timeout=15)
        if antw.status_code != 200 or len(antw.content) < 1000:
            return None, "Afbeelding kon niet opgehaald worden."
        return antw.content, None
    except Exception:
        return None, "Netwerkfout bij het ophalen van de afbeelding."


class _Upload:
    """Minimale wrapper zodat de bestaande fotopijplijn (herincodering,
    EXIF-strip, veilige naam) ongewijzigd hergebruikt kan worden."""
    def __init__(self, data, naam):
        self._b = io.BytesIO(data)
        self.filename = naam

    def read(self, *a):
        return self._b.read(*a)

    def seek(self, *a):
        return self._b.seek(*a)
