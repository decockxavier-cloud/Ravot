"""Vriendensysteem (wederzijds akkoord + vervallende code) en wraakbestendige score."""
from tests.conftest import login_as
from app.extensions import db
from app.models import Family, Connection, FriendInvite, Review, Event
from app.pricing import aggregate_ravotscore


def _maak_gezin(email):
    f = Family(email=email, postcode="9000", display_name=email.split("@")[0])
    db.session.add(f); db.session.commit()
    return f


def test_vrienden_vereist_wederzijds_akkoord(client, app):
    with app.app_context():
        a = _maak_gezin("a@t.be"); b = _maak_gezin("b@t.be")
        aid, bid = a.id, b.id
    # A haalt zijn code op
    login_as(client, type("F", (), {"id": aid})())
    with client.session_transaction() as s:
        s["family_id"] = aid
    r = client.get("/mijn/vrienden")
    import re
    m = re.search(r'letter-spacing:4px[^>]*>([A-Z0-9]{6})<', r.get_data(as_text=True))
    assert m, "geen code gevonden op de pagina"
    code_a = m.group(1)
    # B voert A's code in → pending, nog geen vriendschap
    with client.session_transaction() as s:
        s["family_id"] = bid
    client.post("/mijn/vrienden", data={"code": code_a})
    with app.app_context():
        conn = Connection.query.first()
        assert conn is not None
        assert conn.status == "pending"  # nog niet gekoppeld
    # A voert B's code in → nu accepted
    with client.session_transaction() as s:
        s["family_id"] = bid
    rb = client.get("/mijn/vrienden")
    mb = re.search(r'letter-spacing:4px[^>]*>([A-Z0-9]{6})<', rb.get_data(as_text=True))
    code_b = mb.group(1)
    with client.session_transaction() as s:
        s["family_id"] = aid
    client.post("/mijn/vrienden", data={"code": code_b})
    with app.app_context():
        conn = Connection.query.first()
        assert conn.status == "accepted"  # wederzijds akkoord → gekoppeld


def test_verlopen_vriendcode_werkt_niet(client, app):
    from datetime import datetime, timedelta
    with app.app_context():
        a = _maak_gezin("x@t.be"); b = _maak_gezin("y@t.be")
        aid, bid = a.id, b.id
    with client.session_transaction() as s:
        s["family_id"] = aid
    r = client.get("/mijn/vrienden")
    import re
    code_a = re.search(r'letter-spacing:4px[^>]*>([A-Z0-9]{6})<', r.get_data(as_text=True)).group(1)
    # laat de code verlopen
    with app.app_context():
        inv = FriendInvite.query.first()
        inv.expires_at = datetime.utcnow() - timedelta(hours=1)
        db.session.commit()
    with client.session_transaction() as s:
        s["family_id"] = bid
    client.post("/mijn/vrienden", data={"code": code_a})
    with app.app_context():
        assert Connection.query.count() == 0  # verlopen code koppelt niet


def test_gewogen_gemiddelde_dempt_extreme_score():
    """Eén extreme score mag een event niet kelderen (wraak-preventie)."""
    class R:
        def __init__(self, k): self.kid_score = k; self.child_ages = []; self.cost_range = None
    # één boze 1-score
    een = aggregate_ravotscore([R(1)])
    # het ruwe gemiddelde is 1.0, maar de getoonde (gewogen) score ligt hoger
    assert een["avg_ruw"] == 1.0
    assert een["avg"] > 1.5  # naar het midden getrokken
    # bij veel consistente scores nadert de gewogen score het echte gemiddelde
    veel = aggregate_ravotscore([R(5)] * 20)
    assert veel["avg"] >= 4.5
