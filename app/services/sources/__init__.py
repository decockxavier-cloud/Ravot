"""Bronnen-dispatcher.

Eén plek die weet welke bronnen bestaan, welke aan/uit staan, hoe je ze draait,
hun status bijhoudt (voor de admin) en hoe je ze opruimt. UiT houdt zijn eigen
gespecialiseerde sync (reeks-matching, centroids); de andere bronnen delen de
generieke upsert uit `base.run_source`.
"""
from flask import current_app

from ...extensions import db
from ...models import get_bool
from . import ticketmaster, toerisme, osm, wikidata, feeds
from .base import run_source

# naam -> (setting-key, label, adaptermodule)
REGISTRY = {
    "uit": ("bron_uit_aan", "UiTdatabank (publiq)", None),   # eigen sync-pad
    "tm": ("bron_tm_aan", "Ticketmaster (Family)", ticketmaster),
    "tv": ("bron_tv_aan", "Toerisme Vlaanderen", toerisme),
    "osm": ("bron_osm_aan", "OpenStreetMap", osm),
    "wd": ("bron_wd_aan", "Wikidata", wikidata),
    "feed": ("bron_feed_aan", "Agenda-feeds (iCal/RSS)", feeds),
}


def source_enabled(name):
    key = REGISTRY.get(name, (None,))[0]
    return bool(key) and get_bool(key)


# ------------------------------------------------------------------ status --

def _set_status(source, state, result=None, error=None):
    """Werk de status van een bron bij. Mag NOOIT de sync zelf breken."""
    from ...models import SyncStatus, utcnow
    try:
        row = db.session.get(SyncStatus, source) or SyncStatus(source=source)
        row.state = state
        if state in ("done", "error", "idle"):
            row.last_run = utcnow()
        if result is not None:
            row.last_result = str(result)[:200]
        if error is not None:
            row.last_error = str(error)[:300]
        db.session.add(row)
        db.session.commit()
    except Exception:
        db.session.rollback()


def is_sync_running():
    from ...models import SyncStatus
    try:
        return db.session.query(SyncStatus).filter_by(state="running").count() > 0
    except Exception:
        return False


def get_statuses():
    from ...models import SyncStatus
    try:
        return {s.source: s for s in SyncStatus.query.all()}
    except Exception:
        return {}


# -------------------------------------------------------------------- sync --

def sync_one(name):
    """Draai één bron en houd de status bij. Retourneert een resultaat-dict."""
    _set_status(name, "running")
    try:
        if name == "uit":
            from ..uit_sync import run_sync
            n = run_sync()
            res = {"bron": "uit", "verwerkt": n, "verworpen": 0}
        else:
            adapter = REGISTRY.get(name, (None, None, None))[2]
            if adapter is None:
                _set_status(name, "error", error="onbekende bron")
                return {"bron": name, "verwerkt": 0, "verworpen": 0, "fout": "onbekende bron"}
            processed, rejected = run_source(adapter)
            res = {"bron": name, "verwerkt": processed, "verworpen": rejected}
        _set_status(name, "done",
                    result=f"{res['verwerkt']} verwerkt, {res['verworpen']} verworpen")
        return res
    except Exception as exc:
        db.session.rollback()
        _set_status(name, "error", error=str(exc))
        raise


def sync_all():
    """Draai alle INGESCHAKELDE bronnen. Eén falende bron blokkeert de rest niet."""
    results = []
    for name in REGISTRY:
        if not source_enabled(name):
            continue
        try:
            results.append(sync_one(name))
        except Exception as exc:
            db.session.rollback()
            current_app.logger.warning("bron %s faalde: %s", name, str(exc)[:160])
            results.append({"bron": name, "verwerkt": 0, "verworpen": 0,
                            "fout": str(exc)[:160]})
    # POI's zonder eigen geo alsnog op de kaart via postcode-zwaartepunt
    try:
        from ..uit_sync import update_centroids, backfill_geo_from_postcode
        update_centroids()
        backfill_geo_from_postcode()
        update_centroids()
        db.session.commit()
    except Exception:
        db.session.rollback()
    # Dubbels (dezelfde plek uit meerdere bronnen) verbergen
    try:
        dedup_pois()
    except Exception:
        db.session.rollback()
    return results


# ----------------------------------------------------------------- dedup --

def _naam_tokens(titel):
    import re
    import unicodedata
    t = unicodedata.normalize("NFKD", (titel or "")).encode("ascii", "ignore").decode().lower()
    stop = {"de", "het", "een", "van", "the", "le", "la", "les", "museum", "park"}
    return set(re.findall(r"[a-z0-9]+", t)) - stop


def dedup_pois():
    """Verberg dubbele permanente POI's (dezelfde plek uit meerdere bronnen).
    Conservatief: enkel samenvoegen bij nabijheid (<150m) én sterke naamgelijkenis.
    De rijkste bron blijft zichtbaar en erft ontbrekende foto/website/beschrijving.
    Retourneert het aantal verborgen dubbels."""
    from collections import defaultdict
    from ...models import Event
    from ...scoring import haversine_km
    evs = Event.query.filter(Event.is_permanent.is_(True), Event.lat.isnot(None)).all()
    for e in evs:                       # vorige dedup resetten (idempotent)
        e.hidden, e.dupe_of = False, None
    db.session.flush()

    buckets = defaultdict(list)
    for e in evs:
        buckets[(round(e.lat, 2), round(e.lng, 2))].append(e)
    tokens = {e.id: _naam_tokens(e.title) for e in evs}
    prio = {"uit": 4, "tv": 3, "wd": 2, "osm": 1}

    def rijkdom(e):
        return (1 if e.image_url else 0, prio.get(e.source, 0), 1 if e.source_url else 0)

    verwerkt, n_dupe = set(), 0
    for e in evs:
        if e.id in verwerkt:
            continue
        groep = [e]
        blat, blng = round(e.lat, 2), round(e.lng, 2)
        for dlat in (-0.01, 0.0, 0.01):
            for dlng in (-0.01, 0.0, 0.01):
                for f in buckets.get((round(blat + dlat, 2), round(blng + dlng, 2)), []):
                    if f.id == e.id or f.id in verwerkt:
                        continue
                    if haversine_km(e.lat, e.lng, f.lat, f.lng) * 1000 > 150:
                        continue
                    ta, tb = tokens[e.id], tokens[f.id]
                    if not ta or not tb:
                        continue
                    jac = len(ta & tb) / len(ta | tb)
                    if jac >= 0.5:                 # sterke naamgelijkenis
                        groep.append(f)
        for g in groep:
            verwerkt.add(g.id)
        if len(groep) > 1:
            canon = max(groep, key=rijkdom)
            for g in groep:
                if g.id == canon.id:
                    continue
                g.hidden, g.dupe_of = True, canon.id
                n_dupe += 1
                if not canon.image_url and g.image_url:
                    canon.image_url = g.image_url
                if not canon.source_url and g.source_url:
                    canon.source_url = g.source_url
                if not canon.description and g.description:
                    canon.description = g.description
                if not canon.adres and g.adres:
                    canon.adres = g.adres
                if not canon.gemeente and g.gemeente:
                    canon.gemeente = g.gemeente
    db.session.commit()
    return n_dupe

def purge_source(naam):
    """Verwijder ALLE events van één bron + verweesde locaties/reeksen/zwaartepunten.
    Retourneert het aantal verwijderde events."""
    from ...models import (Event, Venue, Organizer, EditionSeries,
                           SavedEvent, Review, Share, Interaction, PostcodeCentroid)
    from ..uit_sync import update_centroids
    ids = [e.id for e in Event.query.filter_by(source=naam).with_entities(Event.id)]
    n = len(ids)
    if n == 0:
        _set_status(naam, "idle", result="leeg")
        return 0
    for model in (SavedEvent, Review, Share, Interaction):
        model.query.filter(model.event_id.in_(ids)).delete(synchronize_session=False)
    Event.query.filter_by(source=naam).delete(synchronize_session=False)
    db.session.commit()
    for s in EditionSeries.query.filter(~EditionSeries.events.any()).all():
        db.session.delete(s)
    used_venues = {v for (v,) in db.session.query(Event.venue_id).distinct() if v}
    used_orgs = {o for (o,) in db.session.query(Event.organizer_id).distinct() if o}
    Venue.query.filter(~Venue.id.in_(used_venues or {-1})).delete(synchronize_session=False)
    Organizer.query.filter(~Organizer.id.in_(used_orgs or {-1})).delete(synchronize_session=False)
    db.session.commit()
    gebruikte_pc = {p for (p,) in db.session.query(Event.postcode).distinct() if p}
    PostcodeCentroid.query.filter(
        ~PostcodeCentroid.postcode.in_(gebruikte_pc or {"__none__"})
    ).delete(synchronize_session=False)
    db.session.commit()
    update_centroids()
    db.session.commit()
    _set_status(naam, "idle", result=f"verwijderd ({n})")
    return n
