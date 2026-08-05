"""Patch 182: bijdragen zonder account — lagere drempel, lager stemgewicht."""
from app.extensions import db
from app.models import Event, Family, RavotPunt, Setting, VeldStem


def _plek(app):
    with app.app_context():
        e = Event(title="Speeltuin An", slug="an1", source="osm", ext_id="an1",
                  is_permanent=True, pending=False, hidden=False, lat=50.9,
                  lng=3.1, subtype="playground")
        db.session.add(e)
        db.session.add(Setting(key="anoniem_stemmen_aan", value="1"))
        db.session.commit()
        from app.models import wis_settings_cache
        wis_settings_cache()
        return e.id


def test_anoniem_stemmen_werkt_zonder_punten(client, app):
    eid = _plek(app)
    r = client.post(f"/bevestig/{eid}/toilet/ja", follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        stem = VeldStem.query.filter_by(event_id=eid, veld="toilet").first()
        assert stem is not None
        assert stem.stemmer.startswith("anon:")
        assert stem.gewicht == 0.5              # half gewicht
        assert RavotPunt.query.count() == 0     # geen punten zonder account


def test_anonieme_stem_is_intrekbaar_en_uniek(client, app):
    eid = _plek(app)
    client.post(f"/bevestig/{eid}/toilet/ja", follow_redirects=True)
    client.post(f"/bevestig/{eid}/toilet/ja", follow_redirects=True)   # toggle
    with app.app_context():
        assert VeldStem.query.filter_by(event_id=eid, veld="toilet").count() == 0
    client.post(f"/bevestig/{eid}/toilet/ja", follow_redirects=True)
    client.post(f"/bevestig/{eid}/toilet/nee", follow_redirects=True)  # wijzigen
    with app.app_context():
        rijen = VeldStem.query.filter_by(event_id=eid, veld="toilet").all()
        assert len(rijen) == 1 and rijen[0].waarde is False


def test_ongeldige_invoer_geweigerd(client, app):
    eid = _plek(app)
    assert client.post(f"/bevestig/{eid}/onzinveld/ja").status_code == 400
    assert client.post(f"/bevestig/{eid}/toilet/misschien").status_code == 400


def test_uitschakelbaar(client, app):
    """Aparte test: de settings-cache wordt per app-context gevuld, dus de
    schakelaar moet gezet zijn vóór het eerste verzoek."""
    with app.app_context():
        e = Event(title="Uit", slug="uit1", source="osm", ext_id="uit1",
                  is_permanent=True, pending=False, hidden=False, lat=50.9,
                  lng=3.1, subtype="playground")
        db.session.add(e)
        db.session.add(Setting(key="anoniem_stemmen_aan", value="0"))
        db.session.commit()
        eid = e.id
    assert client.post(f"/bevestig/{eid}/toilet/ja").status_code == 404
    h = client.get("/e/uit1").get_data(as_text=True)
    assert "/bevestig/" not in h        # knoppen ook weg


def test_knoppen_zichtbaar_zonder_account(client, app):
    eid = _plek(app)
    h = client.get("/e/an1").get_data(as_text=True)
    assert "/bevestig/" in h
    assert "geen account nodig" in h
