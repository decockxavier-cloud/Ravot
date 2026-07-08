"""Gebruikersbijdragen: plek toevoegen (moderatiewachtrij) + melden."""
import re
from app.extensions import db
from app.models import Event, Family, Report


def _login(client, app):
    with app.app_context():
        fam = Family(email="ouder@test.be", postcode="9000")
        db.session.add(fam); db.session.commit()
        fid = fam.id
    with client.session_transaction() as s:
        s["family_id"] = fid
    return fid


def test_toevoegen_gaat_naar_wachtrij(client, app):
    _login(client, app)
    r = client.post("/mijn/toevoegen", data={
        "titel": "Speelbos De Warande", "categorie": "buiten",
        "postcode": "9000", "gemeente": "Gent", "age_min": "2", "age_max": "10",
        "gratis": "on", "csrf_token": "x"}, follow_redirects=False)
    assert r.status_code in (302, 303)
    with app.app_context():
        ev = Event.query.filter_by(source="user").first()
        assert ev is not None
        assert ev.pending is True and ev.hidden is False    # wacht op review
        assert ev.is_free is True and ev.lat is not None     # postcode -> coord


def test_ingediende_plek_niet_publiek_zichtbaar(client, app):
    _login(client, app)
    with app.app_context():
        db.session.add(Event(source="user", pending=True, is_permanent=True,
            slug="pending-plek", title="Nog Niet Zichtbaar", gemeente="Gent",
            postcode="9000", lat=51.05, lng=3.72, age_min=0, age_max=12,
            categories=["buiten"]))
        db.session.commit()
    assert "Nog Niet Zichtbaar" not in client.get("/ontdek").get_data(as_text=True)


def test_melden_maakt_report(client, app):
    _login(client, app)
    with app.app_context():
        db.session.add(Event(source="osm", ext_id="node/7", slug="te-melden",
            title="Gesloten Plek", is_permanent=True, gemeente="Gent", postcode="9000",
            lat=51.05, lng=3.72, age_min=0, age_max=12, categories=["buiten"]))
        db.session.commit()
        eid = Event.query.filter_by(slug="te-melden").first().id
    client.post(f"/mijn/melden/{eid}", data={"reason": "gesloten",
                "note": "al jaren dicht", "csrf_token": "x"})
    with app.app_context():
        rep = Report.query.first()
        assert rep is not None and rep.reason == "gesloten" and rep.handled is False


def test_pending_zonder_indiener_nooit_publiek(client, app):
    """SECURITY-regressie: pending plek zonder submitted_by mag voor niemand
    zichtbaar zijn (None mag nooit matchen met een anonieme sessie)."""
    with app.app_context():
        db.session.add(Event(source="user", pending=True, is_permanent=True,
            slug="wees-pending", title="Wees Pending", gemeente="Gent",
            postcode="9000", lat=51.0, lng=3.7, age_min=0, age_max=12,
            categories=["buiten"]))
        db.session.commit()
    assert client.get("/e/wees-pending").status_code == 404
