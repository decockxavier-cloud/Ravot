"""QR-enrollment: 2FA blijft verplicht, maar wordt na login ingesteld via QR."""
import pyotp
from argon2 import PasswordHasher
from app.extensions import db
from app.models import Admin


def _maak_admin(app, confirmed=False):
    with app.app_context():
        a = Admin(email="x@ravot.be", pw_hash=PasswordHasher().hash("Wachtwoord123"),
                  totp_secret=pyotp.random_base32(), totp_confirmed=confirmed)
        db.session.add(a); db.session.commit()
        return a.id, a.totp_secret


def test_nieuwe_admin_gaat_naar_enrollment(client, app):
    _maak_admin(app, confirmed=False)
    r = client.post("/beheer/login", data={"email": "x@ravot.be", "password": "Wachtwoord123"})
    assert r.status_code == 302
    assert "/2fa-instellen" in r.headers["Location"]


def test_enrollment_toont_qr(client, app):
    aid, _ = _maak_admin(app, confirmed=False)
    with client.session_transaction() as s:
        s["admin_id"] = aid; s["admin_2fa_ok"] = False
    html = client.get("/beheer/2fa-instellen").get_data(as_text=True)
    assert "data:image/png;base64," in html  # QR als PNG gerenderd
    assert "tijdgebaseerd" in html.lower()


def test_enrollment_bevestigt_en_geeft_toegang(client, app):
    aid, secret = _maak_admin(app, confirmed=False)
    with client.session_transaction() as s:
        s["admin_id"] = aid; s["admin_2fa_ok"] = False
    code = pyotp.TOTP(secret).now()
    r = client.post("/beheer/2fa-instellen", data={"code": code}, follow_redirects=False)
    assert r.status_code == 302 and "/beheer" in r.headers["Location"]
    with app.app_context():
        assert db.session.get(Admin, aid).totp_confirmed is True


def test_bevestigde_admin_gaat_naar_otp_niet_enrollment(client, app):
    _maak_admin(app, confirmed=True)
    r = client.post("/beheer/login", data={"email": "x@ravot.be", "password": "Wachtwoord123"})
    assert r.status_code == 302
    assert "/otp" in r.headers["Location"]


def test_otp_weigert_onbevestigde_admin(client, app):
    aid, _ = _maak_admin(app, confirmed=False)
    with client.session_transaction() as s:
        s["admin_id"] = aid; s["admin_2fa_ok"] = False
    # otp mag niet werken zolang niet ingeschreven → redirect naar enrollment
    r = client.get("/beheer/otp")
    assert r.status_code == 302 and "/2fa-instellen" in r.headers["Location"]


def test_verkeerde_code_geen_toegang(client, app):
    aid, _ = _maak_admin(app, confirmed=False)
    with client.session_transaction() as s:
        s["admin_id"] = aid; s["admin_2fa_ok"] = False
    r = client.post("/beheer/2fa-instellen", data={"code": "000000"}, follow_redirects=False)
    assert r.status_code == 200  # blijft op de pagina
    with app.app_context():
        assert db.session.get(Admin, aid).totp_confirmed is False


def test_verweesde_sessie_crasht_niet(client, app):
    """Sessie met niet-bestaand admin_id → netjes naar login, geen crash."""
    with client.session_transaction() as s:
        s["admin_id"] = 999999  # bestaat niet
        s["admin_2fa_ok"] = False
    # geen van deze mag een 500 geven
    for pad in ["/beheer/otp", "/beheer/2fa-instellen", "/beheer/"]:
        r = client.get(pad)
        assert r.status_code in (302, 303), f"{pad} gaf {r.status_code}"
        assert "/login" in r.headers.get("Location", "") or "/beheer" in r.headers.get("Location", "")


def test_login_get_ruimt_halve_sessie_op(client, app):
    """Login-pagina openen met halve sessie → sessie wordt schoongemaakt."""
    with client.session_transaction() as s:
        s["admin_id"] = 999999
        s["admin_2fa_ok"] = False
    client.get("/beheer/login")
    with client.session_transaction() as s:
        assert "admin_id" not in s  # opgeruimd
