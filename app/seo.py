"""SEO/GEO-bouwstenen (zie SEO/GEO-plan §3–5):
- JSON-LD (schema.org Event, AggregateRating, Breadcrumb, FAQ)
- meta-title/description-templates per paginatype
- feitelijke antwoordblokken bovenaan elke pagina (AI-citeerbaar)
"""
import json
from datetime import timedelta
from flask import current_app


def _abs(path):
    return current_app.config["SITE_URL"].rstrip("/") + path


# Permanente plekken zijn géén Event (Google eist dan startDate en meldt een
# kritiek probleem). Ze krijgen het passende Place-subtype; horeca wordt
# LocalBusiness, de rest TouristAttraction.
_PLEK_TYPE = {
    "horeca": "Restaurant", "zomerbar": "Restaurant", "winterbar": "Restaurant",
    "ijssalon": "Restaurant", "museum": "Museum", "zwembad": "SportsActivityLocation",
    "zwemvijver": "SportsActivityLocation", "playground": "Playground",
    "park": "Park", "speelbos": "Park", "farm": "TouristAttraction",
    "kinderboerderij": "TouristAttraction",
}


def _beeld(event):
    """Absolute beeld-URL: echte foto (extern of eigen) — nooit een placeholder,
    want een illustratie is geen afbeelding van de plek zelf."""
    if getattr(event, "image_url", None):
        u = event.image_url
        return _abs(u) if u.startswith("/") else u
    from .models import Photo
    p = (Photo.query.filter_by(event_id=event.id, status="approved")
         .order_by(Photo.id).first())
    return _abs(f"/foto/{p.id}") if p else None


def _adres_blok(event):
    return {"@type": "PostalAddress",
            "streetAddress": getattr(event, "adres", None),
            "addressLocality": event.gemeente,
            "postalCode": event.postcode, "addressCountry": "BE"}


def plek_jsonld(event, agg=None):
    """Structured data voor een permanente plek (speeltuin, museum, horeca)."""
    subtype = getattr(event, "subtype", None)
    data = {
        "@context": "https://schema.org",
        "@type": _PLEK_TYPE.get(subtype, "TouristAttraction"),
        "name": event.title,
        "url": _abs(f"/e/{event.slug}"),
        "description": (event.description or "")[:300] or None,
        "image": _beeld(event),
        "address": {k: v for k, v in _adres_blok(event).items() if v},
        "isAccessibleForFree": bool(event.is_free),
    }
    if event.lat and event.lng:
        data["geo"] = {"@type": "GeoCoordinates", "latitude": event.lat,
                       "longitude": event.lng}
    if getattr(event, "telefoon", None):
        data["telephone"] = event.telefoon
    if getattr(event, "source_url", None):
        data["sameAs"] = event.source_url
    if agg and agg["count"] >= 1:
        data["aggregateRating"] = {
            "@type": "AggregateRating", "ratingValue": agg["avg"],
            "reviewCount": agg["count"], "bestRating": 5, "worstRating": 1,
        }
    return json.dumps({k: v for k, v in data.items() if v is not None},
                      ensure_ascii=False)


def event_jsonld(event, agg=None, family_total=None):
    # Zonder startDate is Event ongeldig voor Google -> permanente plek-schema.
    if getattr(event, "is_permanent", False) or not event.start:
        return plek_jsonld(event, agg)
    eind = event.end or (event.start + timedelta(hours=2))
    data = {
        "@context": "https://schema.org",
        "@type": "Event",
        "name": event.title,
        "startDate": event.start.isoformat(),
        "endDate": eind.isoformat(),
        "eventStatus": "https://schema.org/EventScheduled",
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "url": _abs(f"/e/{event.slug}"),
        "description": (event.description or "")[:300]
                       or f"Activiteit voor gezinnen in {event.gemeente or 'Vlaanderen'}.",
        "image": _beeld(event),
        "typicalAgeRange": f"{event.age_min}-{event.age_max}",
        "isAccessibleForFree": bool(event.is_free),
    }
    if event.organizer:
        data["performer"] = {"@type": "Organization", "name": event.organizer.name}
    if event.venue and event.venue.lat:
        data["location"] = {
            "@type": "Place",
            "name": event.venue.name,
            "address": {k: v for k, v in _adres_blok(event).items() if v},
            "geo": {"@type": "GeoCoordinates", "latitude": event.venue.lat,
                    "longitude": event.venue.lng},
        }
    elif event.lat and event.lng:
        # Zonder venue-record tóch een location: Google vereist die.
        data["location"] = {
            "@type": "Place", "name": event.gemeente or event.title,
            "address": {k: v for k, v in _adres_blok(event).items() if v},
            "geo": {"@type": "GeoCoordinates", "latitude": event.lat,
                    "longitude": event.lng},
        }
    elif event.gemeente:
        data["location"] = {"@type": "Place", "name": event.gemeente,
                            "address": {k: v for k, v in _adres_blok(event).items() if v}}
    if event.organizer:
        data["organizer"] = {"@type": "Organization", "name": event.organizer.name}
    if event.price_info:
        data["offers"] = [{
            "@type": "Offer", "name": t.get("name"),
            "price": t.get("price"), "priceCurrency": "EUR",
            "availability": "https://schema.org/InStock",
            "url": _abs(f"/e/{event.slug}"),
        } for t in event.price_info]
    elif event.is_free:
        data["offers"] = [{"@type": "Offer", "price": "0", "priceCurrency": "EUR",
                           "availability": "https://schema.org/InStock",
                           "url": _abs(f"/e/{event.slug}")}]
    if agg and agg["count"] >= 1:
        data["aggregateRating"] = {
            "@type": "AggregateRating", "ratingValue": agg["avg"],
            "reviewCount": agg["count"], "bestRating": 5, "worstRating": 1,
        }
    return json.dumps({k: v for k, v in data.items() if v is not None}, ensure_ascii=False)


def breadcrumb_jsonld(items):
    """items: [(naam, pad of volledige URL), ...]

    Gestructureerde data moet absolute URL's bevatten — een relatieve link
    negeert Google stilzwijgend. _abs laat volledige URL's ongemoeid, zodat
    beide aanroepvormen werken (patch 230).
    """
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": name,
             "item": path if str(path).startswith("http") else _abs(path)}
            for i, (name, path) in enumerate(items)
        ],
    }, ensure_ascii=False)


def faq_jsonld(pairs):
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q,
             "acceptedAnswer": {"@type": "Answer", "text": a}}
            for q, a in pairs
        ],
    }, ensure_ascii=False)


# -- Meta-templates (SEO-plan §3) ---------------------------------------------

def _n_act(n):
    return f"{n} activiteit" if n == 1 else f"{n} activiteiten"


def meta_gemeente(gemeente, n, scope="dit weekend"):
    title = f"Wat te doen met kinderen in {gemeente} {scope}? {_n_act(n)} · Ravot"
    desc = (f"{_n_act(n).replace('activiteit', 'kinderactiviteit')} in {gemeente} {scope}, gefilterd op leeftijd en "
            f"budget, met echte Ravotscores van gezinnen. Gratis en zonder account te gebruiken.")
    return title, desc


def _leeftijd_zin(event):
    """Leeftijdsfragment voor SEO-tekst, of '' bij standaard-vulwaarden."""
    lo, hi = event.age_min, event.age_max
    if lo is None or hi is None:
        return ""
    if (lo, hi) in ((0, 99), (0, 12), (0, 16), (0, 18)) or (lo <= 0 and hi >= 99):
        return ""
    if lo <= 0:
        return f", tot {hi} jaar"
    if hi >= 99:
        return f", vanaf {lo} jaar"
    return f", voor {lo}-{hi} jaar"


def meta_event(event, family_total=None):
    prijs = "gratis" if event.is_free else (f"vanaf €{family_total}" if family_total else "")
    title = f"{event.title} — {event.gemeente or 'Vlaanderen'} · Ravot"
    desc = (f"{event.title} in {event.gemeente}{_leeftijd_zin(event)}"
            + (f", {prijs}" if prijs else "") + ". Bekijk Ravotscores en de echte kost.")
    return title, desc[:158]


# -- Antwoordblok: feitelijk, actueel, AI-citeerbaar (SEO-plan §5) ---------------

def answer_block(gemeente, scope, events, top=None):
    n = len(events)
    if n == 0:
        return (f"{scope.capitalize()} vonden we nog geen kinderactiviteiten in {gemeente}. "
                f"Bekijk buurgemeenten of vergroot je straal.")
    n_free = sum(1 for e in events if e.is_free)
    werkw = "is" if n == 1 else "zijn"
    txt = f"{scope.capitalize()} {werkw} er in {gemeente} {_n_act(n)} voor kinderen"
    txt += (f", waarvan {n_free} gratis." if n_free else ".") if n > 1 else (" — en die is gratis." if n_free else ".")
    if top is not None:
        ev, agg = top
        if agg:
            txt += f" Topscore: {ev.title} (Ravotscore {agg['avg']} bij {agg['count']} gezinnen)."
    return txt


def route_jsonld(r, cover=None, score=None):
    """TouristTrip-schema voor een gezinsfietsroute (patch 160/162)."""
    uit = {"@context": "https://schema.org", "@type": "TouristTrip",
           "name": r.titel,
           "description": (r.beschrijving or "")[:300],
           "touristType": "families with children"}
    if r.afstand_km:
        uit["distance"] = f"{r.afstand_km} km"
    if r.start_lat:
        uit["itinerary"] = {"@type": "Place",
                            "geo": {"@type": "GeoCoordinates",
                                    "latitude": r.start_lat,
                                    "longitude": r.start_lng},
                            "name": f"Start in {r.gemeente or 'Vlaanderen'}"}
    if cover:
        uit["image"] = f"/foto/{cover.id}"
    if getattr(r, "bron_naam", None):
        uit["publisher"] = {"@type": "Organization", "name": r.bron_naam}
        if getattr(r, "bron_url", None):
            uit["isBasedOn"] = r.bron_url
    if score:
        uit["aggregateRating"] = {"@type": "AggregateRating",
                                  "ratingValue": score["kid"],
                                  "bestRating": 5,
                                  "ratingCount": score["n"]}
    return json.dumps(uit, ensure_ascii=False)


def itemlist_jsonld(items):
    """ItemList voor een overzichtspagina (patch 228): zo begrijpt Google dat
    dit een lijst van plekken is en niet één lange lap tekst."""
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "ItemList",
        "numberOfItems": len(items),
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": naam, "url": url}
            for i, (naam, url) in enumerate(items)
        ],
    }, ensure_ascii=False)


def organisatie_jsonld():
    """Wie is Ravot? (patch 230)

    Zonder dit weet Google niet dat ravot.be, de Facebook-, Instagram- en
    TikTok-pagina's bij één organisatie horen. Met sameAs kan het die signalen
    bundelen — belangrijk voor een jong domein zonder backlinks.
    """
    from flask import current_app
    from .models import get_setting
    kanalen = [get_setting(k) for k in
               ("social_facebook", "social_instagram", "social_tiktok")]
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": "Ravot",
        "url": current_app.config["SITE_URL"],
        "logo": _abs("/static/img/icon-512.png"),
        "description": "Gezinsuitstappen in Vlaanderen: speeltuinen, "
                       "kinderboerderijen, musea en fietsroutes, met echte "
                       "scores van gezinnen.",
        "areaServed": {"@type": "AdministrativeArea", "name": "Vlaanderen"},
        "sameAs": [k for k in kanalen if k],
    }, ensure_ascii=False)


def website_jsonld():
    """WebSite met SearchAction: meldt de zoekfunctie aan bij Google."""
    from flask import current_app
    site = current_app.config["SITE_URL"]
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": "Ravot",
        "url": site,
        "inLanguage": "nl-BE",
        "potentialAction": {
            "@type": "SearchAction",
            "target": {"@type": "EntryPoint",
                       "urlTemplate": f"{site}/ontdek?q={{search_term_string}}"},
            "query-input": "required name=search_term_string",
        },
    }, ensure_ascii=False)
