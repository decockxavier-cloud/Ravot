"""Patch 190: foto's meesturen bij het toevoegen van een plek, en de
leeftijdsbovengrens tot 99 ('vanaf X jaar')."""
import io

from PIL import Image

from app.extensions import db
from app.models import Event, Family, Photo


def _foto():
    b = io.BytesIO()
    Image.new("RGB", (900, 700), (90, 140, 60)).save(b, "JPEG")
    b.seek(0)
    return b


def _gezin(app):
    with app.app_context():
        fam = Family(email="pt190@t.be", postcode="8800")
        db.session.add(fam)
        db.session.commit()
        return fam.id


def test_plek_met_fotos_en_ruime_leeftijd(client, app):
    fid = _gezin(app)
    with client.session_transaction() as s:
        s["family_id"] = fid
    r = client.post("/mijn/toevoegen", data={
        "titel": "Avonturenpark", "postcode": "8800", "gemeente": "Roeselare",
        "soort": "playground", "categorie": "buiten",
        "age_min": "6", "age_max": "99", "aard": "vast", "foto_akkoord": "1",
        "fotos": [(_foto(), "a.jpg"), (_foto(), "b.jpg")],
    }, content_type="multipart/form-data", follow_redirects=True)
    assert "2 foto" in r.get_data(as_text=True)
    with app.app_context():
        ev = Event.query.filter_by(title="Avonturenpark").first()
        assert ev.pending and (ev.age_min, ev.age_max) == (6, 99)
        fotos = Photo.query.filter_by(event_id=ev.id).all()
        assert len(fotos) == 2
        assert all(f.status == "pending" for f in fotos)   # moderatie eerst


def test_zonder_akkoord_geen_fotos(client, app):
    fid = _gezin(app)
    with client.session_transaction() as s:
        s["family_id"] = fid
    client.post("/mijn/toevoegen", data={
        "titel": "Zonder akkoord", "postcode": "8800", "aard": "vast",
        "fotos": [(_foto(), "c.jpg")],
    }, content_type="multipart/form-data", follow_redirects=True)
    with app.app_context():
        ev = Event.query.filter_by(title="Zonder akkoord").first()
        assert ev is not None                      # plek wél ingediend
        assert Photo.query.filter_by(event_id=ev.id).count() == 0


def test_maximaal_drie_fotos(client, app):
    fid = _gezin(app)
    with client.session_transaction() as s:
        s["family_id"] = fid
    client.post("/mijn/toevoegen", data={
        "titel": "Vijf foto's", "postcode": "8800", "aard": "vast",
        "foto_akkoord": "1",
        "fotos": [(_foto(), f"f{i}.jpg") for i in range(5)],
    }, content_type="multipart/form-data", follow_redirects=True)
    with app.app_context():
        ev = Event.query.filter_by(title="Vijf foto's").first()
        assert Photo.query.filter_by(event_id=ev.id).count() == 3
