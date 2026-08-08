"""Publieke routes — de kern-app. Werkt mét account én anoniem (zero-friction).

SEO-architectuur (SEO/GEO-plan):
  /                       Vandaag (antwoord als startpunt)
  /weekend                Dit weekend
  /verkennen              Kaart
  /<gemeente>             programmatic gemeentepagina
  /<gemeente>/<facet>     dit-weekend | vandaag | gratis | binnen | peuters | kleuters | 6-12
  /e/<slug>               eventpagina (JSON-LD Event)
  /uitstap/<slug>         permanente editie-reekspagina (301-doel voor afgelopen events)
  /sitemap.xml /robots.txt /llms.txt
"""
import re
import json
from datetime import datetime, timedelta

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect,
                   render_template, request, session, url_for, Response)

from ..extensions import db, limiter
from ..models import (get_int, get_setting, DagUitstap, Event, Family,
                      Interaction, PostcodeCentroid, Review, SavedEvent, Share,
                      Connection)
from ..pricing import aggregate_ravotscore, euro_indicator, family_price
from ..scoring import Profile, score_event
from ..media import poi_image
from ..types import is_commercieel
from .. import seo

bp = Blueprint("public", __name__)


LEEFTIJDEN = [("0-3", "👶 0–3 jaar", 0, 3), ("4-6", "🧒 4–6 jaar", 4, 6),
             ("7-9", "🧑 7–9 jaar", 7, 9), ("10-12", "🎒 10–12 jaar", 10, 12),
             ("13-17", "🎧 tiener", 13, 17)]




def _factor(key, fallback):
    try:
        return float(get_setting(key))
    except (TypeError, ValueError):
        return fallback


def partner_zichtbaar(ev, now=None):
    """⭐-rechten volgens het plan (partner/combi/legacy)."""
    from ..mollie import is_zichtbaar_partner
    return is_zichtbaar_partner(ev, now)


def partner_actief(ev, now=None):
    now = now or datetime.utcnow()
    return bool(ev.partner_until and ev.partner_until > now)


def score_zichtbaar(ev):
    """Ravotscore-afspraak (2c): de score is en blijft van de community.
    Openbare plekken (speeltuin, park, museum, event, ...) tonen ze altijd.
    Commerciële plekken (horeca, indoor-speeltuin, pretpark, ...) tonen de
    badge enkel met een actieve Ravot Partner-status."""
    if not is_commercieel(ev):
        return True
    return partner_actief(ev)


def commercieel_factor(ev):
    """Rankingfactor voor commerciële plekken: lichte bonus mét Partner,
    lichte demping zonder (3c). Niet-commercieel: neutraal (1.0)."""
    if not is_commercieel(ev):
        return 1.0
    if partner_actief(ev):
        return _factor("partner_score_bonus", 1.10)
    return _factor("geen_partner_malus", 0.90)


def _profiel_plaats(fam):
    """(plaatsnaam, lat, lng) van de woonplaats: gezinspostcode of gastpostcode."""
    postcode = (fam.postcode if fam else None) or guest_profile().get("postcode")
    if not postcode:
        return None, None, None
    centroid = db.session.get(PostcodeCentroid, postcode)
    if not centroid:
        return None, None, None
    return centroid.gemeente or postcode, centroid.lat, centroid.lng


def weerbericht(scope, fam, centrum=None, plaats=None):
    """Weerbericht voor op de lijstpagina's: op de woonplaats, of op de
    gezochte activiteitenplaats (centrum). None als weer uitstaat of onbekend.
    Dag = de eerste dag van het gekozen venster (weekend → zaterdag)."""
    from ..models import get_bool
    if not get_bool("weer_aan"):
        return None
    if centrum:
        lat, lng = centrum
    else:
        p, lat, lng = _profiel_plaats(fam)
        plaats = plaats or p
    if lat is None:
        return None
    start, _ = window(scope if scope in ("vandaag", "deze-week", "weekend") else "vandaag")
    dag = max(start.date(), datetime.utcnow().date())
    from ..weer import voorspelling
    v = voorspelling(lat, lng, dag)
    if not v:
        return None
    v = dict(v)
    v["plaats"] = plaats
    v["vandaag"] = dag == datetime.utcnow().date()
    dagen = ["maandag", "dinsdag", "woensdag", "donderdag",
             "vrijdag", "zaterdag", "zondag"]
    v["dag_label"] = "Vandaag" if v["vandaag"] else \
        f"{dagen[dag.weekday()].capitalize()} {dag.day}/{dag.month}"
    return v

# Het tijdvenster (hoe ver vooruit) is instelbaar via de admin: 'toon_maanden_vooruit'.


def geldig_venster(now=None):
    """(ondergrens, bovengrens) voor events die getoond mogen worden.
    Ondergrens: 6u geleden (nog-bezige events tellen mee).
    Bovengrens: instelbaar via de admin (default 24 maanden vooruit)."""
    from ..models import get_int
    now = now or datetime.utcnow()
    maanden = get_int("toon_maanden_vooruit", 24) or 24
    return now - timedelta(hours=6), now + timedelta(days=maanden * 31)


def bron_filter(q):
    """Bronnen die de beheerder publiek onzichtbaar zette wegfilteren.
    Nu enkel de UiT-laag (schakelaar 'uit_zichtbaar'): uitzetten verbergt de
    events publiek, maar data én verrijking blijven volledig bewaard —
    aanzetten maakt alles in één klik weer zichtbaar. Het beheer ziet altijd
    alles (dit filter zit enkel op de publieke routes)."""
    from ..models import get_bool
    if not get_bool("uit_zichtbaar"):
        q = q.filter(Event.source != "uit")
    return q


def geldige_events(query, now=None):
    """Beperk een Event-query tot het geldige venster (niet voorbij, niet absurd ver).
    Een event is 'voorbij' als zijn EINDE achter de ondergrens ligt — zo blijven
    lopende activiteiten (bv. hele-dag, al bezig) gewoon zichtbaar."""
    onder, boven = geldig_venster(now)
    # Permanente POI's (speeltuinen, attracties) hebben geen datum en zijn
    # altijd geldig; gedateerde events moeten binnen het venster vallen.
    # Kampen horen NIET in de gewone lijsten/kaart — die hebben hun eigen
    # onderdeel (/kampen), net als feestjes.
    query = bron_filter(query)
    return query.filter(
        Event.hidden.is_(False), Event.pending.is_(False),      # verborgen dubbels nooit tonen
        db.or_(Event.is_kamp.is_(False), Event.is_kamp.is_(None)),
        db.or_(
            Event.is_permanent.is_(True),
            db.and_((Event.end >= onder) | (Event.start >= onder),
                    Event.start <= boven),
        ))


def curatie_filter(query, toon_alles=False):
    """Als 'enkel_gecureerd' aanstaat, toon publiek enkel door mensen
    goedgekeurde ('Ravot-waardige') fiches. toon_alles=True is de
    ontsnappingsklep waarmee een bezoeker bewust ook de rest ziet."""
    from ..models import get_bool
    if toon_alles or not get_bool("enkel_gecureerd"):
        return query
    return query.filter(Event.curated.is_(True))


def type_filter(query):
    """Weer activiteittypes die de beheerder publiek verborgen heeft
    (setting 'verborgen_types'). Werkt op subtype (vaste plekken) en, voor
    gedateerde events zonder subtype, op de categorie."""
    from ..types import verborgen_type_codes, _CAT_NAAR_EV
    hidden = verborgen_type_codes()
    if not hidden:
        return query
    sub_hidden = [c for c in hidden if not c.startswith("ev_")]
    if sub_hidden:
        query = query.filter(db.or_(Event.subtype.is_(None),
                                    ~Event.subtype.in_(sub_hidden)))
    ev_hidden_cats = [cat for cat, code in _CAT_NAAR_EV.items() if code in hidden]
    for cat in ev_hidden_cats:
        query = query.filter(db.or_(
            Event.subtype.isnot(None),
            ~db.func.lower(db.cast(Event.categories, db.String)).like(f'%"{cat}"%')))
    return query


def kwaliteit_filter(query):
    """Weer fiches onder de kwaliteitsdrempel uit lijsten/gemeentepagina's.
    NULL (nog niet berekend) blijft zichtbaar; de kaart gebruikt dit NIET
    (daar is een kaal speelpleintje met enkel coordinaten nog nuttig)."""
    drempel = get_int("kwaliteit_min_lijst", 30)
    if drempel <= 0:
        return query
    return query.filter(db.or_(Event.quality.is_(None), Event.quality >= drempel))


def _zoek_centrum(zoek, strict=False):
    """Zet een zoekterm (gemeente of postcode) om naar een (lat, lng)-middelpunt.
    Robuust: postcodes uit de statische tabel, plaatsnamen via centroids of
    (als laatste redmiddel) een geocoder met cache. None als er niets past."""
    from .. import geo
    return geo.zoek_centrum(zoek, strict=strict)


def _filter_buurt(rows, centrum, straal_km=20):
    """Houd enkel events binnen straal_km rond het centrum (op afstand, niet op naam)."""
    from ..scoring import haversine_km
    lat0, lng0 = centrum
    uit = []
    for r in rows:
        e = r["event"] if isinstance(r, dict) else r
        if e.lat is None or e.lng is None:
            continue
        if haversine_km(lat0, lng0, e.lat, e.lng) <= straal_km:
            uit.append(r)
    return uit

FACETS = {
    "vandaag": "vandaag", "dit-weekend": "dit weekend", "gratis": "gratis",
    "binnen": "binnen (regenweer)", "peuters": "voor peuters",
    "kleuters": "voor kleuters", "6-12": "voor 6-12 jaar",
}
FACET_AGES = {"peuters": (0, 3), "kleuters": (3, 6), "6-12": (6, 12)}


# ------------------------------------------------------------ profielcontext --

def current_family():
    fid = session.get("family_id")
    return db.session.get(Family, fid) if fid else None


def guest_profile():
    """Anonieme modus: postcode+leeftijden uit een lokaal cookie-achtig sessieveld."""
    return session.get("guest", {})


def gast_actief():
    """True als er een anoniem 'personaliseer'-profiel is maar geen account.
    Bepaalt of we de banner met Aanpassen/Wissen tonen i.p.v. Personaliseer."""
    return bool(guest_profile().get("postcode")) and current_family() is None


def _veilig_int(waarde, standaard):
    try:
        return int(str(waarde).strip() or standaard)
    except (ValueError, TypeError):
        return standaard


def build_profile():
    from ..geo import postcode_coord
    fam = current_family()
    if fam:
        coord = postcode_coord(fam.postcode)
        return Profile(
            child_ages=fam.child_ages(),
            lat=coord[0] if coord else None,
            lng=coord[1] if coord else None,
            radius_km=fam.radius_km, budget_pref=fam.budget_pref,
            interest_weights={i.category: i.weight for i in fam.interests},
        ), fam
    guest = guest_profile()
    coord = postcode_coord(guest.get("postcode", "")) if guest else None
    ages = guest.get("ages", [])
    return Profile(
        child_ages=ages,
        lat=coord[0] if coord else None,
        lng=coord[1] if coord else None,
        radius_km=_veilig_int(guest.get("radius") or get_int("default_radius", 25), 25),
        budget_pref=guest.get("budget", "all"),
    ), None


def log(type_, event_id=None, **meta):
    fam = current_family()
    db.session.add(Interaction(family_id=fam.id if fam else None,
                               event_id=event_id, type=type_, meta=meta))
    db.session.commit()


# -------------------------------------------------------------- tijdsvensters --

DAGEN = ["ma", "di", "wo", "do", "vr", "za", "zo"]
MAANDEN = ["", "jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep",
           "okt", "nov", "dec"]


def event_datum(ev, now=None):
    """Leesbare, ondubbelzinnige datum voor een event.
    - Lopende meerdaagse events (begonnen, nog bezig) => 'loopt nog t/m ...'
      i.p.v. de (voorbije ogende) startdatum.
    - Jaartal tonen zodra het niet het huidige jaar is (anders lijkt 14/02
      volgend jaar op een voorbije datum)."""
    if not ev or not ev.start:
        return ""
    now = now or datetime.utcnow()
    einde = ev.end or ev.start
    meerdaags = ev.end and ev.end.date() != ev.start.date()
    # Al begonnen maar nog bezig:
    if ev.start <= now <= einde:
        if meerdaags:
            # Einddatum ver in de toekomst (>1 jaar) = in de praktijk een
            # placeholder ("open einde", bv. UiT-data met jaar 2100/5201).
            # Dan is een concrete datum zinloos; 'doorlopend' zegt dat het aanbod
            # blijft lopen, zonder een 24/7-belofte zoals 'altijd open' zou doen.
            if (einde - now).days > 365:
                return "doorlopend"
            return f"loopt nog t/m {einde.day} {MAANDEN[einde.month]} {einde.year}"
        return "vandaag bezig"
    d = ev.start
    stuk = f"{DAGEN[d.weekday()]} {d.day} {MAANDEN[d.month]}"
    if d.year != now.year:
        stuk += f" {d.year}"
    if d.hour or d.minute:
        stuk += f" om {d.strftime('%H:%M')}"
    return stuk


def window(scope):
    now = datetime.utcnow()
    if scope == "vandaag":
        end = now.replace(hour=23, minute=59, second=59)
        return now - timedelta(hours=12), end  # nog-bezige events tellen mee
    if scope == "deze-week":
        # Van nu tot en met zondag (einde van de lopende week).
        days_to_sun = (6 - now.weekday()) % 7
        end = (now + timedelta(days=days_to_sun)).replace(hour=23, minute=59, second=59)
        return now - timedelta(hours=12), end
    if scope == "weekend":
        days_to_sat = (5 - now.weekday()) % 7
        sat = (now + timedelta(days=days_to_sat)).replace(hour=0, minute=0)
        if now.weekday() >= 5:  # het is al weekend
            sat = now.replace(hour=0, minute=0)
        return sat, sat + timedelta(days=(7 - sat.weekday()) % 7 or 2)
    return now, now + timedelta(days=30)


def s_helper(event, profile, agg):
    """Scoreberekening die niet crasht als er geen profiel is."""
    try:
        return score_event(event, profile, ravot_avg=agg["avg"] if agg else None)
    except Exception:
        return 0


def _gast_rows(scope, limit=60):
    """Events voor bezoekers ZONDER profiel: het tijdvenster van de scope,
    gesorteerd op start. Geen personalisatie, wel meteen bruikbaar."""
    start, end = window(scope)
    q = geldige_events(Event.query).filter(
        Event.start <= end, (Event.end >= start) | (Event.start >= start))
    evs = q.order_by(Event.start.asc()).limit(limit).all()
    return [{"event": e, "agg": None, "family_total": None} for e in evs]


# Onder deze drempel vullen we de dag/weekend-feed aan met permanente plekken,
# zodat de app nooit leeg oogt (bv. zolang publiq nog uit staat).
MIN_FEED = 6


def permanente_pois(profile, limit=24):
    """Gescoorde permanente plekken (speeltuinen, musea, attracties) in de buurt.
    Fallback zodat Vandaag/Weekend niet leeg zijn als er weinig gedateerde events zijn."""
    candidates = bron_filter(Event.query).filter(Event.is_permanent.is_(True),
                                    Event.hidden.is_(False), Event.pending.is_(False)).limit(3000).all()
    weggeklikt = _weggeklikt_ids()
    rows = []
    for e in candidates:
        s = score_event(e, profile)
        if s > 0:
            if e.id in weggeklikt:   # 'niet voor ons' → hard achteraan
                s *= 0.02
            total, _ = family_price(e.price_info, profile.child_ages)
            rows.append({"event": e, "score": s, "agg": None,
                         "family_total": total, "euro": euro_indicator(total),
                         "regen": None, "permanent": True})
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows[:limit]


def vul_aan_met_permanente(rows, profile):
    """Vul een gedateerde feed aan met permanente POI's als hij (bijna) leeg is."""
    if len(rows) >= MIN_FEED:
        return rows
    extra = permanente_pois(profile, limit=24 - len(rows))
    bestaande = {r["event"].id for r in rows}
    return rows + [r for r in extra if r["event"].id not in bestaande]


def event_agg(e, cache=None):
    """Ravotscore-aggregaat voor één event: via de reeks als die er is, anders
    op de eigen reviews (belangrijk voor permanente plekken zonder reeks)."""
    cache = cache if cache is not None else {}
    sleutel = ("s", e.series_id) if e.series_id else ("e", e.id)
    agg = cache.get(sleutel)
    if agg is None:
        if e.series_id:
            revs = Review.query.join(Event, Review.event_id == Event.id) \
                .filter(Event.series_id == e.series_id).all()
        else:
            revs = Review.query.filter_by(event_id=e.id).all()
        agg = aggregate_ravotscore(revs)
        cache[sleutel] = agg or False
    return agg or None


def vul_agg_cache(events, cache):
    """Vul de agg-cache voor een hele kandidatenlijst in maximaal 2 queries
    i.p.v. 1 query per event (op /ontdek en Vandaag/Weekend scheelt dat
    honderden queries per paginaweergave). event_agg leest daarna enkel nog
    uit de cache; events zonder reviews krijgen expliciet False zodat er
    géén fallback-query meer volgt."""
    serie_ids = {e.series_id for e in events if e.series_id}
    los_ids = {e.id for e in events if not e.series_id}

    per_serie = {sid: [] for sid in serie_ids}
    if serie_ids:
        rijen = (db.session.query(Review, Event.series_id)
                 .join(Event, Review.event_id == Event.id)
                 .filter(Event.series_id.in_(serie_ids)).all())
        for rv, sid in rijen:
            per_serie[sid].append(rv)

    per_event = {eid: [] for eid in los_ids}
    if los_ids:
        for rv in Review.query.filter(Review.event_id.in_(los_ids)).all():
            per_event[rv.event_id].append(rv)

    for sid, revs in per_serie.items():
        cache.setdefault(("s", sid), aggregate_ravotscore(revs) or False)
    for eid, revs in per_event.items():
        cache.setdefault(("e", eid), aggregate_ravotscore(revs) or False)
    return cache


def _weggeklikt_ids():
    """Events die dit gezin als 'niet voor ons' markeerde: die zakken hard
    achteraan (score × 0.02) maar verdwijnen niet — expliciet zoeken vindt
    ze dus nog terug, en de knop op de kaart maakt het omkeerbaar.
    Buiten een request (weekendmail, crons) is er geen sessie: dan geen
    demping, want de mails bouwen hun eigen gezinsprofiel op."""
    from flask import has_request_context
    if not has_request_context():
        return set()
    fam = current_family()
    if not fam:
        return set()
    return {i.event_id for i in
            Interaction.query.filter_by(family_id=fam.id, type="dismiss")}


def scored_events(profile, scope, extra_filter=None, limit=40, weer=True):
    start, end = window(scope)
    now = datetime.utcnow()
    onder, boven = geldig_venster(now)
    # Harde grenzen: nooit afgelopen events, nooit absurd ver in de toekomst.
    # 'Afgelopen' = het EINDE ligt achter de ondergrens (lopende events tellen mee).
    q = bron_filter(Event.query).filter(
        Event.hidden.is_(False), Event.pending.is_(False),
        Event.start <= end,
        (Event.end >= start) | (Event.start >= start),
        (Event.end >= onder) | (Event.start >= onder),   # niet afgelopen
        Event.start <= boven,                            # niet verder dan het venster
    )
    if extra_filter is not None:
        q = extra_filter(q)
    candidates = q.limit(2000).all()
    # Weer één keer ophalen voor het profiel-zwaartepunt (niet per event)
    regen = None
    if weer and profile.lat is not None:
        from ..models import get_bool
        if get_bool("weer_aan"):
            from ..weer import regenkans
            regen = regenkans(profile.lat, profile.lng, start.date() if start else None)
    weggeklikt = _weggeklikt_ids()
    agg_cache = vul_agg_cache(candidates, {})
    rows = []
    for e in candidates:
        agg = event_agg(e, agg_cache)
        toon = score_zichtbaar(e)
        # 2c: bij commerciële plekken zónder Partner telt de score niet mee.
        s = score_event(e, profile,
                        ravot_avg=agg["avg"] if (agg and toon) else None)
        if s > 0:
            # weerbonus: bij regen binnen omhoog, buiten omlaag
            if regen is not None:
                from ..models import get_int
                r_hoog = get_int("regen_drempel", 50) or 50
                r_laag = get_int("zon_drempel", 20) or 20
                if regen >= r_hoog:
                    s *= 1.3 if e.indoor else 0.85
                elif regen <= r_laag and not e.indoor:
                    s *= 1.1
            s *= commercieel_factor(e)          # 3c: partner-bonus / demping
            if not e.image_url:                 # 6b: fiche zonder foto zakt wat
                s *= _factor("foto_malus", 0.92)
            if e.id in weggeklikt:              # 'niet voor ons' → hard achteraan
                s *= 0.02
            total, _ = family_price(e.price_info, profile.child_ages)
            rows.append({"event": e, "score": s, "agg": agg if toon else None,
                         "toon_score": toon,
                         "family_total": total, "euro": euro_indicator(total),
                         "regen": regen})
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows[:limit]


# --------------------------------------------------------------------- pages --

@bp.route("/e/<slug>.ics")
def event_ics(slug):
    """Agenda-export (fase 3): 'zet in agenda' voor één activiteit."""
    from ..models import Event
    ev = bron_filter(Event.query).filter_by(slug=slug).first_or_404()

    def esc(t):
        return (t or "").replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")

    def fmt(dt):
        return dt.strftime("%Y%m%dT%H%M%S") if dt else ""

    start = ev.start
    end = ev.end or (ev.start + timedelta(hours=2) if ev.start else None)
    loc = ", ".join(p for p in [ev.venue.name if ev.venue else None, ev.gemeente] if p)
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Ravot//NL", "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:ravot-{ev.id}@ravot.be",
        f"DTSTAMP:{fmt(datetime.utcnow())}Z",
        f"DTSTART:{fmt(start)}" if start else "",
        f"DTEND:{fmt(end)}" if end else "",
        f"SUMMARY:{esc(ev.title)}",
        f"LOCATION:{esc(loc)}",
        f"DESCRIPTION:{esc((ev.description or '')[:300])}\\n\\nVia Ravot.be",
        f"URL:{current_app.config['SITE_URL']}/e/{ev.slug}",
        "END:VEVENT", "END:VCALENDAR",
    ]
    ics = "\r\n".join(l for l in lines if l)
    return Response(ics, mimetype="text/calendar",
                    headers={"Content-Disposition": f"attachment; filename=ravot-{ev.slug}.ics"})



def _landing_stats():
    """Cijfers voor de landingstegels. Horeca/bars = 'plekken om te smullen',
    de rest van de vaste plekken = 'plekken om te ravotten', gedateerde events
    = 'activiteiten' (enkel getoond met echte UiT-productiedata)."""
    from ..models import Event, Review
    SMUL = ("horeca", "zomerbar", "winterbar")
    perm = Event.query.filter(Event.is_permanent.is_(True), Event.hidden.is_(False))
    smullen = perm.filter(Event.subtype.in_(SMUL)).count()
    ravotten = perm.filter(db.or_(Event.subtype.is_(None),
                                  Event.subtype.notin_(SMUL))).count()
    uit_url = current_app.config.get("UIT_SEARCH_URL") or ""
    return {
        "events": Event.query.filter(Event.is_permanent.is_(False),
                                     Event.hidden.is_(False)).count(),
        "ravotten": ravotten,
        "smullen": smullen,
        "gemeenten": db.session.query(Event.gemeente).filter(
            Event.gemeente.isnot(None)).distinct().count(),
        "reviews": Review.query.count(),
        "echte_events": "search.uitdatabank" in uit_url and "test" not in uit_url,
    }


@bp.route("/welkom")
def welkom():
    """Landingspagina, ook zichtbaar mét actieve zoekopdracht/profiel."""
    from ..models import Event, Review
    stats = _landing_stats()
    return render_template("public/landing.html", stats=stats,
                           family=current_family(), active=None,
                           title="Ravot — waar gaan we vandaag ravotten?")


@bp.route("/opnieuw")
def opnieuw():
    """Wis de anonieme zoekopdracht → terug naar de landingspagina."""
    session.pop("guest", None)
    return redirect(url_for("public.vandaag"))


@bp.route("/proberen", methods=["GET", "POST"])
def proberen():
    """Anonieme snelstart: postcode + leeftijden, zonder account."""
    if request.method == "POST":
        # Geboortejaren (net als een account): de leeftijd groeit zo elk jaar
        # vanzelf mee. We bewaren de jaren én de afgeleide leeftijden — de
        # leeftijden voeden de scoring, de jaren tonen we terug in het formulier.
        huidig = datetime.utcnow().year
        jaren = []
        for j in request.form.getlist("birth_year"):
            if j.strip().isdigit():
                jr = int(j)
                if huidig - 17 <= jr <= huidig:
                    jaren.append(jr)
        ages = [huidig - jr for jr in jaren]
        try:
            radius = int(re.sub(r"\D", "", request.form.get("radius", "") or "") or 25)
        except ValueError:
            radius = 25
        session["guest"] = {
            "postcode": re.sub(r"\D", "", request.form.get("postcode", ""))[:4],
            "birth_years": jaren[:6],
            "ages": ages[:6],
            "radius": max(1, min(radius, 200)),
            "budget": request.form.get("budget", "all"),
        }
        session.permanent = True
        return redirect(url_for("public.vandaag"))
    return render_template("public/proberen.html", family=None, active=None,
                           current_year=datetime.utcnow().year,
                           title="Meteen kijken wat er te doen is")


@bp.before_app_request
def _vang_deelcode():
    """?ref=CODE op eender welke pagina onthouden tot aan de registratie."""
    code = (request.args.get("ref") or "").strip().upper()[:12]
    if code and code.isalnum() and not session.get("family_id"):
        session["ref_code"] = code
        session.permanent = True


@bp.route("/", methods=["GET"])
def home():
    """Home. Uitgelogde bezoekers zien de landingspagina; ingelogde gezinnen
    gaan direct naar hun app (Vandaag)."""
    fam = current_family()
    if fam:
        return redirect(url_for("public.vandaag"))
    from ..models import Event, Review
    stats = _landing_stats()
    return render_template("public/landing.html", stats=stats,
                           family=None, active=None,
                           title="Ravot — waar gaan we vandaag ravotten?")


@bp.route("/vandaag", methods=["GET"])
def vandaag():
    profile, fam = build_profile()
    has_profile = bool(fam or guest_profile().get("postcode"))
    if has_profile:
        rows = scored_events(profile, "vandaag")
    else:
        # Bezoeker zonder profiel: toon gewoon wat er vandaag te doen is.
        # (De landingspagina staat op "/" — de Vandaag-tab hoort de lijst te tonen.)
        rows = _gast_rows("vandaag")
    # Weinig gedateerde events (bv. UiT-laag uit)? Vul aan met speeltuinen,
    # musea en horeca in de buurt — vandaag kún je daar ook gewoon heen.
    rows = vul_aan_met_permanente(rows, profile)
    if not rows:
        log("zero_result", scope="vandaag", postcode=guest_profile().get("postcode")
            or (fam.postcode if fam else None))
    gemeente = (fam.postcode if fam else guest_profile().get("postcode")) or "jouw buurt"
    centroid = db.session.get(PostcodeCentroid, gemeente) if gemeente else None
    plaats = centroid.gemeente if centroid else "jouw buurt"
    answer = seo.answer_block(plaats, "vandaag", [r["event"] for r in rows],
                              top=(rows[0]["event"], rows[0]["agg"]) if rows else None)
    return render_template("public/lijst.html", rows=rows, scope="vandaag",
                           title="Wat gaan we vandaag doen?", answer=answer,
                           regen=rows[0].get("regen") if rows else None,
                           weer=weerbericht("vandaag", fam),
                           has_profile=has_profile, gast_actief=gast_actief(), family=fam, active="vandaag")


@bp.route("/deze-week")
def deze_week():
    profile, fam = build_profile()
    has_profile = bool(fam or guest_profile().get("postcode"))
    rows = scored_events(profile, "deze-week") if has_profile else _gast_rows("deze-week")
    rows = vul_aan_met_permanente(rows, profile)
    return render_template("public/lijst.html", rows=rows, scope="deze week",
                           title="Deze week", answer=None,
                           regen=rows[0].get("regen") if rows else None,
                           weer=weerbericht("deze-week", fam),
                           has_profile=has_profile, gast_actief=gast_actief(), family=fam, active="deze-week")


@bp.route("/weekend")
def weekend():
    profile, fam = build_profile()
    has_profile = bool(fam or guest_profile().get("postcode"))
    rows = scored_events(profile, "weekend") if has_profile else _gast_rows("weekend")
    rows = vul_aan_met_permanente(rows, profile)
    return render_template("public/lijst.html", rows=rows, scope="dit weekend",
                           title="Dit weekend", answer=None,
                           regen=rows[0].get("regen") if rows else None,
                           weer=weerbericht("weekend", fam),
                           has_profile=has_profile, gast_actief=gast_actief(), family=fam, active="weekend")


@bp.route("/ontdek")
@limiter.limit("40/minute;600/hour")   # anti-scrape: ruim voor mensen, traag voor bots
def ontdek():
    """Alle gezinsactiviteiten, zichtbaar ZONDER postcode of profiel.
    Met zoeken, filters en paginering (want het zijn er veel)."""
    from ..models import get_int
    profile, fam = build_profile()
    has_profile = bool(fam or guest_profile().get("postcode"))
    sort = request.args.get("sort", "datum")       # datum (standaard) | score
    zoek = (request.args.get("q") or "").strip().lower()
    # Autocomplete levert "torhout (8820)"; strip het (postcode)-deel zodat het
    # tekst-/gemeentefilter op de kale naam werkt (het middelpunt komt los via
    # zoek_centrum, dat de postcode wél begrijpt).
    zoek = re.sub(r"\s*\(\d{4,5}\)\s*$", "", zoek).strip()
    filter_type = request.args.get("filter", "")   # ''|gratis|binnen|buiten
    wanneer = request.args.get("wanneer", "deze-week")   # standaard: deze week
    # 'alle' (of onbekende waarde) = geen datumbegrenzing; de drie vensters
    # (vandaag/deze-week/weekend) passen wél een filter toe (zie hieronder).
    cat = request.args.get("cat", "")              # categorie-filter
    verberg_sp = request.args.get("sp") == "0"     # gewone speeltuinen verbergen
    try:
        pagina = max(1, int(request.args.get("p", 1)))
    except ValueError:
        pagina = 1
    per_pagina = get_int("ontdek_per_pagina", 24) or 24
    now = datetime.utcnow()

    toon_alles = request.args.get("alles_tonen") == "1"
    # Lijst = kaart: ook vaste plekken (speeltuinen, musea, horeca, ...) horen
    # in Ontdek thuis. Gedateerde events komen door de sortering eerst; de
    # kwaliteits- en curatiefilters houden de POI-vloed beheersbaar.
    q = curatie_filter(type_filter(kwaliteit_filter(geldige_events(Event.query, now))), toon_alles)
    if wanneer in ("vandaag", "deze-week", "weekend"):
        w_start, w_end = window(wanneer)
        # Vaste plekken zijn "altijd te bezoeken" en blijven dus ook binnen
        # een datumvenster zichtbaar.
        q = q.filter(db.or_(
            Event.is_permanent.is_(True),
            db.and_(Event.start <= w_end,
                    (Event.end >= w_start) | (Event.start >= w_start))))
    centrum = _zoek_centrum(zoek, strict=True) if zoek else None
    if zoek and not centrum:
        # Geen exacte plaats → zoek op tekst (titel/gemeente)
        like = f"%{zoek}%"
        q = q.filter(db.or_(db.func.lower(Event.title).like(like),
                            db.func.lower(Event.gemeente).like(like)))
    elif centrum:
        # Herkende plaats: eerst kijken of de gemeente zélf genoeg oplevert.
        # Zo krijg je bij "Torhout" Torhout te zien en niet vooral Brugge en
        # Roeselare, die toevallig binnen 20 km liggen.
        like = f"%{zoek}%"
        in_gemeente = q.filter(db.func.lower(Event.gemeente).like(like))
        if in_gemeente.count() >= 5:
            q = in_gemeente
        else:
            # Te weinig in de gemeente zelf → toon de buurt (rechthoek ~20 km),
            # al in de databank i.p.v. pas na de cap.
            from math import cos, radians
            d_lat = 20.0 / 111.0
            d_lng = 20.0 / max(1.0, 111.0 * cos(radians(centrum[0])))
            q = q.filter(
                Event.lat.between(centrum[0] - d_lat, centrum[0] + d_lat),
                Event.lng.between(centrum[1] - d_lng, centrum[1] + d_lng))
    if filter_type == "gratis":
        q = q.filter(Event.is_free.is_(True))
    elif filter_type == "binnen":
        q = q.filter(Event.indoor.is_(True))
    elif filter_type == "buiten":
        q = q.filter(Event.indoor.is_(False))
    # Ouder-filters: enkel positief filteren (True); onbekend blijft onbekend.
    ouder_filters = {f for f in request.args.getlist("ouder")
                     if f in ("omheind", "verzorgingstafel", "buggy_ok",
                              "kinderstoel", "speelhoek", "kindermenu",
                              "terras", "overdekt_terras", "parking",
                              "toegankelijk", "allergievriendelijk",
                              "babyvoeding", "huisdieren",
                              "toilet", "drinkwater", "picknick", "veggie")}
    for veld in ouder_filters:
        q = q.filter(getattr(Event, veld).is_(True))
    # Soort plek (speeltuin, museum, horeca, ...): filter op subtype.
    from ..types import TYPES, in_seizoen, GROEP_SMULLEN, GROEP_BELEVEN
    # Hoofdgroep-filter (Beleven / Ravotten / Smullen) uit de filterbalk.
    groep = request.args.get("groep") or ""
    if groep == "smullen":
        q = q.filter(Event.subtype.in_(list(GROEP_SMULLEN)))
    elif groep == "beleven":
        q = q.filter(Event.subtype.in_(list(GROEP_BELEVEN)))
    elif groep == "ravotten":
        # Ravotten = de rest (niet smullen, niet beleven).
        niet = list(GROEP_SMULLEN | GROEP_BELEVEN)
        q = q.filter(db.or_(Event.subtype.is_(None), Event.subtype.notin_(niet)))
    else:
        groep = ""
    soort = request.args.get("soort") or ""
    if soort in TYPES:
        q = q.filter(Event.subtype == soort)
    # Seizoensgebonden types (zomer-/winterbar) buiten hun seizoen weglaten —
    # tenzij er expliciet op gefilterd wordt (dan wil je ze bewust zien).
    if soort not in ("zomerbar", "winterbar"):
        q = q.filter(db.or_(Event.subtype.is_(None),
                            ~Event.subtype.in_([t for t in ("zomerbar", "winterbar")
                                                if not in_seizoen(t)])))
    # Leeftijd: toon wat (ook) geschikt is voor die leeftijdsband.
    lft = request.args.get("lft") or ""
    band = next((b for b in LEEFTIJDEN if b[0] == lft), None)
    if band:
        q = q.filter(Event.age_min <= band[3], Event.age_max >= band[2])
    else:
        lft = ""
    if cat:
        # categories is JSON; matchen doen we tekstueel op de opgeslagen lijst
        q = q.filter(db.func.lower(db.cast(Event.categories, db.String)).like(f'%"{cat}"%'))
    if verberg_sp:
        q = q.filter(db.or_(Event.subtype.is_(None), Event.subtype != "playground"))
    # Twee aparte emmers i.p.v. één cap van 1000 met gedateerde events eerst:
    # anders duwen duizend activiteiten alle vaste plekken uit de selectie.
    gedateerd_cand = (q.filter(Event.start.isnot(None))
                       .order_by(Event.start.asc()).limit(700).all())
    vast_cand = (q.filter(Event.start.is_(None))
                  .order_by(Event.quality.desc().nullslast()).limit(700).all())
    candidates = gedateerd_cand + vast_cand
    # Partners APART ophalen — zonder de 1000-cap ÉN zonder de kwaliteits-/
    # curatiedrempel. Een betaalde partner hoort altijd zichtbaar te zijn, ook
    # als zijn fiche (nog) een lage quality heeft. We passen wél de datum-/
    # type-/zoekfilters toe die de gebruiker koos, zodat een partner niet
    # opduikt bij een niet-passende filter.
    partner_q = geldige_events(Event.query, now)
    from ..mollie import ZICHTBAAR_PLANNEN
    partner_q = partner_q.filter(Event.partner_until.isnot(None),
                                 Event.partner_until > now,
                                 db.or_(Event.partner_plan.is_(None),
                                        Event.partner_plan.in_(ZICHTBAAR_PLANNEN)))
    if wanneer in ("vandaag", "deze-week", "weekend"):
        _ws, _we = window(wanneer)
        partner_q = partner_q.filter(db.or_(
            Event.is_permanent.is_(True),
            db.and_(Event.start <= _we, (Event.end >= _ws) | (Event.start >= _ws))))
    if soort in TYPES:
        partner_q = partner_q.filter(Event.subtype == soort)
    if groep == "smullen":
        partner_q = partner_q.filter(Event.subtype.in_(list(GROEP_SMULLEN)))
    elif groep == "beleven":
        partner_q = partner_q.filter(Event.subtype.in_(list(GROEP_BELEVEN)))
    elif groep == "ravotten":
        partner_q = partner_q.filter(db.or_(
            Event.subtype.is_(None),
            Event.subtype.notin_(list(GROEP_SMULLEN | GROEP_BELEVEN))))
    partner_kandidaten = partner_q.all()
    # samenvoegen zonder dubbels (partners kunnen ook al in candidates zitten)
    _in_cand = {e.id for e in candidates}
    for pe in partner_kandidaten:
        if pe.id not in _in_cand:
            candidates.append(pe)
            _in_cand.add(pe.id)
    # Bekende plaats gezocht? Filter op afstand (buurgemeenten mee).
    if centrum:
        candidates = _filter_buurt([{"event": e} for e in candidates], centrum, 20)
        candidates = [r["event"] for r in candidates]

    # Ravotscore ophalen (voor tonen + sorteren) — commercieel zonder Partner
    # toont geen badge en telt niet mee (afspraak 2c/3c).
    rows, agg_cache = [], vul_agg_cache(candidates, {})
    for e in candidates:
        agg = event_agg(e, agg_cache)
        toon = score_zichtbaar(e)
        rows.append({"event": e, "agg": agg if toon else None,
                     "toon_score": toon,
                     "score": s_helper(e, profile, agg if toon else None),
                     "family_total": None})

    if sort == "score":
        rows.sort(key=lambda r: ((r["agg"] or {}).get("avg") or 0,
                                 r["event"].quality or 0,          # completere fiches eerst
                                 r["event"].start or now),
                  reverse=True)
    else:  # datum (Eerst gepland) — bij gelijke datum wint de completere fiche
        rows.sort(key=lambda r: ((r["event"].start or now),
                                 -(r["event"].quality or 0)))

    totaal = len(rows)
    # Partners worden ALTIJD bovenaan uitgelicht (los van sortering/paginering).
    # Zonder dit belandt een permanente partner-zaak (horeca heeft geen datum en
    # zakt bij datumsortering naar achteren) op een late pagina en lijkt hij
    # "verdwenen". We trekken ze uit de volledige lijst en tonen ze enkel op
    # pagina 1; in de gewone (gepagineerde) stroom laten we ze weg.
    partner_rows = [r for r in rows
                    if partner_zichtbaar(r["event"], now)]
    partner_ids = {r["event"].id for r in partner_rows}
    gewone_rows = [r for r in rows if r["event"].id not in partner_ids]

    max_pagina = max(1, (len(gewone_rows) + per_pagina - 1) // per_pagina)
    pagina = min(pagina, max_pagina)
    begin = (pagina - 1) * per_pagina
    pagina_rows = gewone_rows[begin:begin + per_pagina]
    # Uitgelichte partners enkel op de eerste pagina meesturen.
    uitgelichte_partners = partner_rows if pagina == 1 else []

    from ..models import get_bool as _gb
    # Weerbericht: op de gezóchte plaats als die bekend is, anders woonplaats.
    weer_scope = wanneer if wanneer in ("vandaag", "deze-week", "weekend") else "vandaag"
    weer = weerbericht(weer_scope, fam, centrum=centrum,
                       plaats=zoek.title() if centrum else None)

    def _ontdek_url(_endpoint="public.ontdek", **wijzig):
        """Bouw een filter-URL: huidige selectie + één wijziging. Houdt alle
        andere filters vast (dat ging voorheen soms verloren) en reset de
        paginering bij elke filterwissel. Met _endpoint wisselt dezelfde
        selectie naadloos tussen lijst (ontdek) en kaart (verkennen)."""
        params = {"wanneer": wanneer, "sort": sort, "filter": filter_type,
                  "cat": cat, "q": zoek, "soort": soort, "groep": groep, "lft": lft,
                  "ouder": sorted(ouder_filters)}
        params.update(wijzig)
        params = {k: v for k, v in params.items() if v}
        if params.get("sort") == "datum":
            params.pop("sort")           # default niet in de URL
        if params.get("wanneer") == "deze-week":
            params.pop("wanneer")
        return url_for(_endpoint, **params)

    aantal_actief = ((1 if filter_type else 0) + (1 if cat else 0)
                     + (1 if soort else 0) + len(ouder_filters)
                     + (1 if lft else 0) + (1 if sort == "score" else 0))
    return render_template("public/ontdek.html", lft=lft, leeftijden=LEEFTIJDEN, rows=pagina_rows, uitgelichte_partners=uitgelichte_partners, sort=sort, zoek=zoek, wanneer=wanneer, cat=cat, verberg_sp=verberg_sp, toon_alles=toon_alles, curatie_aan=_gb("enkel_gecureerd"), ouder_filters=ouder_filters, weer=weer, soort=soort, groep=groep, soorten=TYPES, flink=_ontdek_url, aantal_actief=aantal_actief, gast_actief=gast_actief(),
                           wissel_lijst=_ontdek_url(), wissel_kaart=_ontdek_url("public.verkennen"),
                           wis_url=url_for("public.ontdek", wanneer=wanneer, q=zoek),
                           zoek_endpoint="public.ontdek", weergave="lijst", toon_sorteer=True, kaart=False,
                           filter_type=filter_type, pagina=pagina, max_pagina=max_pagina,
                           totaal=totaal, has_profile=has_profile, family=fam,
                           active="ontdek", title="Ontdek alles")


@bp.route("/verkennen")
@limiter.limit("20/minute;300/hour")   # kaartdata is het duurst om te oogsten
def verkennen():
    profile, fam = build_profile()
    zoek = (request.args.get("q") or "").strip().lower()
    zoek = re.sub(r"\s*\(\d{4,5}\)\s*$", "", zoek).strip()   # "torhout (8820)" -> "torhout"
    filter_type = request.args.get("filter", "")
    now = datetime.utcnow()

    # Waar centreren we de kaart? Gezochte plaats > profiel > België.
    centrum = _zoek_centrum(zoek) if zoek else None
    if centrum:
        center = [centrum[0], centrum[1]]
        zoom = 14
    elif profile.lat:
        center = [profile.lat, profile.lng]
        zoom = 11
    else:
        center = [50.85, 4.35]
        zoom = 9

    # Gebalanceerd: gedateerde events én permanente POI's krijgen elk een
    # eigen deel van de kaart (anders verdringen 1000en speeltuinen de agenda).
    wanneer = request.args.get("wanneer", "deze-week")   # standaard: deze week
    # Gezocht op een plaats? Bereken één keer de rechthoek van ~30 km en pas
    # die toe vóór élk contingent (zie ook perm_basis verderop).
    _box = ()
    if centrum:
        from math import cos, radians
        _d_lat = 30.0 / 111.0
        _d_lng = 30.0 / max(1.0, 111.0 * cos(radians(centrum[0])))
        _box = (Event.lat.between(centrum[0] - _d_lat, centrum[0] + _d_lat),
                Event.lng.between(centrum[1] - _d_lng, centrum[1] + _d_lng))
    gedateerd_q = geldige_events(Event.query, now).filter(
        Event.lat.isnot(None), Event.is_permanent.is_(False))
    if _box:
        gedateerd_q = gedateerd_q.filter(*_box)
    if wanneer in ("vandaag", "deze-week", "weekend"):
        w_start, w_end = window(wanneer)
        gedateerd_q = gedateerd_q.filter(
            Event.start <= w_end, (Event.end >= w_start) | (Event.start >= w_start))
    gedateerd = gedateerd_q.order_by(Event.start).limit(500).all()
    # Permanente plekken: beste fiches eerst (niet alfabetisch — dan vielen
    # nieuwe types zoals horeca buiten de limiet). Horeca krijgt een eigen
    # gegarandeerd deel, zodat kindvriendelijke restaurants altijd op de
    # kaart staan, hoeveel speeltuinen er ook zijn.
    perm_basis = bron_filter(Event.query).filter(
        Event.lat.isnot(None), Event.is_permanent.is_(True),
        Event.hidden.is_(False), Event.pending.is_(False))
    # Expliciete soort-keuze? Dan het contingent daarop vernauwen — anders kan
    # een zeldzaam type (zomerbar, rommelmarkt) verdrongen worden door de 500
    # best-scorende speeltuinen en lijkt de filter "kapot".
    from ..types import TYPES as _TYPES
    _soort_vooraf = request.args.get("soort") or ""
    if _soort_vooraf in _TYPES:
        perm_basis = perm_basis.filter(Event.subtype == _soort_vooraf)
    # Gezocht op een plaats? Knijp de buurt AL in de databank (rechthoek van
    # ~30 km) vóór de contingenten toeslaan. Anders vullen de 300 best
    # scorende eetplekken van héél Vlaanderen het contingent en blijft er van
    # de gezochte gemeente bijna niets over.
    if _box:
        perm_basis = perm_basis.filter(*_box)
    horeca = perm_basis.filter(Event.subtype == "horeca") \
        .order_by(Event.quality.desc().nullslast()).limit(300).all()
    # Gezinsplekken: eigen contingent — door mensen aangebracht en door de
    # beheerder goedgekeurd, dus die horen áltijd op de kaart.
    eigen = perm_basis.filter(Event.source == "user") \
        .order_by(Event.quality.desc().nullslast()).limit(200).all()
    permanent = perm_basis.filter(db.or_(Event.subtype != "horeca",
                                         Event.subtype.is_(None))) \
        .order_by(Event.quality.desc().nullslast(), Event.title).limit(500).all()
    evs = list({e.id: e for e in gedateerd + permanent + horeca + eigen}.values())

    # Partners horen ALTIJD op de kaart, los van de contingent-limieten én de
    # kwaliteitsdrempel hierboven (anders kan een partner buiten de top-300 of
    # onder een drempel vallen en ontbreken — bron van 'soms wel, soms niet').
    partner_evs = geldige_events(Event.query, now).filter(
        Event.lat.isnot(None),
        Event.partner_until.isnot(None), Event.partner_until > now).all()
    _bekend = {e.id for e in evs}
    for pe in partner_evs:
        if pe.id not in _bekend:
            evs.append(pe)
            _bekend.add(pe.id)

    # Filter op type, categorie, speeltuinen en (indien gezocht) op buurt —
    # zelfde filterset als Ontdek: lijst en kaart zijn twee weergaven van
    # dezelfde vraag, dus je kan overal evenveel.
    cat = request.args.get("cat", "")
    verberg_sp = request.args.get("sp") == "0"   # gewone speeltuinen weg
    from ..types import verborgen_type_codes, type_code, TYPES, in_seizoen
    soort = request.args.get("soort") or ""
    if soort not in TYPES:
        soort = ""
    ouder_filters = {f for f in request.args.getlist("ouder")
                     if f in ("omheind", "verzorgingstafel", "buggy_ok",
                              "kinderstoel", "speelhoek", "kindermenu",
                              "terras", "overdekt_terras", "parking",
                              "toegankelijk", "allergievriendelijk",
                              "babyvoeding", "huisdieren",
                              "toilet", "drinkwater", "picknick", "veggie")}
    lft = request.args.get("lft") or ""
    band = next((b for b in LEEFTIJDEN if b[0] == lft), None)
    if not band:
        lft = ""
    from ..models import get_bool
    from ..types import GROEP_SMULLEN, GROEP_BELEVEN
    _verborgen = verborgen_type_codes()
    _enkel_gecureerd = get_bool("enkel_gecureerd") and request.args.get("alles_tonen") != "1"
    groep = request.args.get("groep") or ""
    if groep not in ("beleven", "ravotten", "smullen"):
        groep = ""
    def _past(e):
        if _enkel_gecureerd and not e.curated:
            return False
        if groep:
            code = type_code(e)
            if groep == "smullen" and code not in GROEP_SMULLEN:
                return False
            if groep == "beleven" and code not in GROEP_BELEVEN:
                return False
            if groep == "ravotten" and (code in GROEP_SMULLEN or code in GROEP_BELEVEN):
                return False
        if filter_type == "gratis" and not e.is_free:
            return False
        if filter_type == "binnen" and not e.indoor:
            return False
        if filter_type == "buiten" and e.indoor:
            return False
        if cat and cat not in (e.categories or []):
            return False
        if soort and type_code(e) != soort:
            return False
        for veld in ouder_filters:
            if getattr(e, veld, None) is not True:
                return False
        if band and not (e.age_min is not None and e.age_min <= band[3]
                         and e.age_max is not None and e.age_max >= band[2]):
            return False
        if e.subtype in ("zomerbar", "winterbar") and soort != e.subtype \
                and not in_seizoen(e.subtype):
            return False
        if verberg_sp and e.subtype == "playground":
            return False
        if _verborgen and type_code(e) in _verborgen:
            return False
        return True
    evs = [e for e in evs if _past(e)]
    if centrum:
        from ..scoring import haversine_km
        evs = [e for e in evs if haversine_km(centrum[0], centrum[1], e.lat, e.lng) <= 30]

    markers = [_kaart_marker(e) for e in evs]

    def _kaart_url(_endpoint="public.verkennen", **wijzig):
        params = {"wanneer": wanneer, "filter": filter_type, "cat": cat,
                  "q": zoek, "soort": soort, "groep": groep, "lft": lft,
                  "ouder": sorted(ouder_filters),
                  "sp": "0" if verberg_sp else None}
        params.update(wijzig)
        params = {k: v for k, v in params.items() if v}
        if params.get("wanneer") == "deze-week":
            params.pop("wanneer")
        return url_for(_endpoint, **params)

    aantal_actief = ((1 if filter_type else 0) + (1 if cat else 0)
                     + (1 if soort else 0) + len(ouder_filters)
                     + (1 if lft else 0) + (1 if verberg_sp else 0)
                     + (1 if groep else 0))
    return render_template("public/verkennen.html", lft=lft, leeftijden=LEEFTIJDEN, markers=markers, center=center,
                           zoom=zoom, zoek=zoek, gezocht=bool(centrum),
                           filter_type=filter_type, cat=cat, verberg_sp=verberg_sp,
                           wanneer=wanneer, aantal=len(markers), totaal=len(markers),
                           soort=soort, groep=groep, soorten=TYPES, ouder_filters=ouder_filters,
                           flink=_kaart_url, aantal_actief=aantal_actief,
                           wissel_lijst=_kaart_url("public.ontdek"),
                           wissel_kaart=_kaart_url(),
                           wis_url=url_for("public.verkennen", wanneer=wanneer, q=zoek),
                           zoek_endpoint="public.verkennen", weergave="kaart",
                           toon_sorteer=False, kaart=True, sort=None,
                           family=fam, active="verkennen", title="Verkennen")


def _kaart_marker(e):
    from ..types import activiteit_type
    return {
        "lat": e.lat, "lng": e.lng, "title": e.title,
        "url": url_for("public.event", slug=e.slug),
        "free": e.is_free, "gemeente": e.gemeente, "adres": e.adres,
        "datum": event_datum(e) if e.start else None,
        "leeftijd": f"{e.age_min}\u2013{e.age_max} jaar" if e.age_min is not None else None,
        "indoor": bool(e.indoor), "img": poi_image(e),
        "emoji": activiteit_type(e)["emoji"], "type": activiteit_type(e)["label"],
        "permanent": bool(e.is_permanent), "eet": e.subtype == "horeca",
        "partner": partner_zichtbaar(e),
        "score": None, "count": None,
    }


@bp.route("/api/kaart")
@limiter.limit("90/minute;900/hour")   # kaart laadt bij elke verplaatsing bij
def api_kaart():
    """Markers voor het zichtbare kaartgebied.

    De kaart vraagt zelf op wat er in beeld is, i.p.v. dat de server een vast
    contingent uit héél Vlaanderen kiest. Ver uitgezoomd sturen we bolletjes
    per gemeente met een aantal; ingezoomd echte pins.
    """
    from ..types import TYPES, GROEP_SMULLEN, GROEP_BELEVEN
    now = datetime.utcnow()
    try:
        zuid = float(request.args["z"]); noord = float(request.args["n"])
        west = float(request.args["w"]); oost = float(request.args["o"])
        zoom = int(float(request.args.get("zoom", 11)))
    except (KeyError, ValueError):
        return jsonify({"fout": "gebied ontbreekt"}), 400
    if noord < zuid or oost < west:
        return jsonify({"fout": "gebied klopt niet"}), 400

    q = type_filter(geldige_events(Event.query, now)).filter(
        Event.lat.isnot(None), Event.lng.isnot(None),
        Event.lat.between(zuid, noord), Event.lng.between(west, oost))

    wanneer = request.args.get("wanneer", "deze-week")
    if wanneer in ("vandaag", "deze-week", "weekend"):
        w_start, w_end = window(wanneer)
        q = q.filter(db.or_(
            Event.is_permanent.is_(True),
            db.and_(Event.start <= w_end,
                    (Event.end >= w_start) | (Event.start >= w_start))))
    groep = request.args.get("groep") or ""
    if groep == "smullen":
        q = q.filter(Event.subtype.in_(list(GROEP_SMULLEN)))
    elif groep == "beleven":
        q = q.filter(Event.subtype.in_(list(GROEP_BELEVEN)))
    elif groep == "ravotten":
        niet = list(GROEP_SMULLEN | GROEP_BELEVEN)
        q = q.filter(db.or_(Event.subtype.is_(None), Event.subtype.notin_(niet)))
    soort = request.args.get("soort") or ""
    if soort in TYPES:
        q = q.filter(Event.subtype == soort)
    ft = request.args.get("filter") or ""
    if ft == "gratis":
        q = q.filter(Event.is_free.is_(True))
    elif ft == "binnen":
        q = q.filter(Event.indoor.is_(True))
    elif ft == "buiten":
        q = q.filter(Event.indoor.is_(False))
    for veld in request.args.getlist("ouder"):
        if veld in ("omheind", "verzorgingstafel", "buggy_ok", "kinderstoel",
                    "speelhoek", "kindermenu", "terras", "overdekt_terras",
                    "parking", "toegankelijk", "allergievriendelijk",
                    "babyvoeding", "huisdieren", "toilet", "drinkwater",
                    "picknick", "veggie"):
            q = q.filter(getattr(Event, veld).is_(True))

    totaal = q.count()
    # Ver uitgezoomd: bolletjes per gemeente i.p.v. duizenden pins.
    if zoom < 10:
        rijen = (q.with_entities(Event.gemeente, db.func.count(Event.id),
                                 db.func.avg(Event.lat), db.func.avg(Event.lng))
                  .group_by(Event.gemeente)
                  .order_by(db.func.count(Event.id).desc()).limit(250).all())
        groepen = [{"gemeente": g or "?", "aantal": int(n),
                    "lat": float(la), "lng": float(lo)}
                   for g, n, la, lo in rijen if la is not None]
        return jsonify({"modus": "gemeenten", "totaal": totaal,
                        "groepen": groepen, "getoond": sum(g["aantal"] for g in groepen)})

    evs = q.order_by(Event.quality.desc().nullslast()).limit(600).all()
    return jsonify({"modus": "pins", "totaal": totaal, "getoond": len(evs),
                    "markers": [_kaart_marker(e) for e in evs]})


def _langs_routes(ev):
    """'Ligt langs route X' op de fiche — alleen als de routes-rubriek aan
    staat en de plek permanent is (patch 187, het routeweefsel)."""
    from ..models import get_bool
    if not get_bool("routes_in_menu") or not ev.is_permanent:
        return []
    try:
        from ..services.routes_gis import routes_bij_event
        return routes_bij_event(ev)
    except Exception:
        return []


@bp.route("/e/<slug>")
@limiter.limit("60/minute;1000/hour")  # fiches: 15k stuks leegtrekken duurt zo dagen per IP
def event(slug):
    ev = bron_filter(Event.query).filter_by(slug=slug).first_or_404()
    # Nog niet gemodereerde gebruikersbijdrage: niet publiek tonen.
    # (Enkel de indiener zelf mag meekijken; geen indiener bekend => niemand.)
    if ev.pending and (ev.submitted_by is None
                       or session.get("family_id") != ev.submitted_by):
        abort(404)
    if ev.hidden:
        # Verborgen fiche (dubbel of afgekeurd): nooit publiek tonen.
        # Een gekend dubbel stuurt 301 naar de canonieke fiche, zodat
        # bestaande links en SEO-waarde mee verhuizen.
        canon = db.session.get(Event, ev.dupe_of) if ev.dupe_of else None
        if canon and canon.slug and not canon.hidden and not canon.pending:
            return redirect(url_for("public.event", slug=canon.slug), code=301)
        abort(404)
    if ev.end and ev.end < datetime.utcnow() - timedelta(days=1) and ev.series:
        # SEO §2.3: afgelopen event → permanente reekspagina (301)
        return redirect(url_for("public.reeks", slug=ev.series.slug), code=301)
    profile, fam = build_profile()
    if ev.series_id:
        series_reviews = Review.query.join(Event, Review.event_id == Event.id) \
            .filter(Event.series_id == ev.series_id).all()
    else:
        series_reviews = Review.query.filter_by(event_id=ev.id).all()
    agg = aggregate_ravotscore(series_reviews)
    toon_score = score_zichtbaar(ev)
    total, _ = family_price(ev.price_info, profile.child_ages)
    friends_interested = []
    saved = shared = False
    if fam:
        saved = SavedEvent.query.filter_by(family_id=fam.id, event_id=ev.id).first() is not None
        shared = Share.query.filter_by(family_id=fam.id, event_id=ev.id).first() is not None
        friend_ids = [c.family_b for c in Connection.query.filter_by(family_a=fam.id)] + \
                     [c.family_a for c in Connection.query.filter_by(family_b=fam.id)]
        if friend_ids:
            rows = db.session.query(Family.display_name).join(
                Share, Share.family_id == Family.id
            ).filter(Share.event_id == ev.id, Family.id.in_(friend_ids)).all()
            friends_interested = [r[0] or "Een bevriend gezin" for r in rows]
    log("view", event_id=ev.id)
    title, desc = seo.meta_event(ev, total)
    from ..models import Photo
    goedgekeurde_fotos = Photo.query.filter_by(event_id=ev.id, status="approved").all()
    mijn_daguitstappen = []
    if fam:
        mijn_daguitstappen = DagUitstap.query.filter_by(family_id=fam.id) \
            .order_by(DagUitstap.updated_at.desc()).limit(10).all()
    # Zelf-curatie (fase 2): drie toestanden per zacht veld.
    #  - onbekend  → open vraag "Is er een toilet? ja/nee"
    #  - voorlopig → getoond, maar met "klopt dit? 👍/👎" om te versterken/weerleggen
    #  - bevestigd → staat vast, geen vraag meer
    from ..models import ZACHTE_VELDEN, VOORZIENING_LABELS, VOORZIENING_VRAAG, VeldStem
    from .. import stemmen as _stemmen
    _status = _stemmen.alle_velden(ev.id)
    # Enkel de velden die zinnig zijn voor dít type plek (geen "kindermenu?" bij
    # een speeltuin). De relevantie zit in stemmen.relevante_velden().
    _volgorde = _stemmen.relevante_velden(ev)
    onbekende_velden = []      # nog niemand → open vraag
    voorlopige_velden = []     # getoond, vraagt bevestiging
    for v in _volgorde:
        st = _status.get(v)
        vraag = VOORZIENING_VRAAG.get(v, VOORZIENING_LABELS.get(v, v) + "?")
        if st is None or st["toestand"] == "onbekend":
            onbekende_velden.append((v, vraag))
        elif st["toestand"] == "voorlopig":
            voorlopige_velden.append((v, vraag, st["waarde"], st["meerderheid_pct"]))
        # 'bevestigd' → geen vraag
    # compat met de template die één lijst verwacht
    ontbrekende_velden = onbekende_velden
    mijn_stemmen = {}
    if fam:
        for s in VeldStem.query.filter_by(event_id=ev.id, stemmer=str(fam.id)).all():
            mijn_stemmen[s.veld] = s.waarde
    return render_template(
        "public/event.html", ev=ev, agg=agg if toon_score else None,
        toon_score=toon_score, family_total=total,
        daguitstappen=mijn_daguitstappen,
        euro=euro_indicator(total), reviews=[r.public_dict() for r in series_reviews[:10]],
        friends=friends_interested, saved=saved, shared=shared, family=fam,
        langs_routes=_langs_routes(ev),
        fotos=goedgekeurde_fotos,
        ontbrekende_velden=ontbrekende_velden,
        voorlopige_velden=voorlopige_velden, mijn_stemmen=mijn_stemmen,
        meta_title=title, meta_desc=desc,
        jsonld=[seo.event_jsonld(ev, agg if toon_score else None, total),
                seo.breadcrumb_jsonld([("Ravot", "/"),
                                       (ev.gemeente or "Vlaanderen", f"/{(ev.gemeente or '').lower()}"),
                                       (ev.title, f"/e/{ev.slug}")])],
        active=None, title=ev.title,
    )


@bp.route("/ravotscore")
def score_uitleg():
    """Uitleg over de Ravotscore en de Ravotpas — publiek, want begrip is de
    basis van vertrouwen (en van meedoen)."""
    _, fam = build_profile()
    from ..models import get_int
    from ..punten import niveaus as _ladder
    return render_template("public/score_uitleg.html", family=fam,
                           geldig_maanden=get_int("punten_geldig_maanden", 6),
                           ladder=_ladder(),
                           pw={r: get_int(f"punt_{r}", d) for r, d in (
                               ("geweest", 5), ("review", 10), ("eerste_score", 15),
                               ("foto", 15), ("eerste_foto", 10), ("daguitstap", 5),
                               ("feestje", 10), ("plek", 15), ("veld_stem", 3),
                               ("uitnodiging", 25))},
                           dag_max=get_int("punten_dag_max", 60),
                           geweest_max=get_int("geweest_dag_max", 3),
                           veldstem_max=get_int("veldstem_dag_max", 8),
                           title="Zo werken de Ravotscore & Ravotpas", active=None)


@bp.route("/feestjes")
def feestjes_info():
    """Publieke uitlegpagina: het feestje als tweede toegangspoort tot Ravot,
    ook zonder login. De CTA stuurt na het inloggen rechtstreeks de wizard in."""
    from ..models import get_bool
    if not get_bool("feestjes_aan"):
        abort(404)
    _, fam = build_profile()
    return render_template("public/feestjes.html", family=fam,
                           title="Verjaardagsfeestje plannen", active=None)


@bp.route("/d/<token>")
@limiter.limit("60/minute")
def daguitstap_publiek(token):
    """Gedeelde daguitstap — leesbaar zonder account, enkel via de deellink.
    Geen gezinsgegevens zichtbaar: alleen de titel en de plekken."""
    d = DagUitstap.query.filter_by(share_token=token).first_or_404()
    items = [i for i in d.items if i.event and not i.event.hidden]
    markers = [_kaart_marker(i.event) for i in items
               if i.event.lat is not None and i.event.lng is not None]
    return render_template("public/daguitstap_publiek.html", d=d, items=items,
                           markers=markers, family=None,
                           title=d.titel, active=None)


@bp.route("/uitstap/<slug>")
def reeks(slug):
    from ..models import EditionSeries
    series = EditionSeries.query.filter_by(slug=slug).first_or_404()
    events = sorted(series.events, key=lambda e: e.start or datetime.min)
    upcoming = [e for e in events if e.start and e.start >= datetime.utcnow()]
    reviews = Review.query.join(Event, Review.event_id == Event.id) \
        .filter(Event.series_id == series.id).all()
    agg = aggregate_ravotscore(reviews)
    return render_template("public/reeks.html", series=series, upcoming=upcoming,
                           past=[e for e in events if e not in upcoming][-5:],
                           agg=agg, reviews=[r.public_dict() for r in reviews[:15]],
                           family=current_family(), active=None, title=series.name)


# -------------------------------------------- programmatic gemeentepagina's --

def _gemeente_events(gemeente, facet=None):
    scope = "vandaag" if facet == "vandaag" else "weekend" if facet in (None, "dit-weekend") else "maand"
    start, end = window(scope)
    onder, boven = geldig_venster()
    q = bron_filter(Event.query).filter(
        db.func.lower(Event.gemeente) == gemeente.lower(),
        Event.hidden.is_(False), Event.pending.is_(False),
        db.or_(
            Event.is_permanent.is_(True),
            db.and_(Event.start <= end,
                    (Event.end >= start) | (Event.start >= start),
                    (Event.end >= onder) | (Event.start >= onder),
                    Event.start <= boven),
        ))
    if facet == "gratis":
        q = q.filter(Event.is_free.is_(True))
    if facet == "binnen":
        q = q.filter(Event.indoor.is_(True))
    if facet in FACET_AGES:
        lo, hi = FACET_AGES[facet]
        q = q.filter(Event.age_min <= hi, Event.age_max >= lo)
    q = curatie_filter(type_filter(kwaliteit_filter(q)))
    # Gedateerde events op datum; permanente plekken daarna, beste fiches eerst.
    return q.order_by(Event.start.is_(None).asc(), Event.start,
                      Event.quality.desc().nullslast()).limit(100).all()


@bp.route("/<gemeente>")
@bp.route("/<gemeente>/<facet>")
def gemeente_page(gemeente, facet=None):
    if facet is not None and facet not in FACETS:
        abort(404)
    # bestaat de gemeente in onze data?
    exists = db.session.query(Event.id).filter(
        db.func.lower(Event.gemeente) == gemeente.lower()).first()
    if not exists:
        abort(404)
    events = _gemeente_events(gemeente, facet)
    naam = events[0].gemeente if events else gemeente.capitalize()
    scope = FACETS.get(facet, "dit weekend")
    noindex = len(events) < current_app.config["NOINDEX_MIN_EVENTS"]
    title, desc = seo.meta_gemeente(naam, len(events), scope)
    answer = seo.answer_block(naam, scope, events)
    buren = [r[0] for r in db.session.query(Event.gemeente).filter(
        Event.gemeente.isnot(None), db.func.lower(Event.gemeente) != gemeente.lower()
    ).group_by(Event.gemeente).limit(6).all()]
    faq = seo.faq_jsonld([(f"Wat is er {scope} te doen in {naam} met kinderen?", answer)])
    # Partnerblok: max. 2 betalende partners in deze gemeente, duidelijk gelabeld.
    # Bewust een APART blok — partners krijgen nooit een betere plek in de lijst.
    partners = bron_filter(Event.query).filter(
        db.func.lower(Event.gemeente) == gemeente.lower(),
        Event.partner_until.isnot(None), Event.partner_until > datetime.utcnow(),
        Event.hidden.is_(False), Event.pending.is_(False),
    ).order_by(Event.partner_until.desc()).limit(2).all()
    return render_template("public/gemeente.html", gemeente=naam, facet=facet,
                           facets=FACETS, events=events, answer=answer, buren=buren,
                           partners=partners,
                           noindex=noindex, meta_title=title, meta_desc=desc,
                           jsonld=[faq], family=current_family(), active=None,
                           title=title)


# ---------------------------------------------------------------- SEO-files --

@bp.route("/robots.txt")
def robots():
    # AI-crawlers expliciet welkom (GEO §5)
    lines = [
        "User-agent: *", "Allow: /", "",
        "User-agent: GPTBot", "Allow: /", "",
        "User-agent: ClaudeBot", "Allow: /", "",
        "User-agent: PerplexityBot", "Allow: /", "",
        "User-agent: Google-Extended", "Allow: /", "",
        f"Sitemap: {current_app.config['SITE_URL']}/sitemap.xml",
    ]
    return Response("\n".join(lines), mimetype="text/plain")


@bp.route("/llms.txt")
def llms():
    txt = render_template("public/llms.txt.j2", site=current_app.config["SITE_URL"])
    return Response(txt, mimetype="text/plain")


@bp.route("/sitemap.xml")
def sitemap():
    site = current_app.config["SITE_URL"]
    urls = [f"{site}/", f"{site}/weekend", f"{site}/verkennen"]
    # Enkel publiek zichtbare fiches: geen pending (detail geeft 404, dus
    # Google zou dode links crawlen) en geen hidden dubbels (duplicate content).
    from ..models import Artikel, FietsRoute
    urls.append(f"{site}/blog")
    urls.append(f"{site}/fietsroutes")
    for r in FietsRoute.query.filter_by(pending=False, hidden=False).all():
        urls.append(f"{site}/fietsroutes/{r.slug}")
    for a in Artikel.query.filter_by(gepubliceerd=True).all():
        urls.append(f"{site}/blog/{a.slug}")
    publiek = [Event.hidden.is_(False), Event.pending.is_(False)]
    from ..models import get_bool
    if not get_bool("uit_zichtbaar"):
        publiek.append(Event.source != "uit")
    publiek = tuple(publiek)
    gemeenten = db.session.query(Event.gemeente, db.func.count(Event.id)) \
        .filter(Event.gemeente.isnot(None), *publiek) \
        .group_by(Event.gemeente).all()
    for g_, n in gemeenten:
        if n >= current_app.config["NOINDEX_MIN_EVENTS"]:
            urls.append(f"{site}/{g_.lower()}")
            for facet in FACETS:
                urls.append(f"{site}/{g_.lower()}/{facet}")
    # Permanente plekken (start=NULL) zijn de evergreen-pagina's: altijd mee.
    for (slug,) in db.session.query(Event.slug).filter(
            *publiek, Event.slug.isnot(None),
            db.or_(Event.is_permanent.is_(True),
                   Event.start >= datetime.utcnow() - timedelta(days=1))).all():
        urls.append(f"{site}/e/{slug}")
    from ..models import EditionSeries
    for (slug,) in db.session.query(EditionSeries.slug).all():
        urls.append(f"{site}/uitstap/{slug}")
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    xml += [f"<url><loc>{u}</loc></url>" for u in urls]
    xml.append("</urlset>")
    return Response("".join(xml), mimetype="application/xml")


@bp.route("/health")
def health():
    """Healthcheck voor de Coolify-healthcheck en Uptime Kuma:
    app draait én de databank antwoordt. Geen gevoelige details."""
    try:
        db.session.execute(db.text("SELECT 1"))
    except Exception:
        return {"status": "fout", "db": "onbereikbaar"}, 503
    return {"status": "ok"}


@bp.route("/manifest.webmanifest")
def manifest():
    import json
    return Response(json.dumps({
        "name": "Ravot", "short_name": "Ravot",
        "description": "Waar gaan we vandaag ravotten?",
        "start_url": "/", "display": "standalone",
        "background_color": "#FAF7F0", "theme_color": "#2E7D46",
        "icons": [
            {"src": "/static/img/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/img/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }), mimetype="application/manifest+json")


def _content_of_template(slug, fallback_template, titel):
    """Toon de in de admin bewerkte pagina, of val terug op het vaste template."""
    from ..models import ContentPage
    cp = db.session.get(ContentPage, slug)
    if cp and cp.inhoud_md.strip():
        from ..content import render_markdown
        return render_template("public/content.html",
                               paginatitel=cp.titel, inhoud_html=render_markdown(cp.inhoud_md),
                               family=current_family(), active=None, title=cp.titel)
    # Geen db-inhoud: probeer het vaste template, anders een nette lege pagina.
    try:
        return render_template(fallback_template, family=current_family(),
                               active=None, title=titel)
    except Exception:
        return render_template("public/content.html", paginatitel=titel,
                               inhoud_html="<p>Deze pagina wordt binnenkort ingevuld.</p>",
                               family=current_family(), active=None, title=titel)


@bp.route("/over")
def over():
    return _content_of_template("over", "public/over.html", "Over Ravot")


@bp.route("/zo-help-je-mee")
def zo_help_je_mee():
    """Fase 5: eerlijke inkadering — één scherm dat uitlegt hoe je meebouwt."""
    return render_template("public/zo_help_je_mee.html",
                           family=current_family(), active=None,
                           title="Zo help je mee")


@bp.route("/hoe-werkt-het")
def hoe_werkt_het():
    # Samengevoegd met de uitgebreide handleiding voor gezinnen — één plek,
    # geen dubbele uitleg. De oude URL blijft werken via een redirect.
    return redirect(url_for("public.help_gezinnen"), code=301)


@bp.route("/privacy")
def privacy():
    return _content_of_template("privacy", "public/privacy.html", "Privacy- en cookieverklaring")


@bp.route("/voorwaarden")
def voorwaarden():
    return _content_of_template("voorwaarden", "public/voorwaarden.html", "Gebruiksvoorwaarden")


@bp.route("/cookies")
def cookies():
    return _content_of_template("cookies", "public/cookies.html", "Cookiebeleid")


@bp.route("/contact")
def contact():
    return _content_of_template("contact", "public/content.html", "Contact")


@bp.route("/foto/<int:pid>")
def foto(pid):
    """Serveer een gebruikersfoto. Goedgekeurd -> iedereen; anders enkel de
    admin of de uploader (pending foto's zijn dus niet publiek zichtbaar)."""
    from flask import send_file
    from .fotos_helpers import _mag_zien   # kleine helper hieronder
    from ..models import Photo
    from ..fotos import pad_van
    import os
    p = db.session.get(Photo, pid)
    if not p or not _mag_zien(p):
        abort(404)
    pad = pad_van(p.filename)
    if not os.path.exists(pad):
        abort(404)
    # Goedgekeurde foto's cachen browsers/CDN een week: de bestandsnaam is een
    # random token en verandert nooit, dus lang cachen is veilig én snel.
    leeftijd = 7 * 24 * 3600 if p.status == "approved" else 0
    return send_file(pad, mimetype="image/jpeg", max_age=leeftijd)


@bp.route("/api/plaatsen")
@limiter.limit("120/minute")
def api_plaatsen():
    """Autocomplete voor stad/postcode: canonieke suggesties uit de offline
    Belgische plaatsenlijst. Geen externe calls, dus snel en altijd consistent."""
    from ..plaatsen import PLAATSEN, PLAATS_LAND
    q = (request.args.get("q") or "").strip().lower()
    if len(q) < 2:
        return {"suggesties": []}
    vlag = {"BE": "🇧🇪", "NL": "🇳🇱", "FR": "🇫🇷"}
    def maak(zc, naam, lat, lng):
        land = PLAATS_LAND.get(zc, "BE")
        merk = "" if land == "BE" else f" {vlag.get(land, '')}"
        return {"label": f"{naam} ({zc}){merk}", "postcode": zc,
                "gemeente": naam, "lat": lat, "lng": lng, "land": land}
    uit = []
    if q.isdigit():                       # postcode-prefix
        for zc, naam, lat, lng in PLAATSEN:
            if zc.startswith(q):
                uit.append(maak(zc, naam, lat, lng))
                if len(uit) >= 8:
                    break
    else:                                 # naam-prefix (accentongevoelig)
        import unicodedata
        def plat(t):
            return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
        qp = plat(q)
        for zc, naam, lat, lng in PLAATSEN:
            if plat(naam).startswith(qp):
                uit.append(maak(zc, naam, lat, lng))
                if len(uit) >= 8:
                    break
    return {"suggesties": uit}


@bp.route("/help")
def help_gezinnen():
    return _content_of_template("help-gezinnen", "public/help_gezinnen.html",
                                "Handleiding voor gezinnen")


@bp.route("/help/partners")
def help_partners():
    return _content_of_template("help-partners", "public/help_partners.html",
                                "Handleiding voor partners")


@bp.route("/bronnen")
def bronnen():
    """Volledige bronvermelding en licenties — de plek waar gebruikers de
    data-attributie kunnen vinden (ODbL-vereiste), los van de kaart-hoek."""
    return _content_of_template("bronnen", "public/bronnen.html",
                                "Bronnen & data")


@bp.route("/kampen")
def kampen():
    """Apart onderdeel (los van de activiteiten): ouders zoeken kampen en
    filteren op datum, leeftijd, buurt, thema en praktische factoren. Niveau 1
    — Ravot is de vindplaats, inschrijven gebeurt bij de organisator via diens
    eigen link."""
    from datetime import date as _date, timedelta
    from ..models import get_bool, get_int, KAMP_THEMAS
    if not get_bool("kampen_aan"):
        abort(404)
    _, fam = build_profile()
    q = bron_filter(Event.query).filter(Event.is_kamp.is_(True), Event.hidden.is_(False),
                           Event.pending.is_(False))
    # Datumfilter met speling: een kamp dat een paar dagen buiten de gezochte
    # periode valt, is meestal nog relevant ("de week van..."). Standaardmarge
    # via de admin; de zoeker mag ze zelf aanpassen (?marge=).
    van = (request.args.get("van") or "").strip()
    tot = (request.args.get("tot") or "").strip()
    std_marge = get_int("kamp_marge_dagen", 3) or 3
    try:
        marge = max(0, min(30, int(request.args.get("marge"))))
    except (TypeError, ValueError):
        marge = std_marge
    def _pdate(s):
        try:
            return _date.fromisoformat(s)
        except (ValueError, TypeError):
            return None
    d_van, d_tot = _pdate(van), _pdate(tot)
    speling = timedelta(days=marge)
    if d_van:
        ondergrens = d_van - speling
        q = q.filter(db.or_(Event.kamp_eind >= ondergrens,
                            Event.kamp_start >= ondergrens))
    if d_tot:
        q = q.filter(Event.kamp_start <= d_tot + speling)
    # Leeftijd
    lft = request.args.get("lft") or ""
    band = next((b for b in LEEFTIJDEN if b[0] == lft), None)
    if band:
        q = q.filter(Event.age_min <= band[3], Event.age_max >= band[2])
    else:
        lft = ""
    # Buurt (postcode -> gemeente-tekstmatch, licht)
    plaats = (request.args.get("plaats") or "").strip()
    if plaats:
        like = f"%{plaats.lower()}%"
        q = q.filter(db.or_(db.func.lower(Event.gemeente).like(like),
                            Event.postcode.like(f"{plaats}%")))
    # Thema
    thema = request.args.get("thema") or ""
    if thema in KAMP_THEMAS:
        q = q.filter(Event.kamp_thema == thema)
    else:
        thema = ""
    # Praktische factoren (aanvinkbaar) — enkel filteren op wat aangevinkt is
    prakt = {
        "opvang": Event.kamp_opvang, "maaltijd": Event.kamp_maaltijd,
        "fiscaal": Event.kamp_fiscaal, "mutualiteit": Event.kamp_mutualiteit,
        "overnachting": Event.kamp_overnachting,
    }
    actief_prakt = []
    for naam, kolom in prakt.items():
        if request.args.get(naam):
            q = q.filter(kolom.is_(True))
            actief_prakt.append(naam)
    kampen = q.order_by(Event.kamp_start.asc().nullslast()).limit(200).all()
    # alleen toekomstige/lopende kampen
    vandaag = _date.today()
    kampen = [k for k in kampen if not k.kamp_eind or k.kamp_eind >= vandaag]
    return render_template("public/kampen.html", family=fam, kampen=kampen,
                           van=van, tot=tot, lft=lft, plaats=plaats,
                           thema=thema, themas=KAMP_THEMAS, marge=marge,
                           std_marge=std_marge, actief_prakt=actief_prakt,
                           leeftijden=LEEFTIJDEN, active="kampen")


# ---------------------------------------------------------------------------
# Blog ("Ravot vertelt") — patch 134
# ---------------------------------------------------------------------------

@bp.route("/blog")
def blog():
    from ..models import Artikel
    artikels = (Artikel.query.filter_by(gepubliceerd=True)
                .order_by(Artikel.publicatie_datum.desc()).limit(50).all())
    return render_template("public/blog.html", artikels=artikels,
                           meta_title="Blog — tips en inspiratie voor gezinsuitstappen | Ravot",
                           meta_desc="Praktische artikels over uitstappen met kinderen in "
                                     "Vlaanderen: van regenweer-tips tot de leukste speeltuinen.",
                           family=current_family(), active=None, title="Blog")


@bp.route("/blog/<slug>")
def blog_artikel(slug):
    from ..models import Artikel
    from ..content import render_markdown
    a = Artikel.query.filter_by(slug=slug, gepubliceerd=True).first_or_404()
    site = current_app.config["SITE_URL"]
    jsonld = json.dumps({
        "@context": "https://schema.org", "@type": "BlogPosting",
        "headline": a.titel,
        "description": a.samenvatting or None,
        "datePublished": a.publicatie_datum.isoformat() if a.publicatie_datum else None,
        "dateModified": a.updated_at.isoformat() if a.updated_at else None,
        "mainEntityOfPage": f"{site}/blog/{a.slug}",
        "author": {"@type": "Organization", "name": "Ravot"},
        "publisher": {"@type": "Organization", "name": "Ravot",
                      "url": site},
    }, ensure_ascii=False)
    # Recente andere artikels als leesverder-blok (interne links).
    meer = (Artikel.query.filter(Artikel.gepubliceerd.is_(True), Artikel.id != a.id)
            .order_by(Artikel.publicatie_datum.desc()).limit(3).all())
    return render_template("public/blog_artikel.html", a=a, meer=meer,
                           inhoud_html=render_markdown(a.inhoud_md),
                           meta_title=f"{a.titel} | Ravot",
                           meta_desc=(a.samenvatting or a.titel)[:160],
                           jsonld=[jsonld],
                           family=current_family(), active=None, title=a.titel)


# ---------------------------------------------------------------------------
# Gezinsfietsroutes (patch 160)
# ---------------------------------------------------------------------------

@bp.route("/fietsroutes")
def fietsroutes():
    from ..models import FietsRoute
    regio = (request.args.get("regio") or "").strip()
    provincie = (request.args.get("provincie") or "").strip()
    q = FietsRoute.query.filter(FietsRoute.pending.is_(False),
                                FietsRoute.hidden.is_(False))
    regios = sorted({r[0] for r in db.session.query(FietsRoute.regio)
                     .filter(FietsRoute.regio.isnot(None),
                             FietsRoute.pending.is_(False),
                             FietsRoute.hidden.is_(False)).all()})
    if regio:
        q = q.filter(FietsRoute.regio.ilike(regio))
    elif provincie:
        from ..regios import STREEK_PROVINCIE
        streken_in = [st for st, pr in STREEK_PROVINCIE.items()
                      if pr.lower() == provincie.lower()]
        q = q.filter(FietsRoute.regio.in_(streken_in)) if streken_in \
            else q.filter(db.false())
    rijen = q.order_by(FietsRoute.titel).all()
    # Gezinspersonalisatie: routes met startpunt binnen de straal eerst
    fam = current_family()
    afstanden = {}
    if fam and fam.postcode:
        from ..services.feestjes import postcode_coord
        from ..scoring import haversine_km
        centrum = postcode_coord(fam.postcode)
        if centrum:
            for r in rijen:
                if r.start_lat is not None:
                    afstanden[r.id] = round(haversine_km(
                        centrum[0], centrum[1], r.start_lat, r.start_lng))
            rijen.sort(key=lambda r: afstanden.get(r.id, 9999))
    # Levendige kaarten (patch 198): een gesprokkelde foto van een plek
    # onderweg, een tekstsnippet en het onderweg-profiel per route.
    from ..media import has_echte_foto, poi_image
    from ..models import RouteBuurt
    from ..types import groep_van
    beelden, onderweg, snippets = {}, {}, {}
    for r in rijen:
        if r.beschrijving:
            kort = " ".join(r.beschrijving.split())
            snippets[r.id] = kort[:130] + ("…" if len(kort) > 130 else "")
        tel = {"ravotten": 0, "smullen": 0, "beleven": 0}
        for b in (RouteBuurt.query.filter_by(route_id=r.id)
                  .order_by(RouteBuurt.route_km.asc()).limit(120).all()):
            ev = b.event
            if ev is None:
                continue
            g = groep_van(ev)
            if g in tel:
                tel[g] += 1
            if r.id not in beelden and not r.cover_photo_id \
                    and has_echte_foto(ev):
                beelden[r.id] = (poi_image(ev), ev.title)
        if any(tel.values()):
            onderweg[r.id] = tel
    kaartdata = [{"lat": r.start_lat, "lng": r.start_lng, "titel": r.titel,
                  "km": r.afstand_km, "regio": r.regio or "",
                  "url": url_for("public.fietsroute", slug=r.slug)}
                 for r in rijen if r.start_lat is not None]
    regio_labels = []
    for reg in regios:
        pts = [k for k in kaartdata if k["regio"] == reg]
        if pts:
            regio_labels.append({
                "regio": reg,
                "lat": sum(p["lat"] for p in pts) / len(pts),
                "lng": sum(p["lng"] for p in pts) / len(pts),
                "url": url_for("public.fietsroutes", regio=reg)})
    from ..regios import provincie_van_streek
    provincies = sorted({p for p in (provincie_van_streek(r) for r in regios)
                         if p})
    regio_provincie = {r.lower(): (provincie_van_streek(r) or "")
                       for r in regios}
    return render_template("public/fietsroutes.html", rijen=rijen, regios=regios,
                           regio=regio, provincie=provincie,
                           provincies=provincies,
                           regio_provincie=regio_provincie,
                           afstanden=afstanden,
                           beelden=beelden, onderweg=onderweg,
                           snippets=snippets, kaartdata=kaartdata,
                           regio_labels=regio_labels,
                           title="Gezinsfietsroutes", family=fam, active="routes")


@bp.route("/fietsroutes/<slug>")
def fietsroute(slug):
    from ..models import FietsRoute, RouteBuurt, Event
    from ..types import groep_van, type_code, TYPES
    from ..services.routes_gis import sample
    from .. import seo
    r = FietsRoute.query.filter_by(slug=slug).first_or_404()
    if (r.pending or r.hidden) and not session.get("admin_id"):
        abort(404)
    buurt = (RouteBuurt.query.filter_by(route_id=r.id)
             .order_by(RouteBuurt.route_km.asc()).all())
    partners = [b for b in buurt if partner_zichtbaar(b.event)]
    from ..models import RouteReview
    revs = RouteReview.query.filter_by(route_id=r.id).all()
    score = None
    if revs:
        score = {"kid": round(sum(x.kid_score for x in revs) / len(revs), 1),
                 "n": len(revs)}
    mijn_review = None
    fam_ = current_family()
    if fam_:
        mijn_review = RouteReview.query.filter_by(route_id=r.id,
                                                  family_id=fam_.id).first()

    # Evenementen vandaag langs de route (live, klein kandidatenveld)
    vandaag = []
    if r.bbox_n is not None:
        w_start, w_end = window("vandaag")
        marge = 0.006
        kandidaten = (bron_filter(geldige_events(Event.query, datetime.utcnow()))
                      .filter(Event.is_permanent.is_(False),
                              Event.lat.between(r.bbox_z - marge, r.bbox_n + marge),
                              Event.lng.between(r.bbox_w - marge, r.bbox_o + marge),
                              Event.start <= w_end,
                              db.or_(Event.end >= w_start, Event.start >= w_start))
                      .limit(40).all())
        if kandidaten and r.geometrie:
            from ..scoring import haversine_km
            lijn = sample([(p[0], p[1], None) for p in r.geometrie])
            for ev in kandidaten:
                if min(haversine_km(ev.lat, ev.lng, la, ln)
                       for la, ln, _ in lijn) * 1000 <= 600:
                    vandaag.append(ev)
    from ..models import Photo
    fotos = Photo.query.filter_by(route_id=r.id, status="approved").all()
    cover = db.session.get(Photo, r.cover_photo_id) if r.cover_photo_id else None
    kaartdata = {"route": r.geometrie or [],
                 "start": [r.start_lat, r.start_lng] if r.start_lat else None,
                 "lus": bool(r.is_lus),
                 "markers": [{"lat": b.event.lat, "lng": b.event.lng,
                              "title": b.event.title, "slug": b.event.slug,
                              "partner": partner_zichtbaar(b.event),
                              "groep": groep_van(b.event),
                              "emoji": TYPES.get(type_code(b.event), ("📍",))[0],
                              "km": b.route_km}
                             for b in buurt if b.event.lat is not None]}
    route_ld = seo.route_jsonld(r, cover, score)
    jsonld = [seo.breadcrumb_jsonld([("Ravot", "/"),
                                     ("Fietsroutes", "/fietsroutes"),
                                     (r.titel, f"/fietsroutes/{r.slug}")]),
              route_ld]
    from ..content import render_markdown
    beschrijving_html = render_markdown(r.beschrijving) if r.beschrijving else None
    routebeschrijving_html = (render_markdown(r.routebeschrijving)
                              if r.routebeschrijving else None)
    # Gezinslaag (patch 187): afstand tot de start voor gezin óf gast, en een
    # pauzeplan-samenvatting uit wat er onderweg ligt.
    start_km = None
    pc = (fam_.postcode if fam_ else None) or guest_profile().get("postcode")
    if pc and r.start_lat is not None:
        from ..geo import postcode_coord as _pc
        centrum = _pc(pc)
        if centrum:
            from ..scoring import haversine_km
            start_km = round(haversine_km(centrum[0], centrum[1],
                                          r.start_lat, r.start_lng))
    pauzeplan = None
    if buurt:
        tel = {"ravotten": 0, "smullen": 0, "beleven": 0}
        for b in buurt:
            g = groep_van(b.event)
            if g in tel:
                tel[g] += 1
        pauzeplan = tel if any(tel.values()) else None
    # Onderweg gegroepeerd (patch 209): één lange lijst met tientallen
    # horecazaken zegt niets; per groep met eigen kop leest veel sneller.
    from ..types import groep_van
    _emmers = {"ravotten": [], "smullen": [], "beleven": []}
    for b in buurt:
        g = groep_van(b.event) if b.event is not None else None
        if g in _emmers:
            _emmers[g].append(b)
    buurt_groepen = [
        ("ravotten", "Ravotten", "🛝", _emmers["ravotten"]),
        ("beleven", "Beleven", "🎭", _emmers["beleven"]),
        ("smullen", "Smullen", "🍦", _emmers["smullen"]),
    ]
    return render_template("public/fietsroute.html", r=r, buurt=buurt,
                           buurt_groepen=buurt_groepen,
                           start_km=start_km, pauzeplan=pauzeplan,
                           beschrijving_html=beschrijving_html,
                           routebeschrijving_html=routebeschrijving_html,
                           partners=partners, vandaag=vandaag, fotos=fotos,
                           cover=cover, kaartdata=kaartdata, jsonld=jsonld,
                           score=score, mijn_review=mijn_review,
                           title=f"{r.titel} — gezinsfietsroute",
                           family=current_family(), active="routes")


@bp.route("/fietsroutes/<slug>/gpx")
def fietsroute_gpx(slug):
    from ..models import FietsRoute
    from flask import send_file
    r = FietsRoute.query.filter_by(slug=slug).first_or_404()
    if r.pending or r.hidden or not r.gpx_bestand:
        abort(404)
    return send_file(f"/data/uploads/gpx/{r.gpx_bestand}",
                     mimetype="application/gpx+xml", as_attachment=True,
                     download_name=f"ravot-{r.slug}.gpx", max_age=86400)


@bp.route("/typebeeld/<sleutel>")
def typebeeld(sleutel):
    """Door de beheerder geüploade type-illustratie (patch 167)."""
    from flask import send_file
    from ..media import eigen_illustratie_pad
    pad = eigen_illustratie_pad(sleutel)
    if not pad:
        abort(404)
    return send_file(pad, mimetype="image/jpeg", max_age=86400)


@bp.route("/bonlogo/<int:bid>.png")
def bonlogo(bid):
    """Webshoplogo van een cadeaubon (patch 175) — publiek, want het staat
    ook in de bonmail die bij het gezin toekomt."""
    from flask import send_file
    from ..media import bon_logo_pad
    pad = bon_logo_pad(bid)
    if not pad:
        abort(404)
    return send_file(pad, mimetype="image/png", max_age=86400)


@bp.route("/bevestig/<int:event_id>/<veld>/<waarde>", methods=["POST"])
@limiter.limit("40/hour")
def anon_veld_stem(event_id, veld, waarde):
    """Voorziening bevestigen zónder account (patch 182).

    De drempel van een account is voor veel bezoekers te hoog terwijl ze wél
    willen bijdragen — melden kan vandaag ook al anoniem. Zo'n stem weegt half
    zo zwaar als die van een gezin: er is geen geschiedenis om vertrouwen op te
    bouwen. Er worden uiteraard geen ravotpunten toegekend.
    """
    from ..models import (Event, VeldStem, VOORZIENING_LABELS, ZACHTE_VELDEN,
                          get_bool)
    from .. import stemmen
    from .account import _herbereken_boolean
    import hashlib
    import secrets as _sec
    if not get_bool("anoniem_stemmen_aan"):
        abort(404)
    if session.get("family_id"):
        # Ingelogd? Dan via de gewone route, mét punten.
        return redirect(url_for("account.veld_stem", event_id=event_id,
                                veld=veld, waarde=waarde), code=307)
    if veld not in ZACHTE_VELDEN or waarde not in ("ja", "nee"):
        abort(400)
    ev = db.session.get(Event, event_id) or abort(404)
    if veld not in stemmen.relevante_velden(ev):
        abort(400)
    # Stabiele, niet-herleidbare bezoeker-id in de sessie (geen IP-opslag).
    anon = session.get("anon_stem_id")
    if not anon:
        anon = hashlib.sha256(_sec.token_bytes(16)).hexdigest()[:24]
        session["anon_stem_id"] = anon
        session.permanent = True
    ja = (waarde == "ja")
    bestaand = VeldStem.query.filter_by(event_id=ev.id, veld=veld,
                                        stemmer=f"anon:{anon}").first()
    if bestaand is not None and bestaand.waarde == ja:
        db.session.delete(bestaand)
        _herbereken_boolean(ev, veld)
        db.session.commit()
        flash("Je antwoord is ingetrokken.", "ok")
        return redirect(request.referrer or url_for("public.event", slug=ev.slug))
    stemmen.leg_stem_vast(ev.id, veld, ja, anon_id=anon)
    _herbereken_boolean(ev, veld)
    db.session.commit()
    lbl = VOORZIENING_LABELS.get(veld, veld)
    flash(f"Bedankt! Je antwoord over {lbl} is genoteerd. "
          "Met een gratis gezinsprofiel spaar je er ook ravotpunten mee. 🦊", "ok")
    return redirect(request.referrer or url_for("public.event", slug=ev.slug))


@bp.route("/fietsroutes/<slug>/bingo")
def fietsbingo(slug):
    """Afdrukbare fietsbingo (patch 200): de browser maakt er de PDF van."""
    from ..models import BingoInzending, FietsRoute, get_int, utcnow
    from ..services.bingo import items_voor_route
    r = FietsRoute.query.filter_by(slug=slug).first_or_404()
    if (r.pending or r.hidden) and not session.get("admin_id"):
        abort(404)
    nu = utcnow()
    maand = nu.year * 100 + nu.month
    fam = current_family()
    al_ingezonden = bool(fam and BingoInzending.query.filter_by(
        family_id=fam.id, route_id=r.id, maand=maand).first())
    return render_template("public/fietsbingo.html", r=r,
                           items=items_voor_route(r, maand),
                           maand_label=nu.strftime("%m/%Y"),
                           al_ingezonden=al_ingezonden,
                           punt_bingo=get_int("punt_bingo", 15),
                           family=fam, active="routes",
                           title=f"Fietsbingo — {r.titel}")


@bp.route("/fietsroutes/<slug>/bingo", methods=["POST"])
@limiter.limit("10/hour")
def fietsbingo_upload(slug):
    """Volle kaart insturen: één per gezin per route per maand; punten volgen
    pas na goedkeuring door de redactie."""
    from ..models import BingoInzending, FietsRoute, utcnow
    from ..fotos import verwerk_upload
    r = FietsRoute.query.filter_by(slug=slug).first_or_404()
    fam = current_family()
    if fam is None:
        flash("Maak eerst een gratis gezinsprofiel om mee te doen.", "error")
        return redirect(url_for("auth.login", terug=f"{slug}"))
    nu = utcnow()
    maand = nu.year * 100 + nu.month
    if BingoInzending.query.filter_by(family_id=fam.id, route_id=r.id,
                                      maand=maand).first():
        flash("Jullie kaart voor deze route is deze maand al ingestuurd. 🦊",
              "ok")
        return redirect(url_for("public.fietsbingo", slug=slug))
    naam = verwerk_upload(request.files.get("kaart"))
    if not naam:
        flash("Dat lukte niet — stuur een foto (jpg/png) van jullie "
              "ingevulde blad.", "error")
        return redirect(url_for("public.fietsbingo", slug=slug))
    db.session.add(BingoInzending(family_id=fam.id, route_id=r.id,
                                  filename=naam, maand=maand))
    db.session.commit()
    flash("Jullie bingokaart is binnen! We kijken hem na; daarna komen de "
          "ravotpunten erbij en dingen jullie mee naar de maandprijs. 🎉",
          "ok")
    return redirect(url_for("public.fietsroute", slug=slug))
