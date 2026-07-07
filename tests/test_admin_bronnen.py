"""Admin-gestuurde sync/verwijderen + statusbijhouding."""
import pyotp
from argon2 import PasswordHasher

from app.extensions import db
from app.models import Admin, Event, SyncStatus


def _login(client, app):
    with app.app_context():
        a = Admin(email="a@ravot.be", pw_hash=PasswordHasher().hash("wachtwoord123"),
                  totp_secret=pyotp.random_base32())
        db.session.add(a); db.session.commit()
        aid = a.id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True


def _osm_event(source="osm"):
    return Event(source=source, ext_id="node/1", slug=f"speeltuin-{source}-node-1",
                 title="Speeltuin", is_permanent=True, gemeente="Gent", postcode="9000",
                 lat=51.05, lng=3.72, age_min=1, age_max=12, categories=["buiten"],
                 is_free=True)


# ---------------------------------------------------------------- status --

def test_sync_one_zet_status(app):
    """sync_one('tm') zonder key doet niets, maar zet netjes de status op 'done'."""
    from app.services.sources import sync_one
    with app.app_context():
        sync_one("tm")
        st = db.session.get(SyncStatus, "tm")
        assert st is not None and st.state == "done"
        assert st.last_run is not None


def test_purge_source_functie(app):
    from app.services.sources import purge_source
    with app.app_context():
        db.session.add(_osm_event()); db.session.commit()
        assert Event.query.filter_by(source="osm").count() == 1
        n = purge_source("osm")
        assert n == 1
        assert Event.query.filter_by(source="osm").count() == 0


# ------------------------------------------------------------ admin-routes --

def test_sync_route_vereist_login(client, app):
    r = client.post("/beheer/sync/all")
    assert r.status_code in (301, 302)                 # redirect naar login
    assert "/beheer/login" in r.headers.get("Location", "") or r.status_code == 302


def test_admin_kan_sync_starten(client, app):
    _login(client, app)
    r = client.post("/beheer/sync/all", follow_redirects=True)   # geen bronnen aan = no-op
    assert r.status_code == 200
    assert "Sync gestart" in r.get_data(as_text=True)


def test_admin_purge_vereist_bevestiging(client, app):
    _login(client, app)
    with app.app_context():
        db.session.add(_osm_event()); db.session.commit()
    # zonder bevestiging: niets verwijderd
    r = client.post("/beheer/purge/osm", data={}, follow_redirects=True)
    assert "bevestig" in r.get_data(as_text=True).lower()
    with app.app_context():
        assert Event.query.filter_by(source="osm").count() == 1
    # mét bevestiging: weg
    r = client.post("/beheer/purge/osm", data={"bevestig": "ja"}, follow_redirects=True)
    assert "verwijderd" in r.get_data(as_text=True).lower()
    with app.app_context():
        assert Event.query.filter_by(source="osm").count() == 0


def test_verbindingen_toont_knoppen(client, app):
    _login(client, app)
    html = client.get("/beheer/verbindingen").get_data(as_text=True)
    assert "Nu syncen" in html
    assert "Alle ingeschakelde bronnen nu syncen" in html
    assert "Verwijder" in html
