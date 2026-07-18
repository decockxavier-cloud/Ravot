"""Overture Maps als horeca-bron (naast OSM).

Waarom Overture: de Places-laag bundelt o.a. de bedrijfsgegevens van
Facebook-pagina's (Meta) en Bing (Microsoft) — en Vlaamse horeca en zomerbars
hebben vrijwel allemaal een Facebook-pagina. Licentie: CDLA Permissive 2.0,
dus opslaan in onze eigen databank mag (mét bronvermelding). Google en
TripAdvisor verbieden dat in hun voorwaarden; Overture is het open
alternatief met vergelijkbare dekking voor dit soort zaken.

Werkwijze in twee stappen, bewust gescheiden:
1. `flask laad-overture` (CLI, maandelijks of ad hoc): download alle eet- en
   drinkgelegenheden binnen België naar de staging-tabel HorecaKandidaat.
   Dit duurt even (parquet-scan over het netwerk) en hoort dus niet in een
   webverzoek thuis.
2. De Horeca-verkenner in /beheer zoekt daarna LOKAAL in die kandidaten:
   bliksemsnel, geen externe afhankelijkheid, en de beheerder kiest wat
   Ravot-waardig is.
"""
from ...extensions import db
from ...models import HorecaKandidaat, PostcodeCentroid
from ...scoring import haversine_km
from .base import clean_postcode, upsert_event

# Ruwweg België (de kandidaten filteren we daarna op provincie/afstand).
BELGIE_BBOX = (2.50, 49.45, 6.45, 51.55)

# Overture-categoriecodes die eten/drinken betekenen. Korte woorden matchen
# we op héél woord (anders is een 'barber' plots een bar), langere op deelstring.
_CAT_KORT = {"bar", "pub", "cafe", "snack", "bistro"}
_CAT_LANG = ("restaurant", "coffee", "beer_garden", "brasserie", "ice_cream",
             "creperie", "pancake", "tea_house", "eat_and_drink", "fast_food")

_ZOMERBAR_HINTS = ("zomerbar", "zomer bar", "summer bar", "beach", "strandbar",
                   "pop-up", "popup", "bar d'été")


def _is_horeca(cat_primair, cat_alt=None):
    """Enkel de primaire categorie telt: via de alternatieve categorieën kwam
    te veel bijvangst binnen (tabakswinkels en supermarkten die ook 'cafe'
    als bijcategorie dragen)."""
    c = (cat_primair or "").lower()
    if any(w in c for w in _CAT_LANG):
        return True
    return bool(set(c.replace("_", " ").split()) & _CAT_KORT)


def lijkt_zomerbar(naam, cat_primair):
    n = (naam or "").lower()
    if any(h in n for h in _ZOMERBAR_HINTS):
        return True
    return "beer_garden" in (cat_primair or "")


def laad_horeca(bbox=BELGIE_BBOX, log=print):
    """Download de Overture Places-laag (eten & drinken) naar HorecaKandidaat.
    Bestaande kandidaten worden bijgewerkt; niets wordt hier al een fiche."""
    from overturemaps import core  # zwaar pakket: enkel hier importeren
    reader = core.record_batch_reader("place", bbox)
    if reader is None:
        raise RuntimeError("Overture gaf geen data terug")
    nieuw = bijgewerkt = bekeken = 0
    bestaande = {k.ext_id: k for k in HorecaKandidaat.query.all()}
    for batch in reader:
        for rec in batch.to_pylist():
            bekeken += 1
            cats = rec.get("categories") or {}
            primair = cats.get("primary")
            if not _is_horeca(primair, cats.get("alternate")):
                continue
            namen = rec.get("names") or {}
            naam = namen.get("primary")
            geom = rec.get("geometry")
            if not naam:
                continue
            # geometry is WKB; overturemaps levert ook bbox mee — gebruik die.
            bb = rec.get("bbox") or {}
            lat = (bb.get("ymin", 0) + bb.get("ymax", 0)) / 2 if bb else None
            lng = (bb.get("xmin", 0) + bb.get("xmax", 0)) / 2 if bb else None
            if not lat or not lng:
                continue
            adres = (rec.get("addresses") or [{}])[0] or {}
            land = (adres.get("country") or "").upper()
            if land and land != "BE":
                continue          # de bbox raakt Noord-Frankrijk en Nederland
            conf = rec.get("confidence")
            if conf is not None and conf < 0.5:
                continue          # te onzeker = vaak gesloten of verkeerd
            webs = rec.get("websites") or []
            socials = rec.get("socials") or []
            k = bestaande.get(rec["id"])
            if k is None:
                k = HorecaKandidaat(ext_id=rec["id"])
                db.session.add(k)
                bestaande[rec["id"]] = k
                nieuw += 1
            else:
                bijgewerkt += 1
            k.naam = naam[:200]
            k.categorie = (primair or "")[:80]
            k.adres = (adres.get("freeform") or "")[:200] or None
            k.gemeente = (adres.get("locality") or "")[:80] or None
            k.postcode = clean_postcode(adres.get("postcode"))
            k.lat, k.lng = float(lat), float(lng)
            web = webs[0] if webs else (socials[0] if socials else None)
            k.website = web[:300] if web else None
            k.zomerbar_hint = lijkt_zomerbar(naam, primair)
            k.confidence = rec.get("confidence")
            if bekeken % 50000 == 0:
                db.session.commit()
                log(f"  ... {bekeken} plaatsen bekeken, {nieuw} kandidaten")
    db.session.commit()
    log(f"Klaar: {nieuw} nieuwe en {bijgewerkt} bijgewerkte horeca-kandidaten "
        f"({bekeken} plaatsen bekeken).")
    return nieuw + bijgewerkt


def zoek_kandidaten(lat, lng, straal_km=5):
    """Lokale zoektocht in de staging-tabel — geen netwerk, dus altijd snel."""
    marge = straal_km / 90.0     # ruwe graden-box, daarna exact op afstand
    q = HorecaKandidaat.query.filter(
        HorecaKandidaat.lat.between(lat - marge, lat + marge),
        HorecaKandidaat.lng.between(lng - 1.6 * marge, lng + 1.6 * marge))
    uit = []
    for k in q.limit(2000).all():
        km = haversine_km(lat, lng, k.lat, k.lng)
        if km > straal_km:
            continue
        uit.append({"ext_id": k.ext_id, "naam": k.naam, "amenity": k.categorie,
                    "adres": k.adres, "gemeente": k.gemeente,
                    "postcode": k.postcode, "lat": k.lat, "lng": k.lng,
                    "signalen": [], "buiten": False, "website": k.website,
                    "zomerbar": bool(k.zomerbar_hint), "km": round(km, 1)})
    uit.sort(key=lambda r: r["km"])
    return uit


_SOORT_TEKST = {"horeca": "Kindvriendelijke zaak", "zomerbar":
                "Gezinsvriendelijke zomerbar"}


def importeer(ext_ids_met_soort):
    """Gekozen kandidaten omzetten naar gecureerde fiches."""
    centroids = None
    aantal = 0
    for ext_id, soort in ext_ids_met_soort:
        k = HorecaKandidaat.query.filter_by(ext_id=ext_id).first()
        if not k:
            continue
        soort = "zomerbar" if soort == "zomerbar" else "horeca"
        gemeente, postcode = k.gemeente, k.postcode
        if not gemeente or not postcode:
            if centroids is None:
                centroids = PostcodeCentroid.query.all()
            best = None
            for c in centroids:
                if c.lat is None:
                    continue
                d = haversine_km(k.lat, k.lng, c.lat, c.lng)
                if best is None or d < best[0]:
                    best = (d, c)
            if best and best[0] <= 10:
                gemeente = gemeente or best[1].gemeente
                postcode = postcode or best[1].postcode
        data = {
            "source": "overture", "ext_id": ext_id, "title": k.naam,
            "description": f"{_SOORT_TEKST[soort]}.", "start": None, "end": None,
            "is_permanent": True, "gemeente": gemeente, "postcode": postcode,
            "adres": k.adres, "lat": k.lat, "lng": k.lng,
            "age_min": 0, "age_max": 12, "categories": [],
            "subtype": soort, "indoor": soort != "zomerbar",
            "is_free": False, "price_info": [], "image_url": None,
            "source_url": k.website,
            "attribution": "Bron: Overture Maps Foundation (CDLA Permissive 2.0)",
            "venue_ext_id": ext_id, "venue_name": k.naam,
        }
        ev = upsert_event(data)
        if ev is not None:
            ev.curated = True
            aantal += 1
    return aantal
