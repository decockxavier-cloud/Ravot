"""Magic links: eenmalig, 15 min, nooit token in de databank; uitschrijven raakt account niet."""
from datetime import datetime, timedelta

from app.extensions import db
from app.models import Family, MagicToken
from app.services import magic
from app.services.weekendmail import unsubscribe_token


def test_token_single_use(app):
    token = magic.issue_token("x@test.be")
    assert magic.verify_token(token) == "x@test.be"
    assert magic.verify_token(token) is None          # tweede keer: verbrand


def test_token_expiry(app):
    token = magic.issue_token("x@test.be")
    row = MagicToken.query.first()
    row.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.session.commit()
    assert magic.verify_token(token) is None


def test_token_stored_hashed(app):
    token = magic.issue_token("x@test.be")
    row = MagicToken.query.first()
    assert token not in row.token_hash and len(row.token_hash) == 64  # sha256, nooit plaintext


def test_rate_limit_counter(app):
    for _ in range(3):
        magic.issue_token("spam@test.be")
    assert magic.recent_requests("spam@test.be") == 3


def test_unsubscribe_keeps_account(client, seed):
    fam = seed["fam_a"]
    fam.newsletter_opt_in = True
    db.session.commit()
    token = unsubscribe_token(fam.id)
    resp = client.get(f"/uitschrijven/{token}")
    assert resp.status_code == 200
    fam = db.session.get(Family, fam.id)
    assert fam is not None                             # account bestaat nog
    assert fam.newsletter_opt_in is False              # enkel de mail staat uit
    assert "account blijft gewoon bestaan" in resp.get_data(as_text=True)


def test_unsubscribe_bad_token(client, seed):
    resp = client.get("/uitschrijven/kapot.token")
    assert resp.status_code == 200
    assert db.session.get(Family, seed["fam_a"].id) is not None
