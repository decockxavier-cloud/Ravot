"""SEO/GEO-bouwstenen (zie SEO/GEO-plan §3–5):
- JSON-LD (schema.org Event, AggregateRating, Breadcrumb, FAQ)
- meta-title/description-templates per paginatype
- feitelijke antwoordblokken bovenaan elke pagina (AI-citeerbaar)
"""
import json
from flask import current_app


def _abs(path):
    return current_app.config["SITE_URL"].rstrip("/") + path


def event_jsonld(event, agg=None, family_total=None):
    data = {
        "@context": "https://schema.org",
        "@type": "Event",
        "name": event.title,
        "startDate": event.start.isoformat() if event.start else None,
        "endDate": event.end.isoformat() if event.end else None,
        "eventStatus": "https://schema.org/EventScheduled",
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "url": _abs(f"/e/{event.slug}"),
        "description": (event.description or "")[:300],
        "image": event.image_url,
        "typicalAgeRange": f"{event.age_min}-{event.age_max}",
        "isAccessibleForFree": bool(event.is_free),
    }
    if event.venue and event.venue.lat:
        data["location"] = {
            "@type": "Place",
            "name": event.venue.name,
            "address": {"@type": "PostalAddress", "addressLocality": event.gemeente,
                        "postalCode": event.postcode, "addressCountry": "BE"},
            "geo": {"@type": "GeoCoordinates", "latitude": event.venue.lat,
                    "longitude": event.venue.lng},
        }
    if event.organizer:
        data["organizer"] = {"@type": "Organization", "name": event.organizer.name}
    if event.price_info:
        data["offers"] = [{
            "@type": "Offer", "name": t.get("name"),
            "price": t.get("price"), "priceCurrency": "EUR",
        } for t in event.price_info]
    if agg and agg["count"] >= 1:
        data["aggregateRating"] = {
            "@type": "AggregateRating", "ratingValue": agg["avg"],
            "reviewCount": agg["count"], "bestRating": 5, "worstRating": 1,
        }
    return json.dumps({k: v for k, v in data.items() if v is not None}, ensure_ascii=False)


def breadcrumb_jsonld(items):
    """items: [(naam, pad), ...]"""
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": name, "item": _abs(path)}
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


def meta_event(event, family_total=None):
    prijs = "gratis" if event.is_free else (f"vanaf €{family_total}" if family_total else "")
    title = f"{event.title} — {event.gemeente or 'Vlaanderen'} · Ravot"
    desc = (f"{event.title} in {event.gemeente}, voor {event.age_min}-{event.age_max} jaar"
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
