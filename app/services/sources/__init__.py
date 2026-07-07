"""Bronnen-dispatcher.

Eén plek die weet welke bronnen bestaan, welke aan/uit staan en hoe je ze draait.
UiT houdt zijn eigen gespecialiseerde sync (reeks-matching, centroids); de andere
bronnen delen de generieke upsert uit `base.run_source`.
"""
from flask import current_app

from ...extensions import db
from ...models import get_bool
from . import ticketmaster, toerisme, osm
from .base import run_source

# naam -> (setting-key, label, adaptermodule)
REGISTRY = {
    "uit": ("bron_uit_aan", "UiTdatabank (publiq)", None),   # eigen sync-pad
    "tm": ("bron_tm_aan", "Ticketmaster (Family)", ticketmaster),
    "tv": ("bron_tv_aan", "Toerisme Vlaanderen", toerisme),
    "osm": ("bron_osm_aan", "OpenStreetMap", osm),
}


def source_enabled(name):
    key = REGISTRY.get(name, (None,))[0]
    return bool(key) and get_bool(key)


def sync_one(name):
    """Draai één bron. Retourneert een resultaat-dict."""
    if name == "uit":
        from ..uit_sync import run_sync
        n = run_sync()
        return {"bron": "uit", "verwerkt": n, "verworpen": 0}
    adapter = REGISTRY.get(name, (None, None, None))[2]
    if adapter is None:
        return {"bron": name, "verwerkt": 0, "verworpen": 0, "fout": "onbekende bron"}
    processed, rejected = run_source(adapter)
    return {"bron": name, "verwerkt": processed, "verworpen": rejected}


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
    return results
