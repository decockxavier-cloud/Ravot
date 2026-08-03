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
    from ..kwaliteit import bereken_kwaliteit
    n = 0
    for ev, kale in kandidaten:
        straat = straat_bij(ev.lat, ev.lng)
        if not straat:
            continue
        ev.adres = straat
        label = ev.title.split(" — ")[0]
        ev.title = f"{label} — {straat}"
        ev.quality = bereken_kwaliteit(ev)   # score meteen mee (adres = +10)
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


def commons_info(bestand):
    """Metadata van één Commons-bestand ('File:...'): url, fotograaf, licentie.
    None als het bestand niet bestaat of geen vrije licentie draagt."""
    try:
        antw = requests.get(COMMONS, params={
            "action": "query", "format": "json", "titles": bestand,
            "prop": "imageinfo", "iiprop": "url|extmetadata",
            "iiurlwidth": 1200}, headers=UA, timeout=12).json()
    except Exception:
        return None
    for p in (antw.get("query", {}).get("pages", {}) or {}).values():
        info = (p.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {}) or {}
        licentie = (meta.get("LicenseShortName", {}) or {}).get("value", "")
        url = info.get("thumburl") or info.get("url") or ""
        if not _licentie_vrij(licentie):
            return None
        if not url.lower().split("?")[0].endswith((".jpg", ".jpeg", ".png")):
            return None
        return {"url": url,
                "fotograaf": _strip_html((meta.get("Artist", {}) or {})
                                         .get("value", ""))[:120],
                "licentie": licentie[:40],
                "pagina": info.get("descriptionshorturl")
                          or info.get("descriptionurl") or ""}
    return None


def importeer_osm_fotos(limiet=100, wacht=1.0):
    """Automatische fotoimport voor plekken waar de OSM-mapper zélf een
    Commons-foto aan koppelde (Event.commons_file). Betrouwbare 1-op-1-match,
    dus geen menselijk nazicht nodig; attributie gaat mee de fiche op."""
    from ..models import Event, Photo
    from .. import fotos as fotodienst
    klaar = {p.event_id for p in Photo.query.filter_by(bron="commons")
             .filter(Photo.event_id.isnot(None)).all()}
    q = (Event.query.filter(Event.commons_file.isnot(None),
                            Event.is_permanent.is_(True))
         .order_by(Event.id).limit(limiet * 3).all())
    n = geprobeerd = 0
    for ev in q:
        if ev.id in klaar:
            continue
        if geprobeerd >= limiet:
            break
        geprobeerd += 1
        info = commons_info(ev.commons_file)
        time.sleep(wacht)
        if not info:
            ev.commons_file = None     # onbruikbaar (weg/onvrij): niet blijven proberen
            continue
        data, fout = commons_import(info["url"])
        if fout:
            continue
        naam = fotodienst.verwerk_upload(_Upload(data, "commons.jpg"))
        if not naam:
            ev.commons_file = None
            continue
        db.session.add(Photo(event_id=ev.id, filename=naam, soort="zaak",
                             status="approved", bron="commons",
                             fotograaf=info["fotograaf"] or None,
                             licentie=info["licentie"] or None,
                             bron_url=info["pagina"] or None))
        n += 1
    db.session.commit()
    return n, geprobeerd


def foto_dekking():
    """Teloverzicht voor de hoeveel-is-er-nog-kaal-vraag."""
    from ..models import Event, Photo
    met_foto_ids = {p.event_id for p in Photo.query.filter(
        Photo.event_id.isnot(None), Photo.status == "approved").all()}
    totaal = echte = wachtrij = 0
    for ev in Event.query.filter(Event.is_permanent.is_(True),
                                 Event.hidden.is_(False),
                                 Event.pending.is_(False)).all():
        totaal += 1
        if ev.image_url or ev.id in met_foto_ids:
            echte += 1
        elif ev.commons_file:
            wachtrij += 1
    return {"totaal": totaal, "met_echte_foto": echte,
            "commons_wachtrij": wachtrij,
            "enkel_illustratie": totaal - echte - wachtrij}
