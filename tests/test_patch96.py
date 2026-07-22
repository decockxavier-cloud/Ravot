"""Patch 96: vosje-logo, echte app-iconen en een omkeerbaar niet-voor-ons-overzicht."""
import os
import re
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import Child, Event, Family, Interaction


@pytest.fixture()
def gezin(app, client):
    with app.app_context():
        fam = Family(email="f96@t.be", postcode="8800")
        db.session.add(fam)
        db.session.flush()
        db.session.add(Child(family_id=fam.id, birth_year=2019))
        nu = datetime.utcnow()
        ev = Event(uit_id="96a", slug="ev-96", title="Poppentheater",
                   start=nu + timedelta(days=1), end=nu + timedelta(days=1, hours=2),
                   gemeente="Roeselare", postcode="8800")
        db.session.add(ev)
        db.session.commit()
        ids = (fam.id, ev.id)
    with client.session_transaction() as s:
        s["family_id"] = ids[0]
    return client, ids


def test_logo_zonder_punt_met_vosje(client):
    html = client.get("/vandaag").data.decode()
    assert "logo-dot" not in html
    assert "img/vosje.svg" in html
    kop = re.search(r'<a class="logo".*?</a>', html, re.S).group(0)
    assert ">Ravot" in kop and "Ravot." not in kop


def test_iconen_bestaan_en_manifest_verwijst(client):
    for f in ("vosje.svg", "icon.svg", "icon-192.png", "icon-512.png",
              "apple-touch-icon.png"):
        assert os.path.exists(f"app/static/img/{f}"), f
    m = client.get("/manifest.webmanifest").get_json()
    srcs = {i["src"] for i in m["icons"]}
    assert "/static/img/icon-192.png" in srcs
    assert "/static/img/icon-512.png" in srcs
    html = client.get("/vandaag").data.decode()
    assert "apple-touch-icon" in html


def test_dubbele_ravotpas_tegel_weg(gezin):
    client, _ = gezin
    html = client.get("/mijn/profiel", follow_redirects=True).data.decode()
    # De pas-kop bovenaan blijft; de losse 🦊-tegel eronder is weg.
    assert 'class="card pas-kop"' in html
    assert html.count("ravotpas") <= html.count("pas-kop") + 1  # geen extra tegel
    assert "dash-emoji\">🦊" not in html


def test_niet_voor_ons_overzicht_met_terugzet(gezin, app):
    client, (fid, eid) = gezin
    client.post(f"/mijn/feedback/{eid}/dismiss", follow_redirects=True)
    html = client.get("/mijn/profiel", follow_redirects=True).data.decode()
    assert "Niet voor ons 🚫" in html
    assert "Poppentheater" in html
    assert "Zet terug" in html
    # Terugzetten via het overzicht = dezelfde toggle
    client.post(f"/mijn/feedback/{eid}/dismiss", follow_redirects=True)
    with app.app_context():
        assert Interaction.query.filter_by(family_id=fid, event_id=eid,
                                           type="dismiss").count() == 0
    html = client.get("/mijn/profiel", follow_redirects=True).data.decode()
    assert "Niet voor ons 🚫" not in html
