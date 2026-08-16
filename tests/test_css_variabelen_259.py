"""Patch 259: elke gebruikte CSS-variabele moet ook bestaan.

Aanleiding: `var(--groen)` bestond niet (de kleur heet `--gras`). Op dertien
plekken viel daardoor de achtergrond weg — onder meer bij de streeklabels op
de routekaart, waar witte tekst op een bleke kaart overbleef. De browser meldt
zoiets niet: hij slaat de regel stilletjes over.
"""
import re


def _css():
    with open("app/static/css/ravot.css", encoding="utf-8") as f:
        return f.read()


# --pin wordt per marker inline gezet door verkennen.js (kleur per groep) en
# hoort dus niet in het stylesheet te staan.
INLINE = {"--pin"}


def test_alle_gebruikte_variabelen_zijn_gedefinieerd():
    css = _css()
    gedefinieerd = set(re.findall(r"^\s*(--[\w-]+)\s*:", css, re.M))
    # var(--x, #fallback) is prima: die valt netjes terug. Alleen var(--x)
    # zonder terugval moet gedefinieerd zijn.
    zonder_terugval = set(re.findall(r"var\((--[\w-]+)\s*\)", css))
    ontbreekt = zonder_terugval - gedefinieerd - INLINE
    assert not ontbreekt, f"CSS-variabelen zonder definitie: {sorted(ontbreekt)}"


def test_kaartbellen_hebben_gegarandeerd_contrast():
    """Een bel op een kaart moet leesbaar zijn, ook als een variabele ooit
    hernoemd wordt: daarom een vaste kleur en een witte rand."""
    css = _css()
    for klasse in (".streek-bel {", ".streek-bol {"):
        blok = css[css.index(klasse):css.index(klasse) + 420]
        assert "#2E7D46" in blok               # vaste donkergroene achtergrond
        assert "solid #fff" in blok            # witte rand tegen de kaart
        assert "color: #fff !important" in blok


def test_routepin_komt_los_van_de_kaart():
    """Een emoji met witte gloed verdrinkt tussen wegen en plaatsnamen;
    een cirkel met rand niet."""
    css = _css()
    blok = css[css.index(".route-pin {"):css.index(".route-pin {") + 400]
    assert "background: #fff" in blok
    assert "border: 2.5px solid" in blok
    assert "border-radius: 50%" in blok
