"""Afbeelding voor een activiteit: echte foto als die er is, anders een warme
categorie-illustratie zodat kaartjes nooit leeg/saai ogen (vooral OSM-POI's)."""
from flask import url_for

# Illustratie-sleutels: per sleutel bestaat een ingebouwde SVG
# (static/img/cat-<sleutel>.svg) die de beheerder kan overschrijven met een
# eigen afbeelding (patch 167, /beheer/illustraties -> /data/uploads/typen/).
ILLUSTRATIES = {
    "buiten": "Buiten / speeltuin", "binnen": "Binnen",
    "natuur": "Natuur", "sport": "Sport & beweging",
    "cultuur": "Cultuur & musea", "creatief": "Creatief",
    "leren": "Leren", "smullen": "Smullen (horeca)",
    "zwem": "Zwemmen", "boerderij": "Kinderboerderij",
}
EIGEN_ILLUSTRATIE_MAP = "/data/uploads/typen"
BON_LOGO_MAP = "/data/uploads/bonlogos"     # webshoplogo's (patch 175)


def bon_logo_pad(beloning_id):
    """Pad van het geüploade webshoplogo, of None."""
    import os
    pad = f"{BON_LOGO_MAP}/{int(beloning_id)}.png"
    return pad if os.path.exists(pad) else None

_SUBTYPE_KEY = {
    "horeca": "smullen", "zomerbar": "smullen", "winterbar": "smullen",
    "ijssalon": "smullen", "zwembad": "zwem", "zwemvijver": "zwem",
    "farm": "boerderij", "kinderboerderij": "boerderij",
}

_CAT_KEY = {"buiten": "buiten", "natuur": "natuur", "sport": "sport",
            "cultuur": "cultuur", "creatief": "creatief", "leren": "leren"}


def beeld_sleutel(event):
    """Welke illustratie-sleutel hoort bij deze plek."""
    k = _SUBTYPE_KEY.get(getattr(event, "subtype", None))
    if k:
        return k
    if getattr(event, "indoor", False):
        return "binnen"
    cats = getattr(event, "categories", None) or []
    return _CAT_KEY.get(cats[0] if cats else "buiten", "buiten")


def eigen_illustratie_pad(sleutel):
    """Pad van de door de beheerder geüploade afbeelding, of None."""
    import os
    pad = f"{EIGEN_ILLUSTRATIE_MAP}/{sleutel}.jpg"
    return pad if sleutel in ILLUSTRATIES and os.path.exists(pad) else None


def illustratie_url(sleutel):
    """URL van de illustratie voor een sleutel: eigen upload wint van de
    ingebouwde SVG. Cache-busting via bestandstijd."""
    import os
    pad = eigen_illustratie_pad(sleutel)
    if pad:
        return url_for("public.typebeeld", sleutel=sleutel,
                       v=int(os.path.getmtime(pad)))
    return url_for("static", filename=f"img/cat-{sleutel}.svg")


def _veilige_afbeelding(url):
    """Geef enkel een afbeeldings-URL terug die veilig laadt, anders None.
    - http:// wordt https:// (anders blokkeert de browser 'mixed content' en
      krijg je een kapot-foto-icoon zonder dat onerror altijd vuurt);
    - enkel URL's die op een echt beeldformaat lijken of van bekende
      beeld-hosts komen, worden vertrouwd."""
    if not url or not isinstance(url, str):
        return None
    u = url.strip()
    # Eigen fotodienst: relatieve /foto/<id>-URL's zijn door ons heringecodeerde
    # JPEG's — altijd veilig, en nodig voor kaartjes-thumbnails van eigen zaken.
    import re as _re
    if _re.fullmatch(r"/foto/\d+", u):
        return u
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


def gezinsfoto_id(event):
    """Id van de best gekeurde gezinsfoto bij een plek, of None (patch 209).

    Heeft een zaak geen eigen foto, dan is een echte gezinsfoto altijd beter
    dan een pictogram. Per verzoek gememoiseerd, zodat een lijst met 24
    kaarten niet 24 keer opnieuw hetzelfde vraagt."""
    eid = getattr(event, "id", None)
    if not eid:
        return None
    try:
        from flask import g as _g
        cache = _g._gezinsfoto_cache
    except (RuntimeError, AttributeError):
        cache = None
        try:
            from flask import g as _g
            cache = _g._gezinsfoto_cache = {}
        except RuntimeError:
            pass
    if cache is not None and eid in cache:
        return cache[eid]
    from .models import Photo
    f = (Photo.query
         .filter_by(event_id=eid, soort="gezin", status="approved")
         .order_by(Photo.id.desc()).first())
    uit = f.id if f else None
    if cache is not None:
        cache[eid] = uit
    return uit


def poi_image(event):
    """URL van de best beschikbare afbeelding. Nooit None, nooit een kapotte.
    Volgorde: eigen foto van de zaak -> goedgekeurde gezinsfoto -> pictogram."""
    echt = _veilige_afbeelding(getattr(event, "image_url", None))
    if echt:
        return echt
    fid = gezinsfoto_id(event)
    if fid:
        from flask import url_for
        return url_for("public.foto", pid=fid)
    return illustratie_url(beeld_sleutel(event))


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
