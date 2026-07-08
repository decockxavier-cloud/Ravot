"""Uitbatersportaal: login via e-mailcode, claims, fichewijzigingen, moderatie."""
import pyotp
from argon2 import PasswordHasher
from app.extensions import db
from app.models import (Admin, Event, Operator, OperatorClaim, EditProposal)
from app.services import magic


def _plek(app, slug="mijn-zaak", titel="Pannenkoekenhuis De Smulhoek"):
    with app.app_context():
        ev = Event(source="osm", ext_id=f"node/{slug}", slug=slug, title=titel,
                   is_permanent=True, gemeente="Gent", postcode="9000",
                   lat=51.0, lng=3.7, age_min=0, age_max=12, categories=["binnen"],
                   source_url="https://smulhoek.be")
        db.session.add(ev); db.session.commit()
        return ev.id


def _login_operator(client, app, email="info@smulhoek.be"):
    with app.app_context():
        code = magic.issue_code(email, purpose="operator")
    client.post("/uitbater/code", data={"email": email, "code": code, "csrf_token": "x"})
    with app.app_context():
        return Operator.query.filter_by(email=email).first().id


def _login_reviewer(client, app):
    with app.app_context():
        a = Admin(email=f"rev{Admin.query.count()}@ravot.be",
                  pw_hash=PasswordHasher().hash("x"),
                  totp_secret=pyotp.random_base32(), totp_confirmed=True, role="reviewer")
        db.session.add(a); db.session.commit(); aid = a.id
    with client.session_transaction() as s:
        s["admin_id"] = aid; s["admin_2fa_ok"] = True


def test_login_via_emailcode_maakt_operator(client, app):
    oid = _login_operator(client, app)
    assert oid is not None
    assert client.get("/uitbater/overzicht").status_code == 200


def test_gezinscode_werkt_niet_voor_uitbaters(client, app):
    """SECURITY: een gezins-inlogcode (purpose=login) mag geen uitbaterssessie geven."""
    with app.app_context():
        code = magic.issue_code("info@smulhoek.be", purpose="login")   # gezinscode
    client.post("/uitbater/code", data={"email": "info@smulhoek.be",
                "code": code, "csrf_token": "x"})
    with client.session_transaction() as s:
        assert "operator_id" not in s


def test_claim_gaat_naar_wachtrij(client, app):
    eid = _plek(app)
    _login_operator(client, app)
    r = client.post("/uitbater/claim", data={"event_id": eid,
                    "note": "e-mail matcht onze site", "csrf_token": "x"})
    assert r.status_code in (302, 303)
    with app.app_context():
        c = OperatorClaim.query.first()
        assert c is not None and c.status == "pending"


def test_fiche_bewerken_vereist_goedgekeurde_claim(client, app):
    """SECURITY: zonder goedgekeurde claim -> 403 op de fiche."""
    eid = _plek(app)
    _login_operator(client, app)
    assert client.get(f"/uitbater/fiche/{eid}").status_code == 403
    # claim pending -> nog steeds 403
    client.post("/uitbater/claim", data={"event_id": eid, "csrf_token": "x"})
    assert client.get(f"/uitbater/fiche/{eid}").status_code == 403


def test_volledige_flow_claim_edit_toepassen(client, app):
    """Uitbater claimt -> reviewer keurt claim -> uitbater dient wijziging in ->
    reviewer past toe -> fiche is aangepast."""
    eid = _plek(app)
    oid = _login_operator(client, app)
    client.post("/uitbater/claim", data={"event_id": eid, "csrf_token": "x"})
    with app.app_context():
        cid = OperatorClaim.query.first().id
    # reviewer keurt de claim goed (aparte client-sessie zou netter zijn; we
    # wisselen de sessie om)
    _login_reviewer(client, app)
    client.post(f"/beheer/nazicht/claim/{cid}/goedkeuren", data={"csrf_token": "x"})
    with app.app_context():
        assert OperatorClaim.query.first().status == "approved"
    # terug als uitbater: wijziging indienen
    with client.session_transaction() as s:
        s.clear(); s["operator_id"] = oid
    assert client.get(f"/uitbater/fiche/{eid}").status_code == 200
    client.post(f"/uitbater/fiche/{eid}", data={
        "description": "Pannenkoeken met speelhoek voor de kleinsten.",
        "adres": "Markt 12", "postcode": "9000", "gemeente": "Gent",
        "source_url": "https://smulhoek.be", "indoor": "on", "csrf_token": "x"})
    with app.app_context():
        v = EditProposal.query.first()
        assert v is not None and v.status == "pending"
        assert v.changes["adres"] == "Markt 12" and v.changes["indoor"] is True
        # fiche is NOG NIET aangepast (wacht op review)
        assert db.session.get(Event, eid).adres != "Markt 12"
        pid = v.id
    # reviewer past toe
    _login_reviewer(client, app)
    client.post(f"/beheer/nazicht/wijziging/{pid}/goedkeuren", data={"csrf_token": "x"})
    with app.app_context():
        ev = db.session.get(Event, eid)
        assert ev.adres == "Markt 12" and ev.indoor is True
        assert ev.description.startswith("Pannenkoeken")
        assert EditProposal.query.first().status == "approved"


def test_uitbater_kan_andermans_fiche_niet_bewerken(client, app):
    """SECURITY: goedgekeurde claim op plek A geeft geen toegang tot plek B."""
    eid_a = _plek(app, slug="zaak-a", titel="Zaak A")
    eid_b = _plek(app, slug="zaak-b", titel="Zaak B")
    oid = _login_operator(client, app)
    with app.app_context():
        db.session.add(OperatorClaim(operator_id=oid, event_id=eid_a, status="approved"))
        db.session.commit()
    assert client.get(f"/uitbater/fiche/{eid_a}").status_code == 200
    assert client.get(f"/uitbater/fiche/{eid_b}").status_code == 403


def test_anoniem_geweigerd(client, app):
    eid = _plek(app)
    assert client.get("/uitbater/overzicht").status_code == 302   # -> login
    assert client.get(f"/uitbater/fiche/{eid}").status_code == 302
