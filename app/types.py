"""Activiteittypes — maakt het soort uitstap expliciet en herkenbaar.

Tot nu toe zag een bezoeker geen verschil tussen een gratis openbaar
speeltuintje en een betalend indoor-pretpark. Dit koppelt elke fiche aan een
duidelijk TYPE (met label + emoji), afgeleid uit het OSM-subtype of, bij
gedateerde events, uit de categorie. De beheerder kan per type kiezen of het
publiek zichtbaar is (setting 'verborgen_types').
"""

# code -> (emoji, label, is_plaats?)  — volgorde bepaalt de weergave in het beheer.
TYPES = {
    # Vaste plekken (uit OSM), fijn onderscheiden:
    "playground":     ("🛝", "Speeltuin (openbaar)", True),
    "park":           ("🌳", "Park", True),
    "nature_reserve": ("🌿", "Natuurgebied", True),
    "zoo":            ("🦁", "Dierenpark", True),
    "aquarium":       ("🐟", "Aquarium", True),
    "theme_park":     ("🎢", "Pretpark", True),
    "water_park":     ("🌊", "Waterpretpark", True),
    "swimming_area":  ("🏊", "Zwemplek", True),
    "miniature_golf": ("⛳", "Minigolf", True),
    "museum":         ("🏛️", "Museum", True),
    "castle":         ("🏰", "Kasteel", True),
    "viewpoint":      ("🔭", "Uitzichtpunt", True),
    "attraction":     ("🎡", "Attractie", True),
    # Gedateerde activiteiten (uit UiT/feeds), afgeleid uit de categorie:
    "ev_cultuur":     ("🎭", "Voorstelling", False),
    "ev_creatief":    ("🎨", "Workshop", False),
    "ev_sport":       ("⚽", "Sport", False),
    "ev_natuur":      ("🌿", "Natuuruitstap", False),
    "ev_leren":       ("🔬", "Ontdekken", False),
    "ev_buiten":      ("🎈", "Evenement", False),
    # Fijne UiT-types (uit het eventType-veld) — maken UiT gelijkwaardig aan OSM:
    "uit_theater":       ("🎭", "Theater", False),
    "uit_concert":       ("🎵", "Concert", False),
    "uit_film":          ("🎬", "Film", False),
    "uit_tentoonstelling": ("🖼️", "Tentoonstelling", False),
    "uit_workshop":      ("🎨", "Cursus of workshop", False),
    "uit_festival":      ("🎪", "Festival", False),
    "uit_rondleiding":   ("🧭", "Rondleiding", False),
    "uit_wandeling":     ("🥾", "Wandel- of fietstocht", False),
    "uit_kinderboerderij": ("🐐", "Kinderboerderij", True),
    "uit_indoorspeeltuin": ("🧸", "Indoor speelparadijs", True),
    "horeca":              ("🍽️", "Kindvriendelijke horeca", True),
    "uit_markt":         ("🛍️", "Markt of braderie", False),
    "uit_kamp":          ("⛺", "Kamp of vakantie", False),
}

# UiT eventType-labels (kleine letters) -> onze code, op trefwoord (robuust voor
# exacte bewoording). Eerste match wint, dus specifieke termen bovenaan.
UIT_TYPE_KEYWORDS = [
    ("indoorspeel", "uit_indoorspeeltuin"), ("speelparadijs", "uit_indoorspeeltuin"),
    ("kinderboerderij", "uit_kinderboerderij"), ("boerderij", "uit_kinderboerderij"),
    ("speeltuin", "playground"), ("pretpark", "theme_park"), ("attractiepark", "theme_park"),
    ("dieren", "zoo"), ("zoo", "zoo"), ("aquarium", "aquarium"),
    ("zwembad", "swimming_area"), ("zwemmen", "swimming_area"),
    ("museum", "museum"), ("galerij", "museum"), ("tentoonstelling", "uit_tentoonstelling"),
    ("theater", "uit_theater"), ("dans", "uit_theater"), ("circus", "uit_theater"),
    ("concert", "uit_concert"), ("muziek", "uit_concert"),
    ("film", "uit_film"), ("bioscoop", "uit_film"),
    ("workshop", "uit_workshop"), ("cursus", "uit_workshop"), ("atelier", "uit_workshop"),
    ("festival", "uit_festival"),
    ("rondleiding", "uit_rondleiding"), ("gegidst", "uit_rondleiding"),
    ("wandel", "uit_wandeling"), ("fiets", "uit_wandeling"), ("tocht", "uit_wandeling"),
    ("kamp", "uit_kamp"), ("vakantie", "uit_kamp"),
    ("markt", "uit_markt"), ("braderie", "uit_markt"),
    ("kasteel", "castle"), ("monument", "castle"),
]


def uit_subtype(eventtype_label):
    """UiT eventType-label -> een fijne type-code, of None (val terug op categorie)."""
    if not eventtype_label:
        return None
    t = str(eventtype_label).lower()
    for sleutel, code in UIT_TYPE_KEYWORDS:
        if sleutel in t:
            return code
    return None

_CAT_NAAR_EV = {
    "cultuur": "ev_cultuur", "creatief": "ev_creatief", "sport": "ev_sport",
    "natuur": "ev_natuur", "leren": "ev_leren", "buiten": "ev_buiten",
}


def type_code(event):
    """De type-code van een event: subtype (vaste plek) of categorie-afgeleide."""
    st = getattr(event, "subtype", None)
    if st and st in TYPES:
        return st
    cats = getattr(event, "categories", None) or []
    if cats:
        return _CAT_NAAR_EV.get(cats[0], "ev_buiten")
    return "ev_buiten"


def activiteit_type(event):
    """{code, emoji, label} voor de type-badge op een fiche."""
    code = type_code(event)
    emoji, label, _ = TYPES.get(code, ("🎈", "Uitstap", False))
    return {"code": code, "emoji": emoji, "label": label}


def verborgen_type_codes():
    """Set van type-codes die de beheerder publiek verborgen heeft."""
    try:
        from .models import get_setting
        ruw = get_setting("verborgen_types") or ""
    except Exception:
        ruw = ""
    return {c.strip() for c in ruw.split(",") if c.strip()}


# Commerciële plekken: hier geldt de Partner-afspraak (Ravotscore blijft van de
# community, maar tonen + meetellen in de volgorde is een Partner-voordeel).
# Openbare/publieke plekken (speeltuin, park, natuur, museum, ...) tonen hun
# score altijd — die hebben geen commerciële relatie met Ravot.
COMMERCIEEL = {"horeca", "uit_indoorspeeltuin", "theme_park", "water_park",
               "miniature_golf"}


def is_commercieel(event):
    return type_code(event) in COMMERCIEEL
