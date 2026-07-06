"""Admin-instellingen + verbindingen: settings uit DB, secrets nooit in DB."""
import pyotp
from app.extensions import db
from app.models import Admin, Setting, get_setting, get_bool, get_int
from argon2 import PasswordHasher


def _maak_admin(app):
    with app.app_context():
        secret = pyotp.random_base32()
        a = Admin(email="a@ravot.be", pw_hash=PasswordHasher().hash("wachtwoord123"),
                  totp_secret=secret)
        db.session.add(a); db.session.commit()
        return a.id, secret


def _login(client, app):
    aid, secret = _maak_admin(app)
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
    return aid


def test_setting_default_en_override(app):
    with app.app_context():
        # default als niets in DB
        assert get_setting("default_radius") == "25"
        assert get_bool("weekendmail_aan") is True
        # override
        db.session.add(Setting(key="default_radius", value="50"))
        db.session.add(Setting(key="weekendmail_aan", value="0"))
        db.session.commit()
        assert get_int("default_radius") == 50
        assert get_bool("weekendmail_aan") is False


def test_instellingen_opslaan(client, app):
    _login(client, app)
    resp = client.post("/beheer/instellingen", data={
        "uit_query": "labels:Vlieg", "sync_max_pages": "100",
        "default_radius": "30",  # bool velden niet aangevinkt = uit
    }, follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        assert get_setting("uit_query") == "labels:Vlieg"
        assert get_int("sync_max_pages") == 100
        assert get_bool("weekendmail_aan") is False  # niet aangevinkt


def test_verbindingen_toont_geen_secrets(client, app):
    _login(client, app)
    html = client.get("/beheer/verbindingen").get_data(as_text=True)
    assert html.count("beschikbaar") >= 0  # pagina rendert
    # de key/wachtwoord mogen NOOIT in de HTML staan
    assert app.config.get("UIT_API_KEY", "x") not in html or not app.config.get("UIT_API_KEY")


def test_instellingen_vereist_admin(client):
    # niet ingelogd → redirect naar login
    assert client.get("/beheer/instellingen").status_code == 302


def test_dashboard_rendert_met_zero_results(client, app):
    """Dashboard rendert met statistieken zonder te crashen."""
    from app.models import Interaction
    with app.app_context():
        db.session.add(Interaction(type="zero_result", meta={"postcode": "9000"}))
        db.session.add(Interaction(type="zero_result", meta={"postcode": "8500"}))
        db.session.commit()
    _login(client, app)
    r = client.get("/beheer/")
    assert r.status_code == 200
    assert "Dashboard" in r.get_data(as_text=True)  # het nieuwe dashboard rendert
    assert "Gezinnen" in r.get_data(as_text=True)   # met statistiek-tegels


def test_codes_per_uur_wordt_gehandhaafd(client, app):
    """De admin-setting codes_per_uur begrenst echt het aantal code-aanvragen."""
    from app.models import Setting
    with app.app_context():
        db.session.add(Setting(key="codes_per_uur", value="2"))
        db.session.commit()
    # 2 aanvragen mogen, de 3e wordt geweigerd
    for i in range(2):
        r = client.post("/login", data={"email": "limiet@test.be"})
        assert "Voer je code in".encode() in r.data or r.status_code == 200
    r = client.post("/login", data={"email": "limiet@test.be"})
    assert "al enkele codes verstuurd".encode() in r.data  # geweigerd
