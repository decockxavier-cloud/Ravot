"""Reviewersrol: rechten, teambeheer en security-grenzen."""
import pyotp
from argon2 import PasswordHasher
from app.extensions import db
from app.models import Admin, Event, Report, Photo, EnrichProposal


def _maak(app, role):
    with app.app_context():
        a = Admin(email=f"{role}{Admin.query.count()}@ravot.be",
                  pw_hash=PasswordHasher().hash("x"),
                  totp_secret=pyotp.random_base32(), totp_confirmed=True, role=role)
        db.session.add(a); db.session.commit()
        return a.id


def _login_als(client, aid):
    with client.session_transaction() as s:
        s["admin_id"] = aid; s["admin_2fa_ok"] = True


def test_reviewer_mag_nazicht(client, app):
    rid = _maak(app, "reviewer")
    _login_als(client, rid)
    assert client.get("/beheer/nazicht").status_code == 200


def test_reviewer_geweigerd_op_adminpaginas(client, app):
    """SECURITY: reviewer krijgt 403 op alle admin-only pagina's."""
    rid = _maak(app, "reviewer")
    _login_als(client, rid)
    for url in ("/beheer/", "/beheer/instellingen", "/beheer/verbindingen",
                "/beheer/verrijk", "/beheer/team"):
        assert client.get(url).status_code == 403, url


def test_reviewer_kan_content_valideren(client, app):
    """Reviewer kan een ingediende plek goedkeuren (de kernopdracht)."""
    rid = _maak(app, "reviewer")
    with app.app_context():
        ev = Event(source="user", pending=True, is_permanent=True, slug="rev-plek",
                   title="Reviewer Plek", gemeente="Gent", postcode="9000",
                   lat=51.0, lng=3.7, age_min=0, age_max=12, categories=["buiten"])
        db.session.add(ev); db.session.commit(); eid = ev.id
    _login_als(client, rid)
    client.post(f"/beheer/nazicht/plek/{eid}/goedkeuren", data={"csrf_token": "x"})
    with app.app_context():
        assert db.session.get(Event, eid).pending is False


def test_reviewer_kan_geen_sync_of_purge(client, app):
    """SECURITY: reviewer kan geen bronnen syncen of wissen."""
    rid = _maak(app, "reviewer")
    _login_als(client, rid)
    assert client.post("/beheer/sync/osm", data={"csrf_token": "x"}).status_code == 403
    assert client.post("/beheer/purge/osm", data={"bevestig": "ja", "csrf_token": "x"}).status_code == 403


def test_admin_maakt_en_verwijdert_reviewer(client, app):
    aid = _maak(app, "admin")
    _login_als(client, aid)
    client.post("/beheer/team", data={"email": "nieuw@rev.be",
                "wachtwoord": "supergeheim12tekens", "csrf_token": "x"})
    with app.app_context():
        r = Admin.query.filter_by(email="nieuw@rev.be").first()
        assert r is not None and r.role == "reviewer" and r.totp_confirmed is False
        rid = r.id
    client.post(f"/beheer/team/{rid}/verwijder", data={"csrf_token": "x"})
    with app.app_context():
        assert Admin.query.filter_by(email="nieuw@rev.be").first() is None


def test_admin_account_niet_verwijderbaar_via_team(client, app):
    """SECURITY: het admin-account zelf kan niet via teambeheer verwijderd worden."""
    aid = _maak(app, "admin")
    ander = _maak(app, "admin")
    _login_als(client, aid)
    client.post(f"/beheer/team/{ander}/verwijder", data={"csrf_token": "x"})
    with app.app_context():
        assert db.session.get(Admin, ander) is not None   # blijft bestaan


def test_zwak_reviewerwachtwoord_geweigerd(client, app):
    aid = _maak(app, "admin")
    _login_als(client, aid)
    client.post("/beheer/team", data={"email": "zwak@rev.be",
                "wachtwoord": "kort", "csrf_token": "x"})
    with app.app_context():
        assert Admin.query.filter_by(email="zwak@rev.be").first() is None


def test_reviewer_ziet_pending_fotos(client, app, tmp_path):
    """Reviewer mag pending foto's bekijken (nodig om te modereren)."""
    app.config["UPLOAD_DIR"] = str(tmp_path)
    rid = _maak(app, "reviewer")
    import io
    from PIL import Image
    from app.fotos import verwerk_upload
    with app.app_context():
        ev = Event(source="osm", ext_id="node/1", slug="f-plek", title="F",
                   is_permanent=True, categories=["buiten"], age_min=0, age_max=12,
                   lat=51.0, lng=3.7)
        db.session.add(ev); db.session.commit()
        buf = io.BytesIO(); Image.new("RGB", (30, 30)).save(buf, format="PNG")
        class F:
            filename = "x.png"
            def read(self): return buf.getvalue()
        with app.test_request_context():
            naam = verwerk_upload(F())
        db.session.add(Photo(event_id=ev.id, filename=naam, status="pending"))
        db.session.commit(); pid = Photo.query.first().id
    _login_als(client, rid)
    assert client.get(f"/foto/{pid}").status_code == 200      # reviewer ziet 'm
