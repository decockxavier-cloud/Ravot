"""Patch 187: het routeweefsel — fiches tonen langs welke route ze liggen,
en de routepagina krijgt de gezinslaag (afstand tot start + pauzeplan)."""
from app.extensions import db
from app.models import Event, Family, FietsRoute, RouteBuurt, Setting


def _opzet(app):
    with app.app_context():
        db.session.add(Setting(key="routes_in_menu", value="1"))
        r = FietsRoute(titel="IJsjesroute", slug="rw-ijs", afstand_km=18.0,
                       duur_min=110, moeilijkheid="makkelijk", is_lus=True,
                       pending=False, hidden=False, gemeente="Roeselare",
                       start_lat=50.946, start_lng=3.123,
                       geometrie=[[50.946, 3.123], [50.95, 3.13]],
                       bbox_n=50.96, bbox_z=50.94, bbox_w=3.11, bbox_o=3.14)
        sp = Event(title="Speeltuin", slug="rw-sp", source="osm", ext_id="rw-sp",
                   is_permanent=True, pending=False, hidden=False,
                   lat=50.9501, lng=3.1301, subtype="playground",
                   categories=["buiten"])
        fr = Event(title="Frituur", slug="rw-fr", source="osm", ext_id="rw-fr",
                   is_permanent=True, pending=False, hidden=False, lat=50.951,
                   lng=3.129, subtype="horeca", indoor=True)
        fam = Family(email="rw@t.be", postcode="8800")
        db.session.add_all([r, sp, fr, fam])
        db.session.flush()
        db.session.add(RouteBuurt(route_id=r.id, event_id=sp.id,
                                  afstand_m=120, route_km=6.2))
        db.session.add(RouteBuurt(route_id=r.id, event_id=fr.id,
                                  afstand_m=200, route_km=11.0))
        db.session.commit()
        return fam.id


def test_fiche_toont_route_chip(client, app):
    _opzet(app)
    h = client.get("/e/rw-sp").get_data(as_text=True)
    assert "Deze plek ligt langs" in h
    assert "IJsjesroute" in h and "km 6" in h


def test_routepagina_gezinslaag(client, app):
    fid = _opzet(app)
    h = client.get("/fietsroutes/rw-ijs").get_data(as_text=True)
    assert "1× ravotten" in h and "1× smullen" in h     # pauzeplan
    assert "van jullie" not in h                        # anoniem: geen afstand
    with client.session_transaction() as s:
        s["family_id"] = fid
    h = client.get("/fietsroutes/rw-ijs").get_data(as_text=True)
    assert "Start op ±" in h                            # gezin: wel


def test_gast_krijgt_ook_afstand(client, app):
    _opzet(app)
    with client.session_transaction() as s:
        s["guest"] = {"postcode": "9000"}
    h = client.get("/fietsroutes/rw-ijs").get_data(as_text=True)
    assert "Start op ±" in h


def test_schakelaar_uit_geen_chips(client, app):
    with app.app_context():
        db.session.add(Setting(key="routes_in_menu", value="0"))
        sp = Event(title="Speeltuin", slug="rw-uit", source="osm",
                   ext_id="rw-uit", is_permanent=True, pending=False,
                   hidden=False, lat=50.9, lng=3.1, subtype="playground")
        db.session.add(sp)
        db.session.commit()
    h = client.get("/e/rw-uit").get_data(as_text=True)
    assert "Deze plek ligt langs" not in h
