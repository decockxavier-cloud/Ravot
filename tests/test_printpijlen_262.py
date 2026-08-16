"""Patch 262: rijrichting ook op het printblad.

Op papier draait er geen app mee: zonder pijl weet je bij een lus niet welke
kant je op moet. Het printblad gebruikt een PNG met echte kaartachtergrond, en
valt terug op een kale SVG als de tegels niet geladen kunnen worden — beide
krijgen pijlen.
"""
import re

from PIL import Image, ImageDraw

from app.extensions import db
from app.models import FietsRoute

# Vierkante lus: noord, oost, zuid, west
GEO = ([[50.94 + i * 0.002, 3.12] for i in range(10)]
       + [[50.958, 3.12 + i * 0.003] for i in range(10)]
       + [[50.958 - i * 0.002, 3.147] for i in range(10)]
       + [[50.94, 3.147 - i * 0.003] for i in range(10)])


def test_pijlen_op_de_terugvalkaart(client, app):
    with app.app_context():
        db.session.add(FietsRoute(titel="Vierkant", slug="pk262",
                                  afstand_km=12, duur_min=70,
                                  moeilijkheid="vlak", is_lus=True,
                                  pending=False, hidden=False, geometrie=GEO,
                                  routebeschrijving="Knooppunten: 1 – 2"))
        db.session.commit()
    r = client.get("/fietsroutes/pk262/kaartje")
    assert r.status_code == 200
    if r.mimetype != "image/svg+xml":
        return                       # echte kaart gelukt; die test hieronder
    svg = r.get_data(as_text=True)
    assert svg.count("<polygon") >= 8


def test_terugvalpijl_wijst_de_juiste_kant_op(client, app):
    """De route start noordwaarts; in SVG betekent noord een kleinere y."""
    with app.app_context():
        db.session.add(FietsRoute(titel="Vierkant", slug="pk262b",
                                  afstand_km=12, duur_min=70,
                                  moeilijkheid="vlak", is_lus=True,
                                  pending=False, hidden=False, geometrie=GEO,
                                  routebeschrijving="Knooppunten: 1 – 2"))
        db.session.commit()
    r = client.get("/fietsroutes/pk262b/kaartje")
    if r.mimetype != "image/svg+xml":
        return
    svg = r.get_data(as_text=True)
    punten = re.search(r'<polygon points="([\d.,\s]+)"', svg).group(1)
    top, links, rechts = [tuple(float(v) for v in p.split(","))
                          for p in punten.split()]
    assert top[1] < (links[1] + rechts[1]) / 2       # punt wijst omhoog


def test_pijlen_worden_op_de_kaartafbeelding_getekend():
    """De normale weg is een PNG met kaartachtergrond."""
    from app.services.statische_kaart import _teken_pijlen
    doek = Image.new("RGB", (400, 300), "#f5f2e8")
    tekenaar = ImageDraw.Draw(doek)
    lijn = [(20 + i * 12, 150) for i in range(30)]
    _teken_pijlen(tekenaar, lijn)
    kleuren = dict((c, n) for n, c in doek.getcolors(99999))
    assert kleuren.get((184, 84, 28), 0) > 100       # pijlkleur aanwezig


def test_te_korte_lijn_geeft_geen_pijlen():
    from app.services.statische_kaart import _teken_pijlen
    doek = Image.new("RGB", (100, 100), "#ffffff")
    tekenaar = ImageDraw.Draw(doek)
    _teken_pijlen(tekenaar, [(10, 10), (20, 20)])
    assert doek.getcolors()[0][1] == (255, 255, 255)  # niets getekend
