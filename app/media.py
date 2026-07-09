"""Afbeelding voor een activiteit: echte foto als die er is, anders een warme
categorie-illustratie zodat kaartjes nooit leeg/saai ogen (vooral OSM-POI's)."""
from flask import url_for

_CAT_IMG = {
    "buiten": "cat-buiten.svg", "natuur": "cat-natuur.svg", "sport": "cat-sport.svg",
    "cultuur": "cat-cultuur.svg", "creatief": "cat-creatief.svg", "leren": "cat-leren.svg",
}


def _veilige_afbeelding(url):
    """Geef enkel een afbeeldings-URL terug die veilig laadt, anders None.
    - http:// wordt https:// (anders blokkeert de browser 'mixed content' en
      krijg je een kapot-foto-icoon zonder dat onerror altijd vuurt);
    - enkel URL's die op een echt beeldformaat lijken of van bekende
      beeld-hosts komen, worden vertrouwd."""
    if not url or not isinstance(url, str):
        return None
    u = url.strip()
    if u.startswith("//"):
        u = "https:" + u
    if u.startswith("http://"):
        u = "https://" + u[len("http://"):]
    if not u.startswith("https://"):
        return None
    laag = u.lower().split("?")[0]
    goede_host = ("upload.wikimedia.org", "commons.wikimedia.org",
                  "wikimedia.org", "uitdatabank", "cultuurdatabank",
                  "googleusercontent.com", "cloudfront.net")
    if laag.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
        return u
    if any(h in laag for h in goede_host):
        return u
    return None   # onbetrouwbaar -> liever de nette categorie-illustratie


def poi_image(event):
    """URL van de best beschikbare afbeelding. Nooit None, nooit een kapotte."""
    echt = _veilige_afbeelding(getattr(event, "image_url", None))
    if echt:
        return echt
    if getattr(event, "indoor", False):
        naam = "cat-binnen.svg"
    else:
        cats = getattr(event, "categories", None) or []
        naam = _CAT_IMG.get(cats[0] if cats else "buiten", "cat-buiten.svg")
    return url_for("static", filename=f"img/{naam}")


def has_echte_foto(event):
    return bool(_veilige_afbeelding(getattr(event, "image_url", None)))


_CAT_EMOJI = {"buiten": "🌳", "natuur": "🌿", "sport": "⚽", "cultuur": "🎭",
              "creatief": "🎨", "leren": "🔬", "binnen": "🏠"}


def poi_emoji(event):
    """Emoji + kleurklasse voor de fallback-banner (geen echte foto)."""
    if getattr(event, "indoor", False):
        cat = "binnen"
    else:
        cats = getattr(event, "categories", None) or []
        cat = cats[0] if cats else "buiten"
    if cat not in _CAT_EMOJI:
        cat = "buiten"
    return {"emoji": _CAT_EMOJI[cat], "klasse": f"c-{cat}"}
