"""Patch 93: Overpass-statuscheck oordeelt op sync-ouderdom, niet op piekdrukte.

Rood is voorbehouden voor situaties die actie vragen: servers plat ÉN een
verouderde OSM-sync. Een traag piekmoment met een recente sync is een zachte
melding (None = grijs), en een uitgeschakelde bron is n.v.t.
"""
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import Setting, SyncStatus
from app.services import health


class _Timeout(Exception):
    pass


@pytest.fixture()
def servers_plat(monkeypatch):
    def kapot(*a, **kw):
        raise _Timeout("ReadTimeout")
    monkeypatch.setattr(health.requests, "get", kapot)


def _zet(key, value):
    db.session.add(Setting(key=key, value=value))
    db.session.commit()


def test_bron_uit_geeft_nvt(app, servers_plat):
    with app.app_context():
        _zet("bron_osm_aan", "0")
        ok, detail = health._overpass()
    assert ok is None
    assert "uit" in detail


def test_recente_sync_geeft_zachte_melding(app, servers_plat):
    with app.app_context():
        _zet("bron_osm_aan", "1")
        db.session.add(SyncStatus(source="osm", state="done",
                                  last_run=datetime.utcnow() - timedelta(days=3)))
        db.session.commit()
        ok, detail = health._overpass()
    assert ok is None
    assert "geen actie nodig" in detail


def test_verouderde_sync_geeft_rood(app, servers_plat):
    with app.app_context():
        _zet("bron_osm_aan", "1")
        db.session.add(SyncStatus(source="osm", state="done",
                                  last_run=datetime.utcnow() - timedelta(days=30)))
        db.session.commit()
        ok, detail = health._overpass()
    assert ok is False
    assert "cron" in detail


def test_geen_sync_geregistreerd_geeft_rood(app, servers_plat):
    with app.app_context():
        _zet("bron_osm_aan", "1")
        ok, detail = health._overpass()
    assert ok is False


def test_bereikbare_server_blijft_groen(app, monkeypatch):
    class _R:
        status_code = 200
    with app.app_context():
        _zet("bron_osm_aan", "1")
        monkeypatch.setattr(health.requests, "get", lambda *a, **kw: _R())
        ok, detail = health._overpass()
    assert ok is True
    assert "antwoordt 200" in detail
