"""Patch 239: het slotje volgt de schakelaar, en de koppelafstand werkt
achteraf ook.

Twee stille fouten: de 🔒-iconen keken alleen naar 'ingelogd' en niet naar de
nieuwe instelling, en de koppelafstand ('leuk onderweg' in meter) werd alleen
toegepast bij het promoveren — wijzigen deed niets zichtbaars.
"""
from argon2 import PasswordHasher

from app.extensions import db
from app.models import (Admin, Event, FietsRoute, RouteBuurt, Setting)


def _route(app, **kw):
    with app.app_context():
        r = FietsRoute(titel="L", slug="ux239", afstand_km=18, duur_min=110,
                       moeilijkheid="vlak", is_lus=True, pending=False,
                       hidden=False, gpx_bestand="x.gpx",
                       geometrie=[[50.94, 3.12], [50.95, 3.13]],
                       routebeschrijving="Knooppunten: 74 – 32", **kw)
        db.session.add(r)
        db.session.commit()
        return r.id


def test_slotje_bij_muur_aan(client, app):
    _route(app)
    h = client.get("/fietsroutes/ux239").get_data(as_text=True)
    assert h.count("🔒") >= 3           # GPX, printblad, bingo


def test_geen_slotje_als_de_muur_uit_staat(client, app):
    with app.app_context():
        db.session.add(Setting(key="routes_login_vereist", value="0"))
        db.session.commit()
    _route(app)
    h = client.get("/fietsroutes/ux239").get_data(as_text=True)
    assert "🔒" not in h                # niets beloven wat er niet is


def test_koppelafstand_werkt_na_herberekenen(client, app):
    """De opgeslagen buurt bleef staan bij een gewijzigde afstand."""
    with app.app_context():
        db.session.add(Admin(email="hk@r.be", pw_hash=PasswordHasher().hash("x"),
                             role="admin", totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        r = FietsRoute(titel="Kort", slug="ux239-hk", afstand_km=1,
                       duur_min=10, moeilijkheid="vlak", is_lus=False,
                       pending=False, hidden=False,
                       geometrie=[[50.9400, 3.1200], [50.9490, 3.1200]],
                       start_lat=50.94, start_lng=3.12, bbox_n=50.96,
                       bbox_z=50.93, bbox_w=3.10, bbox_o=3.14)
        ev = Event(title="Ver weg", slug="ux239-ev", source="osm",
                   ext_id="ux239-ev", is_permanent=True, pending=False,
                   hidden=False, lat=50.9445, lng=3.1285,
                   subtype="playground")     # ~600 m van het tracé
        db.session.add_all([r, ev])
        db.session.commit()
        from app.services import routes_gis
        assert routes_gis.koppel_route(r) == 0      # buiten 400 m
        db.session.commit()
        db.session.add(Setting(key="route_buurt_meter", value="900"))
        db.session.commit()
        aid = Admin.query.filter_by(email="hk@r.be").first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    h = client.post("/beheer/routes/herkoppel",
                    follow_redirects=True).get_data(as_text=True)
    assert "900 m" in h
    with app.app_context():
        assert RouteBuurt.query.count() == 1
