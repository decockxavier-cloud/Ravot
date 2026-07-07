"""Afbeelding voor een activiteit: echte foto als die er is, anders een warme
categorie-illustratie zodat kaartjes nooit leeg/saai ogen (vooral OSM-POI's)."""
from flask import url_for

_CAT_IMG = {
    "buiten": "cat-buiten.svg", "natuur": "cat-natuur.svg", "sport": "cat-sport.svg",
    "cultuur": "cat-cultuur.svg", "creatief": "cat-creatief.svg", "leren": "cat-leren.svg",
}


def poi_image(event):
    """URL van de best beschikbare afbeelding. Nooit None."""
    echt = getattr(event, "image_url", None)
    if echt:
        return echt
    if getattr(event, "indoor", False):
        naam = "cat-binnen.svg"
    else:
        cats = getattr(event, "categories", None) or []
        naam = _CAT_IMG.get(cats[0] if cats else "buiten", "cat-buiten.svg")
    return url_for("static", filename=f"img/{naam}")


def has_echte_foto(event):
    return bool(getattr(event, "image_url", None))
