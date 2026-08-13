"""Patch 225: inloggen met Google naast de e-mailcode.

De veiligheidskern: alleen een door Google geverifieerd adres wordt aanvaard
(e-mail is bij ons de identiteit), en `state` beschermt de terugkeer-URL.
"""
from unittest.mock import patch

from app.extensions import db
from app.models import Family


def test_knop_alleen_als_ingesteld(client, app):
    h = client.get("/login").get_data(as_text=True)
    assert "Verder met Google" not in h          # geen sleutels: geen knop
    app.config["GOOGLE_CLIENT_ID"] = "test-id"
    app.config["GOOGLE_CLIENT_SECRET"] = "test-secret"
    h = client.get("/login").get_data(as_text=True)
    assert "Verder met Google" in h


def test_doorstuur_bevat_state(client, app):
    app.config["GOOGLE_CLIENT_ID"] = "test-id"
    app.config["GOOGLE_CLIENT_SECRET"] = "test-secret"
    r = client.get("/login/google")
    assert r.status_code == 302
    assert "accounts.google.com" in r.headers["Location"]
    assert "state=" in r.headers["Location"]


def test_verkeerde_state_wordt_geweigerd(client, app):
    app.config["GOOGLE_CLIENT_ID"] = "test-id"
    app.config["GOOGLE_CLIENT_SECRET"] = "test-secret"
    client.get("/login/google")
    r = client.get("/login/google/terug?code=x&state=vervalst",
                   follow_redirects=True)
    assert "onderbroken" in r.get_data(as_text=True)


def test_bestaand_gezin_logt_in_nieuw_gaat_naar_onboarding(client, app):
    app.config["GOOGLE_CLIENT_ID"] = "test-id"
    app.config["GOOGLE_CLIENT_SECRET"] = "test-secret"
    with app.app_context():
        db.session.add(Family(email="bekend@test.be", postcode="8800"))
        db.session.commit()
        fid = Family.query.filter_by(email="bekend@test.be").first().id
    client.get("/login/google")
    with client.session_transaction() as s:
        state = s["google_state"]
    with patch("app.services.google_login.email_uit_code",
               return_value="bekend@test.be"):
        client.get(f"/login/google/terug?code=abc&state={state}")
    with client.session_transaction() as s:
        assert s.get("family_id") == fid

    client.get("/login/google")
    with client.session_transaction() as s:
        state = s["google_state"]
    with patch("app.services.google_login.email_uit_code",
               return_value="nieuw@test.be"):
        r = client.get(f"/login/google/terug?code=abc&state={state}")
    assert "/mijn/start" in r.headers.get("Location", "")


def test_niet_geverifieerd_adres_wordt_geweigerd(app):
    """De kern: zonder Google's e-mailverificatie geen bewijs van
    eigenaarschap — anders kan iemand andermans account overnemen."""
    app.config["GOOGLE_CLIENT_ID"] = "test-id"
    app.config["GOOGLE_CLIENT_SECRET"] = "test-secret"

    class Antw:
        def __init__(self, data):
            self._d = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._d

    with app.test_request_context():
        from app.services import google_login
        with patch.object(google_login.requests, "post",
                          return_value=Antw({"access_token": "t"})), \
             patch.object(google_login.requests, "get",
                          return_value=Antw({"email": "x@test.be",
                                             "email_verified": False})):
            assert google_login.email_uit_code("c", "https://r") is None
        with patch.object(google_login.requests, "post",
                          return_value=Antw({"access_token": "t"})), \
             patch.object(google_login.requests, "get",
                          return_value=Antw({"email": "x@test.be",
                                             "email_verified": True})):
            assert google_login.email_uit_code("c", "https://r") == "x@test.be"


def test_google_storing_hindert_niet(app):
    app.config["GOOGLE_CLIENT_ID"] = "test-id"
    app.config["GOOGLE_CLIENT_SECRET"] = "test-secret"
    with app.test_request_context():
        from app.services import google_login
        with patch.object(google_login.requests, "post",
                          side_effect=RuntimeError("google plat")):
            assert google_login.email_uit_code("c", "https://r") is None
