"""Gezinsroute-generator (patch 188).

Ravot ontwerpt routes vanuit de vraag "waar blijft een kind gemotiveerd
trappen?" — niet vanuit landschap of erfgoed. De generator zoekt lussen door
het knooppuntennetwerk en rangschikt ze op wat er voor gezinnen langs ligt
(speeltuinen, ijssalons, belevingsplekken uit de eigen databank). Een lus
wordt pas een echte FietsRoute wanneer de redactie hem goedkeurt en — liefst —
zelf gereden heeft: auto-berekend, nooit auto-beslist.

Netwerkbron: elke GeoJSON met LineString-trajecten werkt. Knooppuntnummers
worden uit de eigenschappen gehaald wanneer die er zijn; anders worden
eindpunten binnen ±50 m samengeklikt tot knooppunten. Zo hangt de generator
niet aan één leverancierschema.
"""
import json
import time as _time

import requests

from ..extensions import db
from ..models import (Event, FietsRoute, Knooppunt, NetwerkSegment,
                      RouteVoorstel, get_int)
from ..scoring import haversine_km
from .routes_gis import (duur_suggestie, koppel_route, moeilijkheid_suggestie,
                         sample, vereenvoudig)

UA = {"User-Agent": "Ravot.be gezinsplatform (info@ravot.be)"}

# Scoregewichten: ravotten is de motor van een kinderfietstocht, smullen de
# beloning halverwege, beleven de kers. Per categorie een plafond zodat één
# speeltuinrijke wijk niet elke lus wint.
GEWICHT = {"ravotten": 3.0, "smullen": 2.0, "beleven": 1.0}
CAT_PLAFOND = 5


# ---------------------------------------------------------------- netwerk ---

def _nummer_uit_props(props):
    """Zoek een knooppuntnummer-paar in de bron-eigenschappen (schema's
    verschillen per leverancier); None als er niets bruikbaars is."""
    van = naar = None
    for k, v in (props or {}).items():
        kl = k.lower()
        if v is None:
            continue
        w = str(v).strip()
        if not w or len(w) > 4 or not w.replace(".", "").isdigit():
            continue
        w = w.split(".")[0]
        if any(t in kl for t in ("begin", "van", "start", "from")) and "nr" in kl or \
           kl in ("knooppuntbegin", "beginknooppunt"):
            van = w
        elif any(t in kl for t in ("eind", "naar", "end", "to")) and "nr" in kl or \
             kl in ("knooppunteind", "eindknooppunt"):
            naar = w
    return van, naar


def laad_netwerk_uit_geojson(data, netwerk="onbekend"):
    """Netwerktabellen (her)vullen uit een GeoJSON FeatureCollection.
    Eindpunten binnen ±50 m worden hetzelfde knooppunt."""
    NetwerkSegment.query.delete()
    Knooppunt.query.delete()
    db.session.flush()

    rooster = {}      # geklikte sleutel -> Knooppunt

    def _knoop(lat, lng, nummer=None):
        sleutel = (round(lat / 0.0005), round(lng / 0.0005))
        k = rooster.get(sleutel)
        if k is None:
            k = Knooppunt(nummer=nummer, lat=lat, lng=lng, netwerk=netwerk)
            db.session.add(k)
            db.session.flush()
            rooster[sleutel] = k
        elif nummer and not k.nummer:
            k.nummer = nummer
        return k

    n_seg = 0
    for f in data.get("features", []):
        geom = f.get("geometry") or {}
        lijnen = []
        if geom.get("type") == "LineString":
            lijnen = [geom.get("coordinates") or []]
        elif geom.get("type") == "MultiLineString":
            lijnen = geom.get("coordinates") or []
        for coords in lijnen:
            if len(coords) < 2:
                continue
            punten = [[c[1], c[0]] for c in coords]      # GeoJSON is lng,lat
            v_nr, n_nr = _nummer_uit_props(f.get("properties"))
            van = _knoop(punten[0][0], punten[0][1], v_nr)
            naar = _knoop(punten[-1][0], punten[-1][1], n_nr)
            if van.id == naar.id:
                continue
            afstand = 0.0
            for a, b in zip(punten, punten[1:]):
                afstand += haversine_km(a[0], a[1], b[0], b[1])
            db.session.add(NetwerkSegment(van_id=van.id, naar_id=naar.id,
                                          afstand_m=round(afstand * 1000),
                                          geometrie=punten))
            n_seg += 1
    # Nummerloze knooppunten krijgen een herkenbaar volgnummer
    for k in rooster.values():
        if not k.nummer:
            k.nummer = f"K{k.id}"
    db.session.commit()
    return len(rooster), n_seg


def nummer_knopen_uit_geojson(data, max_m=75):
    """Echte knooppuntnummers uit een puntenlaag aan de geklikte knooppunten
    hangen (koppeling op afstand). Retourneert het aantal genummerde knopen."""
    knopen = Knooppunt.query.all()
    if not knopen:
        return 0
    rooster = {}
    for k in knopen:
        rooster.setdefault((round(k.lat, 3), round(k.lng, 3)), []).append(k)
    hits = 0
    for f in data.get("features", []):
        geom = f.get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        lng, lat = (geom.get("coordinates") or [None, None])[:2]
        if lat is None:
            continue
        nummer = None
        for kk, v in (f.get("properties") or {}).items():
            kl = kk.lower()
            if any(t in kl for t in ("nummer", "knooppunt", "nodenr", "nr")):
                w = str(v).strip().split(".")[0]
                if w.isdigit() and len(w) <= 3:
                    nummer = w
                    break
        if not nummer:
            continue
        beste = None
        for dla in (-0.001, 0, 0.001):
            for dln in (-0.001, 0, 0.001):
                for k in rooster.get((round(lat + dla, 3),
                                      round(lng + dln, 3)), []):
                    d = haversine_km(lat, lng, k.lat, k.lng) * 1000
                    if d <= max_m and (beste is None or d < beste[0]):
                        beste = (d, k)
        if beste:
            beste[1].nummer = nummer
            hits += 1
    db.session.commit()
    return hits


def laad_netwerk_van_url(url, netwerk="Toerisme Vlaanderen"):
    antw = requests.get(url, headers=UA, timeout=180)
    antw.raise_for_status()
    knopen, segs = laad_netwerk_uit_geojson(antw.json(), netwerk)
    # Zelfde bron heeft doorgaans ook een knopenlaag ("traject" -> "knoop"):
    # die levert de échte knooppuntnummers, zodat je onderweg de bordjes kunt
    # volgen in plaats van interne K-codes.
    genummerd = 0
    if "traject" in url:
        try:
            antw2 = requests.get(url.replace("traject", "knoop"),
                                 headers=UA, timeout=180)
            antw2.raise_for_status()
            genummerd = nummer_knopen_uit_geojson(antw2.json())
        except Exception:
            pass
    return knopen, segs, genummerd


def _graaf():
    """adjacency: knoop_id -> [(buur_id, afstand_m, segment)]."""
    g = {}
    for s in NetwerkSegment.query.all():
        g.setdefault(s.van_id, []).append((s.naar_id, s.afstand_m, s))
        g.setdefault(s.naar_id, []).append((s.van_id, s.afstand_m, s))
    return g


# ------------------------------------------------------------------ lussen --

def _lussen_vanaf(start, graaf, knopen, min_m, max_m, per_start=40,
                  tijdslot=6.0):
    """Begrensde DFS: lussen van start terug naar start binnen [min, max] m.
    Snoeit op 'afgelegd + hemelsbreed terug' en verbiedt knoophergebruik."""
    uit = []
    sk = knopen[start]
    t0 = _time.monotonic()

    def stap(hier, afgelegd, pad_knopen, pad_segs):
        if _time.monotonic() - t0 > tijdslot or len(uit) >= per_start:
            return
        for buur, meters, seg in graaf.get(hier, []):
            nieuw = afgelegd + meters
            if nieuw > max_m:
                continue
            if buur == start:
                if nieuw >= min_m and len(pad_segs) >= 3:
                    uit.append((pad_knopen + [buur], pad_segs + [seg], nieuw))
                continue
            if buur in pad_knopen:
                continue
            bk = knopen[buur]
            terug = haversine_km(bk.lat, bk.lng, sk.lat, sk.lng) * 1000
            if nieuw + terug * 1.1 > max_m:
                continue                     # kan nooit meer op tijd thuis raken
            stap(buur, nieuw, pad_knopen + [buur], pad_segs + [seg])

    stap(start, 0.0, [start], [])
    return uit


def _geometrie_van(pad_knopen, pad_segs):
    punten = []
    for i, seg in enumerate(pad_segs):
        stuk = list(seg.geometrie or [])
        if seg.van_id != pad_knopen[i]:      # segment in omgekeerde richting
            stuk = stuk[::-1]
        if punten and stuk and punten[-1] == stuk[0]:
            stuk = stuk[1:]
        punten.extend(stuk)
    return punten


# ------------------------------------------------------------------- score --

def score_lus(geometrie):
    """Gezinswaarde van een lus, geteld uit de eigen databank."""
    lijn = sample([(p[0], p[1], None) for p in geometrie], stap_km=0.3)
    if not lijn:
        return 0.0, {}
    lats = [p[0] for p in lijn]
    lngs = [p[1] for p in lijn]
    marge = 0.006
    from ..types import groep_van
    kandidaten = (Event.query
                  .filter(Event.is_permanent.is_(True),
                          Event.pending.is_(False), Event.hidden.is_(False),
                          Event.lat.between(min(lats) - marge, max(lats) + marge),
                          Event.lng.between(min(lngs) - marge, max(lngs) + marge))
                  .limit(400).all())
    tel = {"ravotten": 0, "smullen": 0, "beleven": 0}
    bezet_km = set()
    for ev in kandidaten:
        beste = None
        for i, (la, ln, _) in enumerate(lijn):
            d = haversine_km(ev.lat, ev.lng, la, ln)
            if beste is None or d < beste[0]:
                beste = (d, i)
        if beste and beste[0] * 1000 <= 400:
            g = groep_van(ev)
            if g in tel:
                kwal = 0.5 + (ev.quality or 0) / 100.0     # 0.5 .. 1.5
                tel[g] += min(kwal, 1.5)
                bezet_km.add(int(beste[1] * 0.3 // 3))     # 3km-vak
    # Afnemende meeropbrengst i.p.v. een hard plafond: in een dichte stad
    # botste elke lus tegen het maximum en werd alles ~dezelfde score. Nu
    # telt de 25e speeltuin nog steeds iets, maar veel minder dan de 3e —
    # lussen blijven onderscheidbaar zonder dat één wijk alles wint.
    import math as _m
    score = sum(GEWICHT[g] * CAT_PLAFOND * (1 - _m.exp(-tel[g] / CAT_PLAFOND))
                for g in tel)
    # Spreidingsbonus (zwaarder): stops verdeeld over de lus > één kluitje
    vakken = max(1, int(len(lijn) * 0.3 // 3))
    score += 3.0 * (len(bezet_km) / vakken)
    detail = {g: round(tel[g]) for g in tel}
    detail["spreiding"] = round(len(bezet_km) / vakken, 2)
    return round(score, 2), detail


# --------------------------------------------------------------- generator --

def _centrum_van_gemeente(gemeente):
    rijen = (db.session.query(db.func.avg(Event.lat), db.func.avg(Event.lng))
             .filter(Event.gemeente.ilike(gemeente),
                     Event.lat.isnot(None)).first())
    if rijen and rijen[0] is not None:
        return float(rijen[0]), float(rijen[1])
    return None


def genereer_voorstellen(gemeente, top=8):
    """Zoek de beste gezinslussen rond een gemeente en zet ze als voorstel
    in de redactionele wachtrij. Retourneert (bewaard, onderzocht)."""
    centrum = _centrum_van_gemeente(gemeente)
    if not centrum:
        return 0, 0
    min_m = (get_int("generator_min_km", 12) or 12) * 1000
    max_m = (get_int("generator_max_km", 25) or 25) * 1000
    knopen = {k.id: k for k in Knooppunt.query.all()}
    if not knopen:
        return 0, 0
    graaf = _graaf()
    starts = sorted(
        (k for k in knopen.values()
         if haversine_km(k.lat, k.lng, centrum[0], centrum[1]) <= 3.5),
        key=lambda k: haversine_km(k.lat, k.lng, centrum[0], centrum[1]))[:12]

    bestaand = {tuple(sorted(v.knooppunten or []))
                for v in RouteVoorstel.query.filter_by(gemeente=gemeente).all()}
    gezien = set(bestaand)
    kandidaten = []
    for st in starts:
        for pad_knopen, pad_segs, meters in _lussen_vanaf(
                st.id, graaf, knopen, min_m, max_m):
            sleutel = tuple(sorted(knopen[i].nummer for i in pad_knopen[:-1]))
            if sleutel in gezien:
                continue
            gezien.add(sleutel)
            geometrie = _geometrie_van(pad_knopen, pad_segs)
            score, detail = score_lus(geometrie)
            kandidaten.append((score, detail, pad_knopen, geometrie, meters))
    kandidaten.sort(key=lambda x: -x[0])
    # Diversiteit: een top vol varianten van dezelfde lus helpt de redactie
    # niet. Gulzig kiezen, maar kandidaten die >55% van hun knooppunten delen
    # met een al gekozen lus slaan we over.
    gekozen = []
    for kand in kandidaten:
        knoopset = set(kand[2][:-1])
        if any(len(knoopset & g) / max(1, len(knoopset | g)) > 0.55
               for g in gekozen):
            continue
        gekozen.append(knoopset)
        yield_kand = kand
        # bewaar meteen
        score, detail, pad_knopen, geometrie, meters = yield_kand
        db.session.add(RouteVoorstel(
            gemeente=gemeente,
            knooppunten=[knopen[i].nummer for i in pad_knopen],
            # Fijne vereenvoudiging (patch 196): alleen collineaire punten
            # weg. Zo volgt de GPX de echte weg i.p.v. hoeken af te snijden
            # met sprongen van honderden meters.
            geometrie=vereenvoudig([(p[0], p[1], None) for p in geometrie],
                                   tolerantie=0.00006, maxi=4000),
            afstand_km=round(meters / 1000, 1),
            score=score, score_detail=detail))
        if len(gekozen) >= top:
            break
    db.session.commit()
    return len(gekozen), len(kandidaten)





def meet_klimmeters(geometrie, stap_km=0.5):
    """Echte klimmeters van een route via de gratis Open-Meteo hoogte-API
    (patch 194). Bemonstert de lijn om de ~500 m, telt de positieve
    hoogteverschillen op en filtert meetruis (< 2 m per stap telt niet).
    Retourneert een int of None bij een API-fout — geen data is eerlijker
    dan verzonnen data, dus de aanroeper mag NIET terugvallen op 0."""
    punten = sample([(p[0], p[1], None) for p in (geometrie or [])],
                    stap_km=stap_km)
    if len(punten) < 2:
        return None
    punten = punten[:100]                      # API-limiet per aanroep
    try:
        antw = requests.get(
            "https://api.open-meteo.com/v1/elevation",
            params={"latitude": ",".join(f"{p[0]:.5f}" for p in punten),
                    "longitude": ",".join(f"{p[1]:.5f}" for p in punten)},
            headers=UA, timeout=30)
        antw.raise_for_status()
        hoogtes = antw.json().get("elevation") or []
    except Exception:
        return None
    if len(hoogtes) < 2:
        return None
    stijging = 0.0
    for a, b in zip(hoogtes, hoogtes[1:]):
        d = (b or 0) - (a or 0)
        if d >= 2:                             # ruisdrempel
            stijging += d
    return int(stijging)


def schrijf_gpx(route):
    """GPX-bestand maken uit de routegeometrie (patch 191), zodat elke
    gepromoveerde lus meteen op een fietscomputer of in een app kan."""
    import os
    from xml.sax.saxutils import escape
    punten = route.geometrie or []
    if not punten:
        return None
    regels = ['<?xml version="1.0" encoding="UTF-8"?>',
              '<gpx version="1.1" creator="Ravot.be" '
              'xmlns="http://www.topografix.com/GPX/1/1">',
              "  <metadata>",
              f"    <name>{escape(route.titel or 'Ravot-route')}</name>",
              "    <link href=\"https://ravot.be\">"
              "<text>Ravot.be — gezinsuitstappen</text></link>",
              "  </metadata>",
              "  <trk>",
              f"    <name>{escape(route.titel or 'Ravot-route')}</name>",
              "    <trkseg>"]
    for p in punten:
        regels.append(f'      <trkpt lat="{p[0]:.6f}" lon="{p[1]:.6f}"/>')
    regels += ["    </trkseg>", "  </trk>", "</gpx>"]
    os.makedirs("/data/uploads/gpx", exist_ok=True)
    naam = f"route-{route.slug}.gpx"
    with open(f"/data/uploads/gpx/{naam}", "w", encoding="utf-8") as f:
        f.write("\n".join(regels))
    route.gpx_bestand = naam
    return naam


def _hoogtepunten(route, maxi=8):
    """Wat er langs de route ligt, klaar voor de AI-tekst (patch 197):
    - ravotten/beleven: naam mét typelabel, zodat de AI beschrijft wat een
      plek ÍS en niets afleidt uit de naam (een 'Geitenpark' zonder geiten);
    - smullen: alleen betalende Ravot-partners bij naam; de rest telt enkel
      mee als aantal ("onderweg valt er iets te eten of drinken")."""
    from ..models import RouteBuurt, utcnow
    from ..types import TYPES, groep_van
    TYPE_LABELS = {k: v[1].split("(")[0].strip() for k, v in TYPES.items()}
    nu = utcnow().replace(tzinfo=None)
    uit = {"ravotten": [], "beleven": [], "smullen_partners": [],
           "smullen_aantal": 0}
    rijen = (RouteBuurt.query.filter_by(route_id=route.id)
             .order_by(RouteBuurt.route_km.asc()).all())
    for b in rijen:
        ev = b.event
        if ev is None or not ev.title:
            continue
        g = groep_van(ev)
        naam = ev.title.split("—")[0].strip()
        soort = TYPE_LABELS.get(ev.subtype or "", "") if TYPE_LABELS else ""
        etiket = f"{naam} ({soort})" if soort else naam
        if g == "smullen":
            uit["smullen_aantal"] += 1
            partner = bool(ev.partner_until and ev.partner_until > nu)
            if partner and naam not in uit["smullen_partners"] \
                    and len(uit["smullen_partners"]) < 4:
                uit["smullen_partners"].append(naam)
        elif g in ("ravotten", "beleven") and len(uit[g]) < maxi:
            if etiket not in uit[g]:
                uit[g].append(etiket)
    return uit


def _naam_problemen(naam, route, bestaande):
    """Waarom een voorgestelde naam niet goed genoeg is (lege lijst = prima).
    Houdt de rubriek gevarieerd: geen gemeentenaam-sjablonen, geen generieke
    achtervoegsels, geen naam die op een bestaande lijkt."""
    problemen = []
    n = naam.lower()
    gem = (route.gemeente or "").lower()
    # ook de bijvoeglijke vorm vangen ("Roeselaarse", "Izegemse"): de stam
    # van de gemeentenaam volstaat als signaal
    if gem and (gem in n or (len(gem) >= 6 and gem[:6] in n)):
        problemen.append(f"bevat de gemeentenaam '{route.gemeente}'")
    for verboden in ("fietstocht", "fietsroute", "fietslus", " route",
                     " tocht", "wandeling"):
        if verboden in n:
            problemen.append(f"bevat het generieke woord '{verboden.strip()}'")
            break
    kern = {w for w in n.replace("-", " ").split()
            if len(w) > 4 and w not in ("kleine", "grote")}
    for titel in bestaande:
        t = titel.lower()
        tkern = {w for w in t.replace("-", " ").split() if len(w) > 4}
        if n in t or t in n or (kern and kern & tkern):
            problemen.append(f"lijkt te veel op de bestaande route '{titel}'")
            break
    return problemen


def ai_titel_en_beschrijving(route):
    """Vraag de verrijkings-AI om een speelse naam + korte beschrijving,
    geïnspireerd op wat er écht langs de route ligt. De naam moet de rubriek
    gevarieerd houden: bestaande namen worden meegegeven om herhaling te
    vermijden, en een zwak voorstel krijgt één herkansing met uitleg.
    Retourneert (titel, beschrijving) of None."""
    from ..enrich import _generate
    from ..models import FietsRoute
    h = _hoogtepunten(route)
    langs = []
    if h["ravotten"]:
        langs.append("speel- en ravotplekken: " + ", ".join(h["ravotten"][:5]))
    if h["beleven"]:
        langs.append("te beleven: " + ", ".join(h["beleven"][:3]))
    if h["smullen_partners"]:
        langs.append("eet-/drinkstops die je bij naam mag noemen: "
                     + ", ".join(h["smullen_partners"]))
    if h["smullen_aantal"]:
        langs.append(f"daarnaast {h['smullen_aantal']} plekjes om iets te "
                     "eten of te drinken (GEEN namen noemen)")
    bestaande = [t for (t,) in db.session.query(FietsRoute.titel)
                 .filter(FietsRoute.id != route.id).all() if t]

    def _prompt(feedback=""):
        regels = (
            "Bedenk een naam en korte beschrijving voor een gezinsfietslus op "
            "het knooppuntennetwerk.\n"
            f"Gemeente: {route.gemeente or 'onbekend'} · Lengte: "
            f"{route.afstand_km:g} km · Lus langs "
            f"{'; '.join(langs) or 'landelijke wegen'}.\n"
            + (f"Bestaande routenamen (vermijd elke gelijkenis): "
               f"{'; '.join(bestaande[:12])}.\n" if bestaande else "")
            + "\nRegels voor de naam:\n"
            "- Kies ÉÉN concrete verrassing van onderweg als kapstok: een "
            "plek, dier, lekkernij of beeld — niet de gemeente.\n"
            "- De gemeentenaam mag NIET in de naam.\n"
            "- Verboden woorden: route, tocht, fietstocht, fietsroute, "
            "fietslus.\n"
            "- Maximaal 4 woorden. Woordspeling, alliteratie of een "
            "verkleinwoord mag; denk aan namen als 'IJsjessafari', "
            "'Vossenjacht aan de Mandel', 'Van Schommel tot Kasteel', "
            "'Knuffelgeitenronde'.\n"
            "- Elke route in de rubriek moet anders klinken: geen sjabloon, "
            "geen herhaling.\n"
            "\nDe beschrijving: 60-110 woorden, warm en concreet, noemt 2-3 "
            "speel- of belevingsplekken bij naam. STRIKT:\n"
            "- Beschrijf een plek enkel via het type tussen haakjes; leid "
            "NIETS af uit de naam zelf. Een 'Geitenpark (speeltuin)' is een "
            "speeltuin — schrijf dus niet dat er geiten zijn.\n"
            "- Eet- en drinkzaken: noem alleen de zaken die expliciet bij "
            "naam genoemd mogen worden. Voor de rest volstaat dat er "
            "onderweg iets te eten of te drinken valt, zonder namen.\n"
            "- Verzin geen kastelen, dieren, bezienswaardigheden of andere "
            "details die hierboven niet staan. Twijfel = weglaten.\n"
            "Geen superlatievenregen.\n"
            + feedback +
            "\nAntwoord exact zo:\nNAAM: ...\nBESCHRIJVING: ...")
        return regels

    systeem = ("Je bent de redacteur van Ravot.be, een warm Vlaams "
               "gezinsplatform. Schrijf in het Nederlands.")

    def _vraag(p):
        try:
            ruw = _generate(p, systeem, max_tokens=400) or ""
        except Exception:
            return None, None
        naam = besch = None
        for regel in ruw.splitlines():
            r = regel.strip()
            if r.upper().startswith("NAAM:"):
                naam = r[5:].strip().strip('"')[:80]
            elif r.upper().startswith("BESCHRIJVING:"):
                besch = r[13:].strip()[:1000]
        return naam, besch

    # Niet-partner-horeca mag nergens bij naam vallen (patch 197): dat zou
    # gratis reclame zijn naast betalende partners — en het is de kern van
    # het partnermodel dat vermelding een partnervoordeel is.
    from ..models import RouteBuurt, utcnow
    from ..types import groep_van as _gv
    nu = utcnow().replace(tzinfo=None)
    verboden_namen = []
    for b in RouteBuurt.query.filter_by(route_id=route.id).all():
        ev = b.event
        if ev is not None and ev.title and _gv(ev) == "smullen" \
                and not (ev.partner_until and ev.partner_until > nu):
            kern = ev.title.split("—")[0].strip()
            if len(kern) >= 5:
                verboden_namen.append(kern)

    def _horeca_problemen(naam, besch):
        tekst = f"{naam} {besch}".lower()
        return [f"noemt de niet-partnerzaak '{n}'" for n in verboden_namen
                if n.lower() in tekst]

    naam, besch = _vraag(_prompt())
    if not (naam and besch):
        return None
    problemen = _naam_problemen(naam, route, bestaande) \
        + _horeca_problemen(naam, besch)
    if problemen:
        naam2, besch2 = _vraag(_prompt(
            f"\nJe vorige voorstel '{naam}' was niet goed: "
            + "; ".join(problemen) + ". Doe een wezenlijk ander voorstel.\n"))
        if naam2 and besch2:
            p2 = _naam_problemen(naam2, route, bestaande) \
                + _horeca_problemen(naam2, besch2)
            if not p2:
                return naam2, besch2
            if not _horeca_problemen(naam2, besch2):
                return naam2, besch2      # stijlpuntjes mag de redactie doen
        # Niet-partnerreclame blijft erin zitten: dan liever géén AI-tekst.
        if _horeca_problemen(naam, besch):
            return None
    return naam, besch


def regio_suggestie(route):
    """Regio afleiden uit bestaande routes (patch 197): eerst dezelfde
    gemeente, anders de dichtstbijzijnde route (≤ 30 km) met een regio.
    Eén keer handmatig invullen per streek volstaat dus."""
    q = FietsRoute.query.filter(FietsRoute.id != route.id,
                                FietsRoute.regio.isnot(None),
                                FietsRoute.regio != "")
    if route.gemeente:
        zelfde = q.filter(FietsRoute.gemeente.ilike(route.gemeente)).first()
        if zelfde:
            return zelfde.regio
    if route.start_lat is None:
        return None
    beste = None
    for r in q.all():
        if r.start_lat is None:
            continue
        d = haversine_km(route.start_lat, route.start_lng,
                         r.start_lat, r.start_lng)
        if d <= 30 and (beste is None or d < beste[0]):
            beste = (d, r.regio)
    return beste[1] if beste else None


def promoveer(voorstel):
    """Voorstel -> FietsRoute (pending: de redactie werkt hem af en rijdt hem
    na). Koppelt meteen de buurt zodat het pauzeplan direct klopt."""
    from .sources.base import slugify
    titel = f"Gezinslus {voorstel.gemeente} — {voorstel.afstand_km:g} km"
    slug = slugify(f"{titel}-{voorstel.id}")
    afstand = voorstel.afstand_km or 0
    geometrie = voorstel.geometrie or []
    route = FietsRoute(
        titel=titel, slug=slug, pending=True, hidden=False,
        gemeente=voorstel.gemeente, afstand_km=afstand,
        duur_min=duur_suggestie(afstand),
        is_lus=True, geometrie=geometrie,
        start_lat=geometrie[0][0] if geometrie else None,
        start_lng=geometrie[0][1] if geometrie else None,
        bron_naam="Ravot-routegenerator",
        routebeschrijving="Knooppunten: " + " – ".join(voorstel.knooppunten or []),
    )
    if geometrie:
        lats = [p[0] for p in geometrie]
        lngs = [p[1] for p in geometrie]
        route.bbox_n, route.bbox_z = max(lats), min(lats)
        route.bbox_o, route.bbox_w = max(lngs), min(lngs)
    # Echte klimmeters meten (patch 194): "vlak" mag geen aanname zijn.
    # Lukt de meting niet, dan blijft de moeilijkheid leeg voor de redactie.
    klim = meet_klimmeters(voorstel.geometrie)
    if klim is not None:
        route.hoogte_m = klim
        route.moeilijkheid = moeilijkheid_suggestie(afstand, klim)
    else:
        # Let op: None triggert de kolomdefault "vlak" — en dat is nu net de
        # aanname die we niet willen maken. Leeg = "weten we niet".
        route.moeilijkheid = ""
    db.session.add(route)
    db.session.flush()
    koppel_route(route)
    # Nu de buurt bekend is: speelse naam + beschrijving door de AI (valt
    # stil terug op de werktitel als de AI niet beschikbaar is)...
    ai = ai_titel_en_beschrijving(route)
    if ai:
        naam, besch = ai
        route.titel = f"{naam}"
        route.slug = slugify(f"{naam}-{voorstel.gemeente}-{voorstel.id}")
        # Warme tekst = inspiratietekst (de open kaart op de routepagina);
        # de knooppuntenreeks blijft in de stap-voor-stap-routebeschrijving.
        route.beschrijving = besch
    # ...en een GPX-bestand, meteen downloadbaar voor de testrit.
    schrijf_gpx(route)
    if not route.regio:
        route.regio = regio_suggestie(route)
    voorstel.status = "gepromoveerd"
    voorstel.route_id = route.id
    db.session.commit()
    return route
