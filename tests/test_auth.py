"""Inlogcode (6 cijfers): eenmalig, 15 min, per-gebruiker gehasht, brute-force-slot.
Uitschrijven raakt het account niet."""
from datetime import datetime, timedelta

from app.extensions import db
from app.models import Family, MagicToken
from app.services import magic
from app.services.weekendmail import unsubscribe_token


def test_code_is_zes_cijfers(app):
    with app.app_context():
        code = magic.issue_code("x@test.be")
        assert len(code) == 6 and code.isdigit()


def test_code_eenmalig(app):
    with app.app_context():
        code = magic.issue_code("x@test.be")
        assert magic.verify_code("x@test.be", code) == "x@test.be"
        # tweede keer: verbrand
        assert magic.verify_code("x@test.be", code) is None


def test_verlopen_code_werkt_niet(app):
    with app.app_context():
        code = magic.issue_code("x@test.be")
        row = MagicToken.query.filter_by(email="x@test.be", used_at=None).first()
        row.expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()
        assert magic.verify_code("x@test.be", code) is None


def test_verkeerde_code_faalt(app):
    with app.app_context():
        magic.issue_code("x@test.be")
        assert magic.verify_code("x@test.be", "000000") is None


def test_brute_force_slot(app):
    """Na MAX_CODE_ATTEMPTS foute pogingen is de code dood, ook al raad je hem daarna."""
    with app.app_context():
        code = magic.issue_code("brute@test.be")
        for _ in range(magic.MAX_CODE_ATTEMPTS):
            assert magic.verify_code("brute@test.be", "999999") is None
        # zelfs de JUISTE code werkt nu niet meer
        assert magic.verify_code("brute@test.be", code) is None


def test_code_is_per_gebruiker(app):
    """Zelfde cijfers voor een ander adres mogen niet werken."""
    with app.app_context():
        code = magic.issue_code("a@test.be")
        # b heeft die code niet aangevraagd
        assert magic.verify_code("b@test.be", code) is None


def test_nieuwe_code_verstopt_de_oude(app):
    with app.app_context():
        code1 = magic.issue_code("x@test.be")
        code2 = magic.issue_code("x@test.be")
        # oude code is ongeldig gemaakt
        assert magic.verify_code("x@test.be", code1) is None
        # nieuwe werkt
        assert magic.verify_code("x@test.be", code2) == "x@test.be"


def test_login_flow_stuurt_code_en_logt_in(client, app):
    """E2E: e-mail invoeren → code (onderschept) → code invoeren → ingelogd."""
    verzonden = {}
    orig = magic.send_mail

    def vang(to, subject, html, text=None, headers=None):
        verzonden["to"] = to
        verzonden["subject"] = subject
        verzonden["text"] = text
    magic.send_mail = vang
    try:
        with app.app_context():
            db.session.add(Family(email="gezin@test.be", postcode="9000"))
            db.session.commit()
        # stap 1: e-mail invoeren
        r = client.post("/login", data={"email": "gezin@test.be"})
        assert r.status_code == 200
        assert b"6 cijfers" in r.data or b"code" in r.data.lower()
        # code uit de onderschepte mail halen (staat in subject en tekst)
        import re
        m = re.search(r"\b(\d{6})\b", verzonden.get("subject", "") + " " + (verzonden.get("text") or ""))
        assert m, "geen 6-cijferige code in de mail"
        code = m.group(1)
        # stap 2: code invoeren
        r = client.post("/code", data={"email": "gezin@test.be", "code": code})
        assert r.status_code == 302
        with client.session_transaction() as s:
            assert s.get("family_id") is not None
    finally:
        magic.send_mail = orig


def test_scanner_kan_code_niet_opbranden(client, app):
    """Kernpunt: er is geen link. Een GET op /code doet niets (geen route),
    en de code leeft tot ze bewust wordt ingetypt."""
    with app.app_context():
        code = magic.issue_code("scan@test.be")
    # een GET op /code bestaat niet (enkel POST) → 405, code blijft leven
    r = client.get("/code")
    assert r.status_code in (404, 405)
    with app.app_context():
        assert magic.verify_code("scan@test.be", code) == "scan@test.be"


def test_uitschrijven_raakt_account_niet(client, app):
    with app.app_context():
        fam = Family(email="nb@test.be", postcode="9000", newsletter_opt_in=True)
        db.session.add(fam)
        db.session.commit()
        fid = fam.id
        token = unsubscribe_token(fid)
    client.get(f"/uitschrijven/{token}")
    with app.app_context():
        fam = db.session.get(Family, fid)
        assert fam is not None  # account bestaat nog
        assert fam.newsletter_opt_in is False  # enkel opt-in uit
