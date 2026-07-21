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
from datetime import datetime, timedelta

from flask import (Blueprint, abort, current_app, g, redirect, render_template,
                   request, session, url_for, Response)

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


def geldige_events(query, now=None):
    """Beperk een Event-query tot het geldige venster (niet voorbij, niet absurd ver).
    Een event is 'voorbij' als zijn EINDE achter de ondergrens ligt — zo blijven
    lopende activiteiten (bv. hele-dag, al bezig) gewoon zichtbaar."""
    onder, boven = geldig_venster(now)
    # Permanente POI's (speeltuinen, attracties) hebben geen datum en zijn
    # altijd geldig; gedateerde events moeten binnen het venster vallen.
    # Kampen horen NIET in de gewone lijsten/kaart — die hebben hun eigen
    # onderdeel (/kampen), net als feestjes.
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
    candidates = Event.query.filter(Event.is_permanent.is_(True),
                                    Event.hidden.is_(False), Event.pending.is_(False)).limit(3000).all()
    rows = []
    for e in candidates:
        s = score_event(e, profile)
        if s > 0:
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


def scored_events(profile, scope, extra_filter=None, limit=40, weer=True):
    start, end = window(scope)
    now = datetime.utcnow()
    onder, boven = geldig_venster(now)
    # Harde grenzen: nooit afgelopen events, nooit absurd ver in de toekomst.
    # 'Afgelopen' = het EINDE ligt achter de ondergrens (lopende events tellen mee).
    q = Event.query.filter(
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
    agg_cache = {}
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
    ev = Event.query.filter_by(slug=slug).first_or_404()

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
        ages = [int(a) for a in request.form.getlist("age") if a.strip().isdigit()]
        try:
            radius = int(re.sub(r"\D", "", request.form.get("radius", "") or "") or 25)
        except ValueError:
            radius = 25
        session["guest"] = {
            "postcode": re.sub(r"\D", "", request.form.get("postcode", ""))[:4],
            "ages": ages[:6],
            "radius": max(1, min(radius, 200)),
            "budget": request.form.get("budget", "all"),
        }
        session.permanent = True
        return redirect(url_for("public.vandaag"))
    return render_template("public/proberen.html", family=None, active=None,
                           title="Meteen kijken wat er te doen is")


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
                           has_profile=has_profile, family=fam, active="vandaag")


@bp.route("/deze-week")
def deze_week():
    profile, fam = build_profile()
    has_profile = bool(fam or guest_profile().get("postcode"))
    rows = scored_events(profile, "deze-week") if has_profile else _gast_rows("deze-week")
    return render_template("public/lijst.html", rows=rows, scope="deze week",
                           title="Deze week", answer=None,
                           regen=rows[0].get("regen") if rows else None,
                           weer=weerbericht("deze-week", fam),
                           has_profile=has_profile, family=fam, active="deze-week")


@bp.route("/weekend")
def weekend():
    profile, fam = build_profile()
    has_profile = bool(fam or guest_profile().get("postcode"))
    rows = scored_events(profile, "weekend") if has_profile else _gast_rows("weekend")
    return render_template("public/lijst.html", rows=rows, scope="dit weekend",
                           title="Dit weekend", answer=None,
                           regen=rows[0].get("regen") if rows else None,
                           weer=weerbericht("weekend", fam),
                           has_profile=has_profile, family=fam, active="weekend")


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
                              "babyvoeding", "huisdieren")}
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
    # Gedateerde events eerst (permanente POI's met start=None achteraan), zodat
    # de 1000-cap niet volloopt met permanente plekken.
    candidates = q.order_by(Event.start.is_(None).asc(),
                            Event.start.asc()).limit(1000).all()
    # Bekende plaats gezocht? Filter op afstand (buurgemeenten mee).
    if centrum:
        candidates = _filter_buurt([{"event": e} for e in candidates], centrum, 20)
        candidates = [r["event"] for r in candidates]

    # Ravotscore ophalen (voor tonen + sorteren) — commercieel zonder Partner
    # toont geen badge en telt niet mee (afspraak 2c/3c).
    rows, agg_cache = [], {}
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
    max_pagina = max(1, (totaal + per_pagina - 1) // per_pagina)
    pagina = min(pagina, max_pagina)
    begin = (pagina - 1) * per_pagina
    pagina_rows = rows[begin:begin + per_pagina]

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
    return render_template("public/ontdek.html", lft=lft, leeftijden=LEEFTIJDEN, rows=pagina_rows, sort=sort, zoek=zoek, wanneer=wanneer, cat=cat, verberg_sp=verberg_sp, toon_alles=toon_alles, curatie_aan=_gb("enkel_gecureerd"), ouder_filters=ouder_filters, weer=weer, soort=soort, groep=groep, soorten=TYPES, flink=_ontdek_url, aantal_actief=aantal_actief,
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
    gedateerd_q = geldige_events(Event.query, now).filter(
        Event.lat.isnot(None), Event.is_permanent.is_(False))
    if wanneer in ("vandaag", "deze-week", "weekend"):
        w_start, w_end = window(wanneer)
        gedateerd_q = gedateerd_q.filter(
            Event.start <= w_end, (Event.end >= w_start) | (Event.start >= w_start))
    gedateerd = gedateerd_q.order_by(Event.start).limit(500).all()
    # Permanente plekken: beste fiches eerst (niet alfabetisch — dan vielen
    # nieuwe types zoals horeca buiten de limiet). Horeca krijgt een eigen
    # gegarandeerd deel, zodat kindvriendelijke restaurants altijd op de
    # kaart staan, hoeveel speeltuinen er ook zijn.
    perm_basis = Event.query.filter(
        Event.lat.isnot(None), Event.is_permanent.is_(True),
        Event.hidden.is_(False), Event.pending.is_(False))
    # Expliciete soort-keuze? Dan het contingent daarop vernauwen — anders kan
    # een zeldzaam type (zomerbar, rommelmarkt) verdrongen worden door de 500
    # best-scorende speeltuinen en lijkt de filter "kapot".
    from ..types import TYPES as _TYPES
    _soort_vooraf = request.args.get("soort") or ""
    if _soort_vooraf in _TYPES:
        perm_basis = perm_basis.filter(Event.subtype == _soort_vooraf)
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
                              "babyvoeding", "huisdieren")}
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
        "partner": partner_actief(e),
        "score": None, "count": None,
    }


@bp.route("/e/<slug>")
@limiter.limit("60/minute;1000/hour")  # fiches: 15k stuks leegtrekken duurt zo dagen per IP
def event(slug):
    ev = Event.query.filter_by(slug=slug).first_or_404()
    # Nog niet gemodereerde gebruikersbijdrage: niet publiek tonen.
    # (Enkel de indiener zelf mag meekijken; geen indiener bekend => niemand.)
    if ev.pending and (ev.submitted_by is None
                       or session.get("family_id") != ev.submitted_by):
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
    return render_template(
        "public/event.html", ev=ev, agg=agg if toon_score else None,
        toon_score=toon_score, family_total=total,
        daguitstappen=mijn_daguitstappen,
        euro=euro_indicator(total), reviews=[r.public_dict() for r in series_reviews[:10]],
        friends=friends_interested, saved=saved, shared=shared, family=fam,
        fotos=goedgekeurde_fotos,
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
    return render_template("public/score_uitleg.html", family=fam,
                           geldig_maanden=get_int("punten_geldig_maanden", 6),
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
    q = Event.query.filter(
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
    partners = Event.query.filter(
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
    gemeenten = db.session.query(Event.gemeente, db.func.count(Event.id)) \
        .filter(Event.gemeente.isnot(None)).group_by(Event.gemeente).all()
    for g_, n in gemeenten:
        if n >= current_app.config["NOINDEX_MIN_EVENTS"]:
            urls.append(f"{site}/{g_.lower()}")
            for facet in FACETS:
                urls.append(f"{site}/{g_.lower()}/{facet}")
    for (slug,) in db.session.query(Event.slug).filter(
            Event.start >= datetime.utcnow() - timedelta(days=1)).all():
        urls.append(f"{site}/e/{slug}")
    from ..models import EditionSeries
    for (slug,) in db.session.query(EditionSeries.slug).all():
        urls.append(f"{site}/uitstap/{slug}")
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    xml += [f"<url><loc>{u}</loc></url>" for u in urls]
    xml.append("</urlset>")
    return Response("".join(xml), mimetype="application/xml")


@bp.route("/manifest.webmanifest")
def manifest():
    import json
    return Response(json.dumps({
        "name": "Ravot", "short_name": "Ravot",
        "description": "Waar gaan we vandaag ravotten?",
        "start_url": "/", "display": "standalone",
        "background_color": "#FAF7F0", "theme_color": "#2E7D46",
        "icons": [{"src": "/static/img/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
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
    q = Event.query.filter(Event.is_kamp.is_(True), Event.hidden.is_(False),
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
