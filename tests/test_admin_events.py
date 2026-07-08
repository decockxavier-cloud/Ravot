"""Admin kan alle fiches beheren; zoek-fix; beeld-sanering."""
from datetime import datetime, timedelta
import pyotp
from argon2 import PasswordHasher
from app.extensions import db
from app.models import Admin, Event


def _admin(client, app):
    with app.app_context():
        a = Admin(email="a@r.be", pw_hash=PasswordHasher().hash("x"),
                  totp_secret=pyotp.random_base32(), totp_confirmed=True, role="admin")
        db.session.add(a); db.session.commit()
        aid = a.id
    with client.session_transaction() as s:
        s["admin_id"] = aid; s["admin_2fa_ok"] = True


def test_admin_kan_permanente_plek_bewerken(client, app):
    with app.app_context():
        db.session.add(Event(source="osm", ext_id="o", slug="perm", title="Speeltuin Park",
            is_permanent=True, gemeente="Gent", postcode="9000", lat=51.0, lng=3.7,
            age_min=0, age_max=12, categories=["buiten"], quality=40))
        db.session.commit()
        eid = Event.query.filter_by(slug="perm").first().id
    _admin(client, app)
    assert client.get(f"/beheer/activiteiten/{eid}").status_code == 200
    r = client.post(f"/beheer/activiteiten/{eid}", data={
        "csrf_token": "x", "title": "Nieuwe Naam", "gemeente": "Gent",
        "postcode": "9000", "categorie": "buiten", "age_min": "3", "age_max": "9"})
    assert r.status_code == 302
    with app.app_context():
        ev = db.session.get(Event, eid)
        assert ev.title == "Nieuwe Naam" and ev.age_min == 3
        assert ev.quality is not None          # herberekend na edit


def test_admin_activiteiten_zoekt_ook_pending(client, app):
    with app.app_context():
        db.session.add(Event(source="user", slug="wacht", title="Nog In Wachtrij",
            is_permanent=True, pending=True, gemeente="Gent", postcode="9000",
            lat=51.0, lng=3.7, age_min=0, age_max=12, categories=["buiten"]))
        db.session.commit()
    _admin(client, app)
    html = client.get("/beheer/activiteiten?status=pending").get_data(as_text=True)
    assert "Nog In Wachtrij" in html


def test_zoekbalk_tekst_niet_gekaapt_door_plaatsprefix(client, app):
    """'poppen' (prefix van gemeente 'Poppel') mag de tekstzoek niet kapen."""
    now = datetime.utcnow()
    with app.app_context():
        db.session.add(Event(source="uit", ext_id="p", slug="poptheater",
            title="Poppentheater Pinokkio", start=now + timedelta(hours=4),
            gemeente="Brugge", postcode="8000", lat=51.209, lng=3.224,
            age_min=0, age_max=12, categories=["cultuur"], quality=70))
        db.session.commit()
    html = client.get("/ontdek?q=poppen").get_data(as_text=True)
    assert "Poppentheater Pinokkio" in html


def test_beeld_sanering():
    from app.media import _veilige_afbeelding
    assert _veilige_afbeelding("http://x.be/f.jpg") == "https://x.be/f.jpg"
    assert _veilige_afbeelding("//up.be/a.png") == "https://up.be/a.png"
    assert _veilige_afbeelding("https://tracker.evil/pixel") is None   # geen echt beeld
    assert _veilige_afbeelding("https://x.be/foto.jpg") == "https://x.be/foto.jpg"
    assert _veilige_afbeelding(None) is None
