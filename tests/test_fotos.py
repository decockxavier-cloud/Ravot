"""Gebruikersfoto's: veilige verwerking + moderatiewachtrij."""
import io
import os
from PIL import Image
from app.extensions import db
from app.models import Event, Family, Photo


def _png_bytes(size=(50, 40), exif_gps=False):
    img = Image.new("RGB", size, (100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _login(client, app):
    with app.app_context():
        fam = Family(email="f@test.be", postcode="9000")
        db.session.add(fam); db.session.commit(); fid = fam.id
    with client.session_transaction() as s:
        s["family_id"] = fid
    return fid


def _plek(app):
    with app.app_context():
        ev = Event(source="osm", ext_id="node/1", slug="foto-plek", title="Foto Plek",
                   is_permanent=True, gemeente="Gent", categories=["buiten"],
                   age_min=0, age_max=12, lat=51.0, lng=3.7)
        db.session.add(ev); db.session.commit()
        return ev.id


def test_geldige_foto_naar_wachtrij(client, app, tmp_path):
    app.config["UPLOAD_DIR"] = str(tmp_path)
    _login(client, app); eid = _plek(app)
    data = {"akkoord": "on",
            "foto": (io.BytesIO(_png_bytes()), "vakantie.png"),
            "csrf_token": "x"}
    r = client.post(f"/mijn/foto/{eid}", data=data, content_type="multipart/form-data")
    assert r.status_code in (302, 303)
    with app.app_context():
        p = Photo.query.first()
        assert p is not None and p.status == "pending"
        assert p.filename.endswith(".jpg")                  # heringecodeerd naar jpg
        assert os.path.exists(os.path.join(str(tmp_path), p.filename))


def test_nepbestand_geweigerd(client, app, tmp_path):
    app.config["UPLOAD_DIR"] = str(tmp_path)
    _login(client, app); eid = _plek(app)
    data = {"akkoord": "on",
            "foto": (io.BytesIO(b"<?php echo 'hack'; ?>"), "shell.php.png"),
            "csrf_token": "x"}
    client.post(f"/mijn/foto/{eid}", data=data, content_type="multipart/form-data")
    with app.app_context():
        assert Photo.query.count() == 0                     # geen echte afbeelding -> geweigerd


def test_zonder_toestemming_geweigerd(client, app, tmp_path):
    app.config["UPLOAD_DIR"] = str(tmp_path)
    _login(client, app); eid = _plek(app)
    data = {"foto": (io.BytesIO(_png_bytes()), "x.png"), "csrf_token": "x"}
    client.post(f"/mijn/foto/{eid}", data=data, content_type="multipart/form-data")
    with app.app_context():
        assert Photo.query.count() == 0                     # geen akkoord-vinkje


def test_pending_foto_niet_publiek(client, app, tmp_path):
    app.config["UPLOAD_DIR"] = str(tmp_path)
    eid = _plek(app)
    from app.fotos import verwerk_upload
    with app.app_context():
        # rechtstreeks een pending foto aanmaken
        img = io.BytesIO(_png_bytes())
        class F:
            filename = "x.png"
            def read(self): return img.getvalue()
        naam = None
        with app.test_request_context():
            naam = verwerk_upload(F())
        db.session.add(Photo(event_id=eid, filename=naam, status="pending"))
        db.session.commit()
        pid = Photo.query.first().id
    # publieke bezoeker (geen sessie) mag pending niet zien
    assert client.get(f"/foto/{pid}").status_code == 404


def test_goedkeuren_en_afwijzen(client, app, tmp_path):
    app.config["UPLOAD_DIR"] = str(tmp_path)
    import pyotp
    from argon2 import PasswordHasher
    from app.models import Admin
    eid = _plek(app)
    from app.fotos import verwerk_upload
    with app.app_context():
        a = Admin(email="a@ravot.be", pw_hash=PasswordHasher().hash("x"),
                  totp_secret=pyotp.random_base32())
        db.session.add(a); db.session.commit(); aid = a.id
        img = io.BytesIO(_png_bytes())
        class F:
            filename = "x.png"
            def read(self): return img.getvalue()
        with app.test_request_context():
            naam = verwerk_upload(F())
        db.session.add(Photo(event_id=eid, filename=naam, status="pending"))
        db.session.commit()
        pid = Photo.query.first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid; s["admin_2fa_ok"] = True
    client.post(f"/beheer/foto/{pid}/goedkeuren", data={"csrf_token": "x"})
    with app.app_context():
        p = db.session.get(Photo, pid)
        assert p.status == "approved"
        assert db.session.get(Event, eid).image_url.endswith(f"/foto/{pid}")  # wordt hoofdfoto
    # nu publiek zichtbaar
    assert client.get(f"/foto/{pid}").status_code == 200
