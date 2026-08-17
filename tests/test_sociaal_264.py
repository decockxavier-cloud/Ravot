"""Patch 264: sociale knoppen leesbaar in elke context.

Twee dingen liepen samen mis. De achtergrond stond op `var(--groen)`, een
variabele die niet bestaat — die viel dus weg (hersteld in p259). En omdat de
knoppen óók in de desktopnavigatie staan, won `.desktop-nav a { color: #5f6d63 }`
van de eigen kleurregel: grijsgroen icoon op een groene knop.
"""
import re


def _blok(klasse):
    with open("app/static/css/ravot.css", encoding="utf-8") as f:
        css = f.read()
    i = css.index(klasse)
    return css[i:css.index("}", i)]


def test_icoon_contrasteert_altijd_met_de_knop():
    blok = _blok(".sociaal-knop {")
    assert "background: #2E7D46" in blok          # vaste kleur, geen variabele
    assert "color: #fff !important" in blok       # wint van .desktop-nav a


def test_knop_blijft_rond_in_de_navigatie():
    """`.desktop-nav a` zet een padding die de cirkel zou vervormen."""
    blok = _blok(".sociaal-knop {")
    assert "padding: 0 !important" in blok
    assert "border-radius: 50%" in blok


def test_ook_bij_hover_leesbaar():
    blok = _blok(".sociaal-knop:hover")
    assert "color: #fff !important" in blok


def test_knoppen_staan_op_de_drie_plekken(client, app):
    """Header, mobiele footer en het Meer-paneel gebruiken dezelfde include."""
    with open("app/templates/base.html", encoding="utf-8") as f:
        base = f.read()
    assert base.count('include "public/_sociaal.html"') == 3
