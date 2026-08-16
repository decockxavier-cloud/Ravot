"""Wegdek en verkeersdrukte langs een route (patch 252).

Toerisme Vlaanderen publiceert per netwerksegment het wegdektype (verhard /
onverhard) en de verkeersintensiteit (autovrij / niet-autovrij). Dat zijn
precies de twee dingen die een ouder wil weten en die we tot nu toe met de
hand invulden of gokten.

Twee eerlijkheidsregels die hier belangrijker zijn dan de meting zelf:

- Het wegdek dekt bijna het hele netwerk, de verkeersintensiteit maar ongeveer
  een vijfde. We rapporteren daarom altijd hoeveel procent van de route we
  effectief konden meten, en tonen een percentage alleen als de dekking hoog
  genoeg is. Anders zou "80% autovrij" een claim zijn over vier vijfde
  onbekend terrein.
- Lukt de meting niet, dan blijft het veld leeg in plaats van op nul te vallen.
  Geen data is eerlijker dan verzonnen data (zie ook de klimmeting, p194).
"""
import math

import requests

WFS = "https://geodata.toerismevlaanderen.be/geoserver/routes/wfs"
UA = {"User-Agent": "Ravot.be/1.0 (info@ravot.be)"}
# Binnen hoeveel meter een routepunt bij een segment hoort. Ruim genoeg voor
# de vereenvoudigde geometrie, krap genoeg om niet de buurstraat te pakken.
MAX_M = 35
# Onder deze dekking tonen we geen percentage.
MIN_DEKKING = 0.6


def _bbox(punten, marge_m=60):
    lats = [p[0] for p in punten]
    lngs = [p[1] for p in punten]
    d_lat = marge_m / 111_000
    d_lng = marge_m / 70_000
    return (min(lats) - d_lat, min(lngs) - d_lng,
            max(lats) + d_lat, max(lngs) + d_lng)


def _haal_segmenten(laag, punten, veld):
    """Segmenten binnen de omhullende van de route ophalen."""
    z, w, n, o = _bbox(punten)
    antw = requests.get(WFS, headers=UA, timeout=90, params={
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": laag, "outputFormat": "application/json",
        "srsName": "EPSG:4326",
        "bbox": f"{z},{w},{n},{o},urn:ogc:def:crs:EPSG::4326",
        "count": 4000,
    })
    antw.raise_for_status()
    uit = []
    for f in (antw.json() or {}).get("features", []):
        waarde = (f.get("properties") or {}).get(veld)
        geom = f.get("geometry") or {}
        lijnen = []
        if geom.get("type") == "LineString":
            lijnen = [geom.get("coordinates") or []]
        elif geom.get("type") == "MultiLineString":
            lijnen = geom.get("coordinates") or []
        for lijn in lijnen:
            for a, b in zip(lijn, lijn[1:]):
                uit.append(((a[1], a[0]), (b[1], b[0]), waarde))
    return uit


def _afstand_tot_stuk(punt, a, b):
    """Ruwe afstand (meter) van punt tot lijnstuk a-b."""
    k = math.cos(math.radians(punt[0]))
    px, py = punt[1] * k, punt[0]
    ax, ay = a[1] * k, a[0]
    bx, by = b[1] * k, b[0]
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        t = 0.0
    else:
        t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
        t = max(0.0, min(1.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy) * 111_000


def meet(punten, laag, veld, goede_waarden):
    """Aandeel van de route met een 'goede' waarde.

    Retourneert (percentage of None, dekking 0..1). Het percentage is berekend
    over de gemeten punten; de dekking zegt hoe betrouwbaar dat is.
    """
    if not punten or len(punten) < 2:
        return None, 0.0
    try:
        segmenten = _haal_segmenten(laag, punten, veld)
    except Exception:
        return None, 0.0
    if not segmenten:
        return None, 0.0

    # Rooster voor snelle nabijheid (cel ≈ 111 m). Een segment wordt in álle
    # cellen gezet die het doorkruist — enkel de eindpunten registreren laat
    # punten midden op een lang segment ongemeten.
    rooster = {}
    for a, b, waarde in segmenten:
        lengte_m = math.hypot((b[1] - a[1]) * math.cos(math.radians(a[0])),
                              b[0] - a[0]) * 111_000
        stappen = max(1, int(lengte_m / 60))
        for i in range(stappen + 1):
            t = i / stappen
            lat = a[0] + (b[0] - a[0]) * t
            lng = a[1] + (b[1] - a[1]) * t
            rooster.setdefault((round(lat, 3), round(lng, 3)), []).append(
                (a, b, waarde))

    gemeten = goed = 0
    for punt in punten:
        beste = None
        for dla in (-0.001, 0, 0.001):
            for dln in (-0.001, 0, 0.001):
                sleutel = (round(punt[0] + dla, 3), round(punt[1] + dln, 3))
                for a, b, waarde in rooster.get(sleutel, ()):
                    if waarde is None:
                        continue
                    d = _afstand_tot_stuk(punt, a, b)
                    if d <= MAX_M and (beste is None or d < beste[0]):
                        beste = (d, waarde)
        if beste:
            gemeten += 1
            if str(beste[1]).strip().lower() in goede_waarden:
                goed += 1
    if not gemeten:
        return None, 0.0
    dekking = gemeten / len(punten)
    return round(100 * goed / gemeten), round(dekking, 2)


def meet_wegdek(punten):
    """Percentage verhard. Dekking is doorgaans hoog."""
    return meet(punten, "routes:wegdek_fiets", "ground", {"verhard"})


def meet_autovrij(punten):
    """Percentage autovrij. Let op: deze laag dekt maar een deel van het
    netwerk, dus de dekking bepaalt of het cijfer bruikbaar is."""
    return meet(punten, "routes:verkeersintensiteit_fiets", "traffic",
                {"autovrij"})


def betrouwbaar(dekking):
    return dekking >= MIN_DEKKING
