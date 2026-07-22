"""Patch 90: sitemap-hygiëne, verborgen dubbels en /health-endpoint."""
from datetime import datetime, timedelta

from app.extensions import db
from app.models import Event, Setting


def _event(**kw):
    nu = datetime.utcnow()
    basis = dict(start=nu + timedelta(hours=4), end=nu + timedelta(hours=6),
                 gemeente="Roeselare", postcode="8800")
    basis.update(kw)
    ev = Event(**basis)
    db.session.add(ev)
    db.session.commit()
    return ev


def test_sitemap_weert_pending_en_hidden(client, app):
    with app.app_context():
        _event(uit_id="s1", slug="zichtbaar-ev", title="Zichtbaar")
        _event(uit_id="s2", slug="pending-ev", title="Pending", pending=True)
        _event(uit_id="s3", slug="hidden-ev", title="Hidden", hidden=True)
    xml = client.get("/sitemap.xml").data
    assert b"/e/zichtbaar-ev" in xml
    assert b"/e/pending-ev" not in xml
    assert b"/e/hidden-ev" not in xml


def test_sitemap_bevat_permanente_plekken(client, app):
    with app.app_context():
        _event(uit_id="s4", slug="perma-speeltuin", title="Speeltuin",
               is_permanent=True, start=None, end=None)
    xml = client.get("/sitemap.xml").data
    assert b"/e/perma-speeltuin" in xml


def test_hidden_dubbel_301_naar_canoniek(client, app):
    with app.app_context():
        canon = _event(uit_id="s5", slug="canoniek-ev", title="Canoniek")
        _event(uit_id="s6", slug="dubbel-ev", title="Dubbel",
               hidden=True, dupe_of=canon.id)
    r = client.get("/e/dubbel-ev")
    assert r.status_code == 301
    assert r.headers["Location"].endswith("/e/canoniek-ev")


def test_hidden_zonder_canoniek_404(client, app):
    with app.app_context():
        _event(uit_id="s7", slug="weggemoffeld-ev", title="Weg", hidden=True)
    assert client.get("/e/weggemoffeld-ev").status_code == 404


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"


def test_health_blijft_werken_in_onderhoudsmodus(client, app):
    with app.app_context():
        db.session.add(Setting(key="onderhoud_aan", value="1"))
        db.session.commit()
    assert client.get("/vandaag").status_code == 503
    assert client.get("/health").status_code == 200
