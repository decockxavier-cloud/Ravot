"""Statische kaartafbeelding voor het afdrukbare routeblad (patch 217).

Het printblad toonde alleen een lijn op een effen achtergrond — zonder wegen,
dorpen of water kun je je op papier niet oriënteren. Deze module stelt een
echte kaart samen uit OSM-tegels en tekent de route erover.

Hoffelijkheid tegenover de tegelservers is hier belangrijk: we cachen het
resultaat per route op schijf, halen hoogstens een handvol tegels per kaart op
en sturen een herkenbare User-Agent mee. Een kaart wordt dus één keer gemaakt
en daarna gewoon van schijf geserveerd.
"""
import io
import math
import os

import requests

BREEDTE, HOOGTE = 1400, 900          # liggend, past op A4-landscape
TEGEL = 256
MAX_TEGELS = 40                      # veiligheidsrem per kaart
UA = {"User-Agent": "Ravot.be/1.0 (info@ravot.be) statische routekaart"}
TEGEL_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"


def _naar_pixels(lat, lng, zoom):
    n = 2 ** zoom * TEGEL
    x = (lng + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def _kies_zoom(punten):
    """Grootste zoom waarbij de hele route nog binnen het kader past."""
    lats = [p[0] for p in punten]
    lngs = [p[1] for p in punten]
    for zoom in range(16, 5, -1):
        x0, y0 = _naar_pixels(max(lats), min(lngs), zoom)
        x1, y1 = _naar_pixels(min(lats), max(lngs), zoom)
        if (x1 - x0) <= BREEDTE - 60 and (y1 - y0) <= HOOGTE - 60:
            return zoom
    return 6


def kaart_bestand(route, map_=None):
    """Pad naar de kaart-PNG van een route; maakt hem aan als hij ontbreekt.
    Retourneert None als er geen geometrie is of de tegels niet opgehaald
    kunnen worden (de aanroeper valt dan terug op de SVG-lijn)."""
    punten = route.geometrie or []
    if len(punten) < 2:
        return None
    if map_ is None:
        from flask import current_app
        map_ = os.path.join(
            current_app.config.get("UPLOAD_DIR", "/data/uploads"), "kaarten")
    os.makedirs(map_, exist_ok=True)
    pad = os.path.join(map_, f"route-{route.slug}.png")
    if os.path.exists(pad):
        return pad

    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    zoom = _kies_zoom(punten)
    xs, ys = zip(*[_naar_pixels(p[0], p[1], zoom) for p in punten])
    midden_x, midden_y = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    links = midden_x - BREEDTE / 2
    boven = midden_y - HOOGTE / 2

    tx0, ty0 = int(links // TEGEL), int(boven // TEGEL)
    tx1 = int((links + BREEDTE) // TEGEL)
    ty1 = int((boven + HOOGTE) // TEGEL)
    if (tx1 - tx0 + 1) * (ty1 - ty0 + 1) > MAX_TEGELS:
        return None

    doek = Image.new("RGB", (BREEDTE, HOOGTE), "#f2efe6")
    grens = 2 ** zoom
    gelukt = 0
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            if not (0 <= tx < grens and 0 <= ty < grens):
                continue
            try:
                antw = requests.get(
                    TEGEL_URL.format(z=zoom, x=tx, y=ty),
                    headers=UA, timeout=15)
                antw.raise_for_status()
                tegel = Image.open(io.BytesIO(antw.content)).convert("RGB")
            except Exception:
                continue
            doek.paste(tegel, (int(tx * TEGEL - links), int(ty * TEGEL - boven)))
            gelukt += 1
    if not gelukt:
        return None

    tekenaar = ImageDraw.Draw(doek)
    lijn = [(x - links, y - boven) for x, y in zip(xs, ys)]
    # witte onderlijn zodat de route ook op drukke kaarten leesbaar blijft
    tekenaar.line(lijn, fill="#ffffff", width=11, joint="curve")
    tekenaar.line(lijn, fill="#EE8035", width=6, joint="curve")
    sx, sy = lijn[0]
    tekenaar.ellipse([sx - 11, sy - 11, sx + 11, sy + 11],
                     fill="#4CA362", outline="#ffffff", width=4)
    tekenaar.rectangle([0, HOOGTE - 22, 300, HOOGTE], fill="#ffffffcc")
    tekenaar.text((6, HOOGTE - 17), "© OpenStreetMap-bijdragers",
                  fill="#333333")
    doek.save(pad, "PNG", optimize=True)
    return pad
