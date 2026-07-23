"""Gedeelde basis voor alle databronnen.

Elke bron (adapter) levert genormaliseerde dicts in HETZELFDE formaat als de
UiT-sync, zodat ze door één upsert de Event-tabel in gaan. De gouden regel van
Ravot geldt overal: **enkel kindvriendelijk aanbod komt binnen; de rest is
rommel en wordt weggegooid vóór opslag** (zie `child_safe`/de per-bron poorten).

Genormaliseerd event-dict (velden die een adapter mag zetten):
    source        str   verplicht: 'uit' | 'tm' | 'tv' | 'osm'
    ext_id        str   verplicht: unieke id binnen de bron
    title         str   verplicht
    description   str
    start, end    datetime | None   (None => permanente POI)
    is_permanent  bool  True voor POI's zonder vaste datum
    gemeente, postcode, lat, lng
    age_min, age_max
    categories    list[str]   (Ravot-categorieën, zie models.CATEGORIES)
    indoor        bool
    is_free       bool
    price_info    list[dict]
    image_url     str | None
    source_url    str | None   canonieke "meer info & tickets"-link
    attribution   str | None   korte bronvermelding (licentie-compliance)
    venue_name    str | None
"""
import re
import unicodedata

from ...extensions import db
from ...models import Event, Venue


def slugify(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:200] or "activiteit"


def clean_postcode(raw):
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw))
    return digits[:4] or None


# --------------------------------------------------------- kindvriendelijk --

# Woorden die een activiteit/plek diskwalificeren als "voor het hele gezin".
# Bewust streng: liever een kindvriendelijke plek missen dan rommel tonen.
NIET_KINDVRIENDELIJK = (
    "brouwerij", "brewery", "distilleerderij", "distillery", "wijn", "wine",
    "bier tasting", "biertasting", "jenever", "cocktail", "bar ", "nachtclub",
    "casino", "kerk", "kathedraal", "kapel", "abdij", "klooster", "begraafplaats",
    "cemetery", "war ", "oorlog", "wwi", "wwii", "1914", "1940", "loopgraaf",
    "trench", "erotisch", "erotic", "adults only", "18+", "vape", "tabak",
)

# Positieve signalen: als één hiervan matcht, is de plek/activiteit
# vrijwel zeker geschikt voor gezinnen met kinderen.
WEL_KINDVRIENDELIJK = (
    "speeltuin", "playground", "speelparadijs", "indoor speel", "kinderboerderij",
    "petting", "dieren", "zoo", "dierentuin", "dierenpark", "pretpark", "amusement",
    "theme park", "attractiepark", "avonturenpark", "adventure", "klimpark",
    "speelbos", "kabouter", "sprookjes", "fairy", "subtropisch", "zwemparadijs",
    "waterpark", "water park", "familie", "family", "kids", "kinder", "jeugd",
    "doe-museum", "kindermuseum", "science", "wetenschap", "planetarium",
    "boerderij", "farm", "natuureducatie", "bezoekerscentrum natuur",
)


def child_safe(text):
    """True als de tekst kindvriendelijk oogt: geen blacklist-hit en minstens
    één whitelist-hit. Enkel-whitelist-benadering = streng maar veilig."""
    t = (text or "").lower()
    if any(bad in t for bad in NIET_KINDVRIENDELIJK):
        return False
    return any(good in t for good in WEL_KINDVRIENDELIJK)


# ---------------------------------------------------------------- upsert --

def upsert_venue(source, ext_id, name, gemeente, postcode, lat, lng):
    """Locatie dedupliceren per bron. Venue.uit_id fungeert hier als generieke
    dedup-sleutel (wordt nooit als UiT-link getoond), met bron-prefix."""
    if not ext_id:
        return None
    key = f"{source}:{ext_id}"[:64]
    ven = Venue.query.filter_by(uit_id=key).first()
    if ven is None:
        ven = Venue(uit_id=key)
        db.session.add(ven)
    ven.name = name or gemeente or "Onbekend"
    ven.gemeente = gemeente
    ven.postcode = postcode
    ven.lat, ven.lng = lat, lng
    db.session.flush()
    return ven


def upsert_event(data):
    """Generieke upsert op (source, ext_id). Werkt voor alle niet-UiT bronnen.
    UiT houdt zijn eigen gespecialiseerde upsert (met reeks-matching)."""
    source, ext_id = data["source"], data["ext_id"]
    # Venue eerst oplossen: dit doet een flush; het Event mag pas daarna in de
    # sessie, anders probeert autoflush een half-leeg Event weg te schrijven.
    venue = upsert_venue(source, data.get("venue_ext_id") or ext_id,
                         data.get("venue_name"), data.get("gemeente"),
                         data.get("postcode"), data.get("lat"), data.get("lng"))
    ev = Event.query.filter_by(source=source, ext_id=ext_id).first()
    if ev is None:
        ev = Event(source=source, ext_id=ext_id)
        db.session.add(ev)
    for f in ("title", "description", "start", "end", "is_permanent",
              "gemeente", "postcode", "adres", "lat", "lng", "age_min", "age_max",
              "categories", "subtype", "indoor", "is_free", "price_info", "image_url",
              "source_url", "attribution", "pending"):
        if f in data:
            if f == "image_url" and not data[f] and ev.image_url:
                continue   # een bestaande (bv. gezins)foto nooit wissen bij hersync
            setattr(ev, f, data[f])
    # Openingsuren uit de bron verversen bij elke sync, TENZIJ een mens ze
    # zelf instelde: de beheerder (marker "_handmatig" in de JSON) of een
    # goedgekeurde uitbater-claim — die blijven altijd leidend.
    if data.get("openingsuren"):
        handmatig = bool((ev.openingsuren or {}).get("_handmatig"))
        geclaimd = False
        if ev.id:
            from ...models import OperatorClaim
            geclaimd = OperatorClaim.query.filter_by(
                event_id=ev.id, status="approved").count() > 0
        if not handmatig and not geclaimd:
            ev.openingsuren = data["openingsuren"]
    # Contact & speeltuindetail: bron vult/ververst, maar wist nooit.
    for f in ("telefoon", "email", "socials", "speeltoestellen",
              "cuisine", "uitbater_naam"):
        if data.get(f):
            setattr(ev, f, data[f])
    # Ouder-filters uit de bron: enkel AANzetten als de bron het bevestigt.
    # Nooit terug naar onbekend/uit — de community (reviews) en de beheerder
    # kunnen deze velden ook zetten en dat mag een sync niet ongedaan maken.
    for f in ("omheind", "verzorgingstafel", "buggy_ok",
              "kinderstoel", "speelhoek", "kindermenu",
              "toegankelijk", "toilet", "drinkwater", "picknick", "bbq",
              "veggie", "afhaal", "reserveren", "huisdieren"):
        if data.get(f) is True and getattr(ev, f, None) is not True:
            setattr(ev, f, True)
    ev.has_vlieg = False  # Vlieg is een publiq-label; nooit op andere bronnen
    if not ev.slug:
        # Volledige ext_id in de slug -> geen botsingen bij naamloze POI's.
        ev.slug = f"{slugify(data['title'])}-{source}-{slugify(str(ext_id))}"[:290]
    ev.venue_id = venue.id if venue else None
    from ...kwaliteit import bereken_kwaliteit
    ev.quality = bereken_kwaliteit(ev, heeft_reviews=False)  # reviews tellen mee bij herbereken
    db.session.flush()
    return ev


def run_source(adapter, commit_every=50):
    """Draai één adapter: haal op, gate op kindvriendelijkheid, upsert.
    `adapter.fetch()` levert ruwe items; `adapter.normalise(item)` levert een
    genormaliseerde dict of None (= verworpen). Retourneert (verwerkt, verworpen).

    Een fout tijdens het ophalen (bv. een pagina die faalt) mag NOOIT de reeds
    verwerkte items weggooien: we committen wat we hebben en stoppen netjes."""
    from flask import current_app
    processed = rejected = 0
    try:
        for item in adapter.fetch():
            try:
                data = adapter.normalise(item)
            except Exception:  # één slecht item mag de sync niet breken
                data = None
            if not data:
                rejected += 1
                continue
            try:
                # Savepoint per item: een fout rolt enkel DIT item terug,
                # niet de al-verwerkte items in dezelfde batch.
                with db.session.begin_nested():
                    upsert_event(data)
                processed += 1
                if processed % commit_every == 0:
                    db.session.commit()
            except Exception:
                rejected += 1
    except Exception as exc:
        db.session.commit()  # behoud alles wat tot hiertoe verwerkt is
        current_app.logger.warning("ophalen afgebroken na %d items: %s",
                                   processed, str(exc)[:160])
        return processed, rejected
    db.session.commit()
    return processed, rejected
