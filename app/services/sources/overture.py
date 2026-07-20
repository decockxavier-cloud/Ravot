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
import json

from ...extensions import db
from ...models import HorecaKandidaat, PostcodeCentroid
from ...scoring import haversine_km
from .base import clean_postcode, upsert_event

# Ruwweg België (de kandidaten filteren we daarna op provincie/afstand).
BELGIE_BBOX = (2.50, 49.45, 6.45, 51.55)

# Overture-categoriecodes die eten/drinken betekenen. Korte woorden matchen
# we op héél woord (anders is een 'barber' plots een bar), langere op deelstring.
_CAT_KORT = {"bar", "pub", "cafe", "snack", "bistro"}

# Harde NEE: verkooppunten en productie die als "food" binnensluipen maar geen
# plek zijn waar een gezin op uitstap gaat. Slagers, bakkers, traiteurs,
# groothandel, fabrieken... — precies de bijvangst die Xavier signaleerde.
# Feest-leveranciers: NIET op de gezinskaart, WÉL bruikbaar als feestprospect.
# Deze horen niet in _CAT_UITSLUIT (dan gooien we ze weg) maar in hun eigen laag.
_CAT_FEEST = {
    "caterer", "catering", "event_venue", "banquet_hall", "event_planning",
    "party_supply", "party_equipment_rental", "event_space", "wedding_venue",
    "function_room", "reception_hall",
}

_CAT_UITSLUIT = {
    "bakery", "butcher", "butcher_shop", "deli", "delicatessen",
    "grocery", "supermarket", "convenience", "greengrocer", "farm_shop",
    "wholesale", "distributor", "food_processing", "manufacturing",
    "factory", "caterer", "catering", "food_court_stall", "market_stall",
    "liquor", "wine_shop", "brewery_equipment", "tobacco", "newsstand",
    "chocolatier", "confectionery", "patisserie_shop", "cheese_shop",
    "fishmonger", "food_bank", "meal_delivery", "meal_takeaway_only",
}
_CAT_LANG = ("restaurant", "coffee", "beer_garden", "brasserie", "ice_cream",
             "creperie", "pancake", "tea_house", "eat_and_drink", "fast_food")

_ZOMERBAR_HINTS = ("zomerbar", "zomer bar", "summer bar", "beach", "strandbar",
                   "pop-up", "popup", "bar d'été")
_WINTERBAR_HINTS = ("winterbar", "winter bar", "après-ski", "apres-ski",
                    "apres ski", "wintergloed", "winterdorp", "chalet")


def lijkt_winterbar(naam):
    n = (naam or "").lower()
    return any(h in n for h in _WINTERBAR_HINTS)


def _is_horeca(cat_primair, cat_alt=None):
    """Enkel de primaire categorie telt: via de alternatieve categorieën kwam
    te veel bijvangst binnen (tabakswinkels en supermarkten die ook 'cafe'
    als bijcategorie dragen)."""
    c = (cat_primair or "").lower()
    # Harde uitsluiting eerst: een bakkerij met bijcategorie 'cafe' blijft een
    # bakkerij. Exacte match op de categoriecode, niet op deelstring.
    if c in _CAT_UITSLUIT:
        return False
    if any(w in c for w in _CAT_UITSLUIT):
        return False
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
            c = (primair or "").lower()
            is_gezin = _is_horeca(primair, cats.get("alternate"))
            is_feest = (c in _CAT_FEEST or any(w in c for w in _CAT_FEEST))
            # Restaurant/brasserie kan óók feesten doen -> beide.
            if is_gezin and any(w in c for w in ("restaurant", "brasserie",
                                                 "banquet", "event")):
                is_feest = True
            if not is_gezin and not is_feest:
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
            # Geen adres én geen postcode? Overslaan — objectief rommelsignaal
            # en sowieso onvindbaar op de kaart voor een gezin.
            if not (adres.get("freeform") or adres.get("postcode")):
                continue
            conf = rec.get("confidence")
            # Strengere drempel (0.6): onder deze grens is de zaak vaak gesloten
            # of fout. De ✖-knop en gezinsmeldingen vangen de rest.
            if conf is not None and conf < 0.6:
                continue
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
            tel = rec.get("phones") or []
            k.telefoon = (tel[0][:40] if tel else None)
            mails = rec.get("emails") or []
            k.email = (mails[0][:255] if mails else None)
            k.is_feest = is_feest
            k.doel = "gezin" if is_gezin else "feest"
            k.zomerbar_hint = (not lijkt_winterbar(naam)
                               and lijkt_zomerbar(naam, primair))
            k.winterbar_hint = lijkt_winterbar(naam)
            k.confidence = rec.get("confidence")
            if bekeken % 50000 == 0:
                db.session.commit()
                log(f"  ... {bekeken} plaatsen bekeken, {nieuw} kandidaten")
    db.session.commit()
    # Registreer de run als bronstatus, zodat Overture gewoon in de
    # "Databronnen"-lijst van het Status-dashboard staat, naast uit en osm.
    from . import _set_status
    _set_status("overture", "done",
                result=f"{nieuw} nieuw, {bijgewerkt} bijgewerkt "
                       f"({bekeken} plaatsen bekeken)")
    log(f"Klaar: {nieuw} nieuwe en {bijgewerkt} bijgewerkte horeca-kandidaten "
        f"({bekeken} plaatsen bekeken).")
    return nieuw + bijgewerkt


def kandidaten_in_gebied(lat, lng, straal_km=5):
    """Modelobjecten binnen de straal (voor de AI-triage). Enkel gezin-doel:
    feestprospecten horen niet in de kaart-triage."""
    marge = straal_km / 90.0
    q = HorecaKandidaat.query.filter(
        HorecaKandidaat.lat.between(lat - marge, lat + marge),
        HorecaKandidaat.lng.between(lng - 1.6 * marge, lng + 1.6 * marge),
        db.or_(HorecaKandidaat.gesloten.is_(False),
               HorecaKandidaat.gesloten.is_(None)),
        db.or_(HorecaKandidaat.doel == "gezin", HorecaKandidaat.doel.is_(None)))
    return [k for k in q.limit(2000).all()
            if haversine_km(lat, lng, k.lat, k.lng) <= straal_km]


def zoek_kandidaten(lat, lng, straal_km=5):
    """Lokale zoektocht in de staging-tabel — geen netwerk, dus altijd snel."""
    marge = straal_km / 90.0     # ruwe graden-box, daarna exact op afstand
    q = HorecaKandidaat.query.filter(
        HorecaKandidaat.lat.between(lat - marge, lat + marge),
        HorecaKandidaat.lng.between(lng - 1.6 * marge, lng + 1.6 * marge),
        db.or_(HorecaKandidaat.gesloten.is_(False),
               HorecaKandidaat.gesloten.is_(None)),
        db.or_(HorecaKandidaat.doel == "gezin", HorecaKandidaat.doel.is_(None)))
    uit = []
    for k in q.limit(2000).all():
        km = haversine_km(lat, lng, k.lat, k.lng)
        if km > straal_km:
            continue
        uit.append({"ext_id": k.ext_id, "naam": k.naam, "amenity": k.categorie,
                    "adres": k.adres, "gemeente": k.gemeente,
                    "postcode": k.postcode, "lat": k.lat, "lng": k.lng,
                    "signalen": [], "buiten": False, "website": k.website,
                    "zomerbar": bool(k.zomerbar_hint),
                    "winterbar": bool(getattr(k, "winterbar_hint", False)),
                    "km": round(km, 1),
                    "ai": k.ai_advies, "ai_uitleg": k.ai_uitleg})
    # AI-'ja' bovenaan, daarna twijfel, dan op afstand — zo bevestig je eerst
    # de meest kansrijke zaken.
    volgorde = {"ja": 0, "twijfel": 1, None: 1, "nee": 2}
    uit.sort(key=lambda r: (volgorde.get(r["ai"], 1), r["km"]))
    return uit


_SOORT_TEKST = {"horeca": "Kindvriendelijke zaak",
                "zomerbar": "Gezinsvriendelijke zomerbar",
                "winterbar": "Gezinsvriendelijke winterbar"}


def importeer(ext_ids_met_soort, auto_nagekeken=False):
    """Gekozen kandidaten omzetten naar gecureerde fiches. Met
    auto_nagekeken=True gaan ze meteen live (voor de AI-ja-automaat);
    handmatige import laat nagekeken=False zodat ze in de werkvoorraad
    belanden."""
    centroids = None
    aantal = 0
    for ext_id, soort in ext_ids_met_soort:
        k = HorecaKandidaat.query.filter_by(ext_id=ext_id).first()
        if not k:
            continue
        if soort not in ("zomerbar", "winterbar"):
            soort = "horeca"
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
            "subtype": soort, "indoor": soort not in ("zomerbar",),
            "is_free": False, "price_info": [], "image_url": None,
            "source_url": k.website,
            "attribution": "Bron: Overture Maps Foundation (CDLA Permissive 2.0)",
            "venue_ext_id": ext_id, "venue_name": k.naam,
        }
        ev = upsert_event(data)
        if ev is not None:
            ev.curated = True
            if auto_nagekeken:
                ev.nagekeken = True
            aantal += 1
    return aantal


# --- AI-voorsortering ---------------------------------------------------------
def ai_triage(kandidaten, max_batch=None):
    """Laat het verrijkingsmodel (Ollama of cloud) inschatten welke kandidaten
    waarschijnlijk kindvriendelijk zijn. Werkt op naam + categorie + gemeente
    en bewaart het advies per kandidaat — beoordelen gebeurt dus maar één keer.
    Retourneert het aantal beoordeelde kandidaten."""
    from ...enrich import _generate, _parse_json
    from ...models import get_setting
    if max_batch is None:
        # Kleine batches voor het lokale model: cpu-Ollama wordt onbetrouwbaar
        # (halve JSON, timeouts) bij lange lijsten; de cloud kan grote happen aan.
        backend = (get_setting("verrijk_backend") or "ollama").lower()
        max_batch = 25 if backend == "cloud" else 8
    te_doen = [k for k in kandidaten if not k.ai_advies]
    beoordeeld = 0
    system = ("Je beoordeelt Vlaamse horecazaken voor een gezinsplatform. "
              "Vraag: is deze zaak waarschijnlijk aantrekkelijk en geschikt om "
              "met jonge kinderen (0-12) naartoe te gaan? Denk aan ijssalons, "
              "pannenkoekenhuizen, kinderboerderij-cafés, brasseries met "
              "speeltuin. Nachtcafés, sterrenrestaurants, shishabars en "
              "bruine kroegen zijn 'nee'. Bij onvoldoende info: 'twijfel'. "
              "Geef per zaak een korte motivatie (max 12 woorden, Nederlands). "
              "Antwoord ENKEL met JSON.")
    fouten_op_rij = 0
    for i in range(0, len(te_doen), max_batch):
        batch = te_doen[i:i + max_batch]
        lijst = [{"id": k.ext_id, "naam": k.naam,
                  "categorie": k.categorie or "onbekend",
                  "gemeente": k.gemeente or "?"} for k in batch]
        prompt = ("Beoordeel deze zaken. Geef ENKEL compacte, volledig "
                  "afgewerkte JSON: "
                  '{"beoordelingen": [{"id": "...", "advies": "ja|nee|twijfel", '
                  '"uitleg": "korte reden"}]}\n'
                  + json.dumps(lijst, ensure_ascii=False))
        try:
            # Ruime max_tokens: het afgekapte 500-token-antwoord was precies
            # de "7 van 357"-bug — halve JSON = bijna niets bewaard.
            data = _parse_json(_generate(prompt, system, max_tokens=3000)) or {}
        except Exception as exc:
            data = {}
            _triage_bezig["fout"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        per_id = {b.get("id"): b for b in
                  (data.get("beoordelingen") or []) if isinstance(b, dict)}
        gelukt = 0
        for k in batch:
            b = per_id.get(k.ext_id) or {}
            advies = b.get("advies")
            if advies in ("ja", "nee", "twijfel"):
                k.ai_advies = advies
                k.ai_uitleg = (str(b.get("uitleg") or "")).strip()[:200] or None
                gelukt += 1
            elif data.get("beoordelingen"):
                # Het model antwoordde wél maar sloeg deze zaak over of gaf
                # een ongeldig advies -> eerlijk "twijfel", anders blijft
                # dezelfde zaak elke run opnieuw de batch blokkeren.
                k.ai_advies = "twijfel"
                gelukt += 1
        # AI-ja => automatisch live. De beheerder koos hiervoor: snelheid nu,
        # met de "Zaken & fiches"-pagina als vangnet om achteraf bij te sturen.
        ja = [k.ext_id for k in batch if k.ai_advies == "ja"]
        if ja:
            geimporteerd = importeer([(e, "horeca") for e in ja],
                                     auto_nagekeken=True)
            _triage_bezig["live"] = _triage_bezig.get("live", 0) + geimporteerd
        beoordeeld += gelukt
        db.session.commit()
        if gelukt:
            fouten_op_rij = 0
            _triage_bezig["fout"] = None
        else:
            # Kapotte batch? Doorgaan met de volgende — pas na 3 missers op
            # rij stoppen (dan ligt de backend er echt uit).
            fouten_op_rij += 1
            if fouten_op_rij >= 3:
                break
    return beoordeeld


# --- AI-triage op de achtergrond ---------------------------------------------
# Het taalmodel (zeker lokale Ollama op cpu) denkt minuten na over een batch;
# gunicorn breekt webverzoeken na ~30s af. Daarom draait de beoordeling in een
# achtergrond-thread: de knop start ze, de pagina toont de voortgang.
import threading

_triage_lock = threading.Lock()
_triage_bezig = {"actief": False, "fout": None, "backend": None, "live": 0}


def triage_status():
    return dict(_triage_bezig)


def triage_actief():
    return _triage_bezig["actief"]


def start_ai_triage_achtergrond(app, lat, lng, straal_km, synchroon=False):
    """Start de AI-beoordeling van alle onbeoordeelde kandidaten in het gebied.
    Retourneert False als er al een beoordeling loopt (nooit dubbel werk)."""
    if not _triage_lock.acquire(blocking=False):
        return False
    _triage_bezig["actief"] = True

    def _werk():
        try:
            with app.app_context():
                from ...models import get_setting
                _triage_bezig["backend"] = (get_setting("verrijk_backend")
                                            or "ollama").lower()
                ks = kandidaten_in_gebied(lat, lng, straal_km)
                ai_triage(ks)          # batcht + commit per batch
                db.session.remove()
        except Exception:
            with app.app_context():
                app.logger.exception("AI-triage op de achtergrond faalde")
        finally:
            _triage_bezig["actief"] = False
            _triage_lock.release()

    if synchroon:                      # voor tests
        _werk()
        return True
    threading.Thread(target=_werk, daemon=True,
                     name="ravot-ai-triage").start()
    return True


def markeer_gesloten(ext_id):
    """Beheerder: 'deze zaak bestaat niet meer'. De kandidaat verdwijnt uit
    alle zoekresultaten, en een al geïmporteerde fiche gaat mee op verborgen —
    zo houdt curatie de databank eerlijker dan eender welke bron."""
    from ...models import Event
    k = HorecaKandidaat.query.filter_by(ext_id=ext_id).first()
    if k:
        k.gesloten = True
    ev = Event.query.filter_by(ext_id=ext_id).first()
    if ev:
        ev.hidden = True
    return k is not None or ev is not None


def beoordeel_voorraad(log=print, max_kandidaten=None):
    """Beoordeel de VOLLEDIGE nog-niet-beoordeelde voorraad in één run —
    heel Vlaanderen, zonder per gemeente te zoeken. AI-ja-zaken gaan meteen
    live (auto-import in ai_triage). Bedoeld voor de maandelijkse Overture-run
    en de eenmalige initiële vulling."""
    q = HorecaKandidaat.query.filter(
        HorecaKandidaat.ai_advies.is_(None),
        db.or_(HorecaKandidaat.gesloten.is_(False),
               HorecaKandidaat.gesloten.is_(None)))
    if max_kandidaten:
        q = q.limit(max_kandidaten)
    kandidaten = q.all()
    log(f"Te beoordelen: {len(kandidaten)} kandidaten.")
    if not kandidaten:
        return 0
    n = ai_triage(kandidaten)
    live = _triage_bezig.get("live", 0)
    log(f"Klaar: {n} beoordeeld, {live} automatisch live gezet.")
    return n


def verrijk_contact(log=print, max_afstand_km=0.15):
    """Vul ontbrekende website/telefoon op bestaande fiches (OSM-plekken,
    geïmporteerde horeca) aan met Overture-data, gematcht op naam + nabijheid.
    Nul curatiewerk: enkel lege velden worden ingevuld, bestaande blijven
    onaangeroerd."""
    from ...models import Event
    from ...scoring import haversine_km
    doel = Event.query.filter(
        Event.lat.isnot(None), Event.is_permanent.is_(True),
        db.or_(Event.source_url.is_(None), Event.telefoon.is_(None))).all()
    log(f"{len(doel)} fiches met een leeg contactveld.")
    # kandidaten met contactdata, licht gebucket op afgeronde coord voor snelheid
    kand = HorecaKandidaat.query.filter(
        db.or_(HorecaKandidaat.website.isnot(None),
               HorecaKandidaat.telefoon.isnot(None))).all()
    bucket = {}
    for k in kand:
        if k.lat is None:
            continue
        bucket.setdefault((round(k.lat, 2), round(k.lng, 2)), []).append(k)
    verrijkt = 0
    for ev in doel:
        naam = (ev.title or "").lower()
        beste = None
        for dl in (0, -0.01, 0.01):
            for dg in (0, -0.01, 0.01):
                for k in bucket.get((round(ev.lat, 2) + dl,
                                     round(ev.lng, 2) + dg), []):
                    if not naam or not k.naam:
                        continue
                    kn = k.naam.lower()
                    if naam not in kn and kn not in naam:
                        continue
                    d = haversine_km(ev.lat, ev.lng, k.lat, k.lng)
                    if d <= max_afstand_km and (beste is None or d < beste[0]):
                        beste = (d, k)
        if beste:
            k = beste[1]
            gewijzigd = False
            if not ev.source_url and k.website:
                ev.source_url = k.website[:500]; gewijzigd = True
            if not ev.telefoon and k.telefoon:
                ev.telefoon = k.telefoon[:40]; gewijzigd = True
            if gewijzigd:
                verrijkt += 1
    db.session.commit()
    log(f"{verrijkt} fiches verrijkt met contactgegevens uit Overture.")
    return verrijkt


def kuis_kandidaten(log=print):
    """Ruim reeds ingeladen kandidaten op die niet (meer) door de strengere
    regels raken: verkeerde categorie (bakker/slager/...), geen adres, of te
    lage confidence. Verwijdert enkel kandidaten die NOG GEEN fiche zijn — wat
    al geimporteerd en gecureerd is, blijft staan (daar beslist je ✖-knop)."""
    from ...models import Event
    fiche_ids = {r[0] for r in db.session.query(Event.ext_id).filter(
        Event.source == "overture", Event.ext_id.isnot(None)).all()}
    weg = 0
    for k in HorecaKandidaat.query.all():
        if k.ext_id in fiche_ids:
            continue                      # al een (mogelijk gecureerde) fiche
        # Feestprospecten mogen blijven ook al zijn ze geen kaart-horeca.
        geen_doel = not _is_horeca(k.categorie) and not getattr(k, "is_feest", False)
        slecht = (
            geen_doel
            or not (k.adres or k.postcode)
            or (k.confidence is not None and k.confidence < 0.6)
        )
        if slecht:
            db.session.delete(k)
            weg += 1
    db.session.commit()
    log(f"Opgekuist: {weg} kandidaten verwijderd (categorie/adres/confidence).")
    return weg
