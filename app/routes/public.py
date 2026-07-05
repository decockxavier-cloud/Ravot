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
from datetime import datetime, timedelta

from flask import (Blueprint, abort, current_app, g, redirect, render_template,
                   request, session, url_for, Response)

from ..extensions import db, limiter
from ..models import (Event, Family, Interaction, PostcodeCentroid, Review,
                      SavedEvent, Share, Connection)
from ..pricing import aggregate_ravotscore, euro_indicator, family_price
from ..scoring import Profile, score_event
from .. import seo

bp = Blueprint("public", __name__)

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


def build_profile():
    fam = current_family()
    if fam:
        centroid = db.session.get(PostcodeCentroid, fam.postcode)
        return Profile(
            child_ages=fam.child_ages(),
            lat=centroid.lat if centroid else None,
            lng=centroid.lng if centroid else None,
            radius_km=fam.radius_km, budget_pref=fam.budget_pref,
            interest_weights={i.category: i.weight for i in fam.interests},
        ), fam
    guest = guest_profile()
    centroid = db.session.get(PostcodeCentroid, guest.get("postcode", "")) if guest else None
    ages = guest.get("ages", [])
    return Profile(
        child_ages=ages,
        lat=centroid.lat if centroid else None,
        lng=centroid.lng if centroid else None,
        radius_km=int(guest.get("radius", 25)),
        budget_pref=guest.get("budget", "all"),
    ), None


def log(type_, event_id=None, **meta):
    fam = current_family()
    db.session.add(Interaction(family_id=fam.id if fam else None,
                               event_id=event_id, type=type_, meta=meta))
    db.session.commit()


# -------------------------------------------------------------- tijdsvensters --

def window(scope):
    now = datetime.utcnow()
    if scope == "vandaag":
        end = now.replace(hour=23, minute=59, second=59)
        return now - timedelta(hours=12), end  # nog-bezige events tellen mee
    if scope == "weekend":
        days_to_sat = (5 - now.weekday()) % 7
        sat = (now + timedelta(days=days_to_sat)).replace(hour=0, minute=0)
        if now.weekday() >= 5:  # het is al weekend
            sat = now.replace(hour=0, minute=0)
        return sat, sat + timedelta(days=(7 - sat.weekday()) % 7 or 2)
    return now, now + timedelta(days=30)


def scored_events(profile, scope, extra_filter=None, limit=40, weer=True):
    start, end = window(scope)
    q = Event.query.filter(Event.start <= end, (Event.end >= start) | (Event.start >= start))
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
        agg = agg_cache.get(e.series_id)
        if agg is None and e.series_id:
            series_reviews = Review.query.join(Event, Review.event_id == Event.id) \
                .filter(Event.series_id == e.series_id).all()
            agg = aggregate_ravotscore(series_reviews)
            agg_cache[e.series_id] = agg or False
        agg = agg or None
        s = score_event(e, profile, ravot_avg=agg["avg"] if agg else None)
        if s > 0:
            # weerbonus: bij regen binnen omhoog, buiten omlaag
            if regen is not None:
                if regen >= 50:
                    s *= 1.3 if e.indoor else 0.85
                elif regen <= 20 and not e.indoor:
                    s *= 1.1
            total, _ = family_price(e.price_info, profile.child_ages)
            rows.append({"event": e, "score": s, "agg": agg,
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


@bp.route("/welkom")
def welkom():
    """Landingspagina, ook zichtbaar mét actieve zoekopdracht/profiel."""
    from ..models import Event, Review
    stats = {
        "events": Event.query.count(),
        "gemeenten": db.session.query(Event.gemeente).filter(
            Event.gemeente.isnot(None)).distinct().count(),
        "reviews": Review.query.count(),
    }
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
        session["guest"] = {
            "postcode": request.form.get("postcode", "").strip()[:4],
            "ages": ages[:6],
            "radius": request.form.get("radius", 25),
            "budget": request.form.get("budget", "all"),
        }
        session.permanent = True
        return redirect(url_for("public.vandaag"))
    return render_template("public/proberen.html", family=None, active=None,
                           title="Meteen kijken wat er te doen is")


@bp.route("/", methods=["GET"])
def vandaag():
    profile, fam = build_profile()
    has_profile = bool(fam or guest_profile().get("postcode"))
    if not has_profile:
        # Nieuwe bezoeker zonder profiel → landingspagina (website-jas)
        from ..models import Event, Review
        stats = {
            "events": Event.query.count(),
            "gemeenten": db.session.query(Event.gemeente).filter(
                Event.gemeente.isnot(None)).distinct().count(),
            "reviews": Review.query.count(),
        }
        return render_template("public/landing.html", stats=stats,
                               family=None, active=None,
                               title="Ravot — waar gaan we vandaag ravotten?")
    rows = scored_events(profile, "vandaag")
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
                           has_profile=has_profile, family=fam, active="vandaag")


@bp.route("/weekend")
def weekend():
    profile, fam = build_profile()
    has_profile = bool(fam or guest_profile().get("postcode"))
    rows = scored_events(profile, "weekend") if has_profile else []
    return render_template("public/lijst.html", rows=rows, scope="dit weekend",
                           title="Dit weekend", answer=None,
                           regen=rows[0].get("regen") if rows else None,
                           has_profile=has_profile, family=fam, active="weekend")


@bp.route("/verkennen")
def verkennen():
    profile, fam = build_profile()
    rows = scored_events(profile, "maand", limit=200) if (fam or guest_profile()) else []
    markers = [{
        "lat": r["event"].lat, "lng": r["event"].lng,
        "title": r["event"].title, "url": url_for("public.event", slug=r["event"].slug),
        "score": (r["agg"] or {}).get("avg"), "free": r["event"].is_free,
    } for r in rows if r["event"].lat]
    center = [profile.lat or 50.85, profile.lng or 4.35]
    return render_template("public/verkennen.html", markers=markers, center=center,
                           family=fam, active="verkennen", title="Verkennen")


@bp.route("/e/<slug>")
def event(slug):
    ev = Event.query.filter_by(slug=slug).first_or_404()
    if ev.end and ev.end < datetime.utcnow() - timedelta(days=1) and ev.series:
        # SEO §2.3: afgelopen event → permanente reekspagina (301)
        return redirect(url_for("public.reeks", slug=ev.series.slug), code=301)
    profile, fam = build_profile()
    series_reviews = []
    if ev.series_id:
        series_reviews = Review.query.join(Event, Review.event_id == Event.id) \
            .filter(Event.series_id == ev.series_id).all()
    agg = aggregate_ravotscore(series_reviews)
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
    return render_template(
        "public/event.html", ev=ev, agg=agg, family_total=total,
        euro=euro_indicator(total), reviews=[r.public_dict() for r in series_reviews[:10]],
        friends=friends_interested, saved=saved, shared=shared, family=fam,
        meta_title=title, meta_desc=desc,
        jsonld=[seo.event_jsonld(ev, agg, total),
                seo.breadcrumb_jsonld([("Ravot", "/"),
                                       (ev.gemeente or "Vlaanderen", f"/{(ev.gemeente or '').lower()}"),
                                       (ev.title, f"/e/{ev.slug}")])],
        active=None, title=ev.title,
    )


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
    q = Event.query.filter(db.func.lower(Event.gemeente) == gemeente.lower(),
                           Event.start <= end,
                           (Event.end >= start) | (Event.start >= start))
    if facet == "gratis":
        q = q.filter(Event.is_free.is_(True))
    if facet == "binnen":
        q = q.filter(Event.indoor.is_(True))
    if facet in FACET_AGES:
        lo, hi = FACET_AGES[facet]
        q = q.filter(Event.age_min <= hi, Event.age_max >= lo)
    return q.order_by(Event.start).limit(100).all()


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
    return render_template("public/gemeente.html", gemeente=naam, facet=facet,
                           facets=FACETS, events=events, answer=answer, buren=buren,
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


@bp.route("/over")
def over():
    return render_template("public/over.html", family=current_family(),
                           active=None, title="Over Ravot")


@bp.route("/privacy")
def privacy():
    return render_template("public/privacy.html", family=current_family(),
                           active=None, title="Privacy- en cookieverklaring")


@bp.route("/voorwaarden")
def voorwaarden():
    return render_template("public/voorwaarden.html", family=current_family(),
                           active=None, title="Gebruiksvoorwaarden")
