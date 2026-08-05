"""Patch 182: voorzieningen bevestigen zonder account.

De accountdrempel is voor veel bezoekers te hoog terwijl ze wél willen
bijdragen (melden kan al anoniem). Zo'n stem weegt half zo zwaar en levert
geen punten op.
"""
from app.extensions import db
from app.models import Event, RavotPunt, Setting, VeldStem


def _plek(app):
    with app.app_context():
        ev = Event(title="Speeltuin", slug="anonp", source="osm", ext_id="anonp",
                   is_permanent=True, pending=False, hidden=False, lat=50.9,
                   lng=3.1, subtype="playground", gemeente="Roeselare",
                   postcode="8800")
        db.session.add(ev)
        db.session.commit()
        return ev.id


def test_anoniem_bevestigen_werkt_zonder_punten(client, app):
    eid = _plek(app)
    assert "/bevestig/" in client.get("/e/anonp").get_data(as_text=True)
    r = client.post(f"/bevestig/{eid}/toilet/ja", follow_redirects=True)
    h = r.get_data(as_text=True)
    assert "genoteerd" in h and "ravotpunten" in h    # uitnodiging tot profiel
    with app.app_context():
        stem = VeldStem.query.one()
        assert stem.stemmer.startswith("anon:")
        assert stem.gewicht < 1.0                     # weegt minder dan een gezin
        assert RavotPunt.query.count() == 0            # nooit punten
        assert db.session.get(Event, eid).toilet is True


def test_anoniem_intrekken_en_validatie(client, app):
    eid = _plek(app)
    client.post(f"/bevestig/{eid}/toilet/ja", follow_redirects=True)
    client.post(f"/bevestig/{eid}/toilet/ja", follow_redirects=True)
    with app.app_context():
        assert VeldStem.query.count() == 0            # toggle = intrekken
    assert client.post(f"/bevestig/{eid}/hackveld/ja").status_code == 400
    assert client.post(f"/bevestig/{eid}/kindermenu/ja").status_code == 400


def test_anoniem_stemmen_uitschakelbaar(client, app):
    eid = _plek(app)
    with app.app_context():
        db.session.add(Setting(key="anoniem_stemmen_aan", value="0"))
        db.session.commit()
    assert client.post(f"/bevestig/{eid}/toilet/ja").status_code == 404
    assert "/bevestig/" not in client.get("/e/anonp").get_data(as_text=True)
