"""Patch 214: startpunt op de fiche en een afdrukbaar routeblad met kaartje,
knooppunten (met straatnaam) en de stops onderweg."""
from app.extensions import db
from app.models import Event, FietsRoute, Knooppunt, RouteBuurt


def _opzet(app):
    with app.app_context():
        r = FietsRoute(titel="Knuffelronde", slug="pr-1", afstand_km=18.0,
                       duur_min=110, moeilijkheid="vlak", is_lus=True,
                       pending=False, hidden=False, gemeente="Roeselare",
                       regio="Leiestreek", start_lat=50.946, start_lng=3.123,
                       start_adres="Sint-Amandsstraat, Roeselare",
                       geometrie=[[50.946, 3.123], [50.955, 3.14],
                                  [50.96, 3.12], [50.946, 3.123]],
                       routebeschrijving="Knooppunten: 74 – 32 – 2 – 85")
        sp = Event(title="Speeltuin Sterrebos", slug="pr-sp", source="osm",
                   ext_id="pr-sp", is_permanent=True, pending=False,
                   hidden=False, lat=50.95, lng=3.13, subtype="playground",
                   categories=["buiten"])
        db.session.add_all([r, sp])
        db.session.flush()
        db.session.add(RouteBuurt(route_id=r.id, event_id=sp.id,
                                  afstand_m=120, route_km=6.2))
        for nr, straat in (("74", "Sint-Amandsstraat"), ("32", "Kasteeldreef")):
            db.session.add(Knooppunt(nummer=nr, straat=straat, lat=50.95,
                                     lng=3.13, netwerk="test"))
        db.session.commit()


def test_printblad_heeft_alles_om_mee_te_nemen(client, app):
    _opzet(app)
    h = client.get("/fietsroutes/pr-1/print").get_data(as_text=True)
    assert "Sint-Amandsstraat, Roeselare" in h        # startpunt
    assert ">74<" in h and "Kasteeldreef" in h        # knooppunt + straatnaam
    assert "Speeltuin Sterrebos" in h and "km 6.2" in h
    assert "kaartje.svg" in h                         # kaartje ingesloten
    assert "onclick" not in h                         # CSP-proof


def test_kaartje_is_geldige_svg(client, app):
    _opzet(app)
    r = client.get("/fietsroutes/pr-1/kaartje.svg")
    assert r.status_code == 200
    assert b"<polyline" in r.data and b"<svg" in r.data


def test_fiche_toont_startpunt_en_printknop(client, app):
    _opzet(app)
    h = client.get("/fietsroutes/pr-1").get_data(as_text=True)
    assert "Startpunt:" in h and "Sint-Amandsstraat" in h
    assert "Printblad" in h
