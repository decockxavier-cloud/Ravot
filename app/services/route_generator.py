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


def laad_netwerk_van_url(url, netwerk="Toerisme Vlaanderen"):
    antw = requests.get(url, headers=UA, timeout=120)
    antw.raise_for_status()
    return laad_netwerk_uit_geojson(antw.json(), netwerk)


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
    score = sum(GEWICHT[g] * min(tel[g], CAT_PLAFOND) for g in tel)
    # Spreidingsbonus: stops verdeeld over de lus > alles op één kluitje
    vakken = max(1, int(len(lijn) * 0.3 // 3))
    score += 2.0 * (len(bezet_km) / vakken)
    detail = {g: round(tel[g], 1) for g in tel}
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
    for score, detail, pad_knopen, geometrie, meters in kandidaten[:top]:
        db.session.add(RouteVoorstel(
            gemeente=gemeente,
            knooppunten=[knopen[i].nummer for i in pad_knopen],
            geometrie=vereenvoudig([(p[0], p[1], None) for p in geometrie]),
            afstand_km=round(meters / 1000, 1),
            score=score, score_detail=detail))
    db.session.commit()
    return min(top, len(kandidaten)), len(kandidaten)


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
        moeilijkheid=moeilijkheid_suggestie(afstand, 0),
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
    db.session.add(route)
    db.session.flush()
    koppel_route(route)
    voorstel.status = "gepromoveerd"
    voorstel.route_id = route.id
    db.session.commit()
    return route
