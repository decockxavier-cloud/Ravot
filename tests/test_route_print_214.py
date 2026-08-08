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


def test_printblad_is_liggend_met_onderweg_op_blad_twee(client, app):
    """Patch 215: kaart groot op blad 1 (liggend A4), alles onderweg op de
    achterkant en gegroepeerd per soort."""
    _opzet(app)
    h = client.get("/fietsroutes/pr-1/print").get_data(as_text=True)
    assert "A4 landscape" in h
    assert "print-blad2" in h and "break-before: page" in h
    assert "Ravotten" in h                        # gegroepeerd i.p.v. één lijst
    svg = client.get("/fietsroutes/pr-1/kaartje.svg").get_data(as_text=True)
    assert 'viewBox="0 0 1400 900"' in svg        # liggende verhouding


def test_routelijst_zonder_n_plus_1(client, app):
    """Patch 215: de lijst haalde per route apart zijn buurt op (N+1) en laadde
    daardoor traag. Nu één join voor alle routes samen."""
    from sqlalchemy import event as sa_event
    from app.models import FietsRoute, RouteBuurt
    with app.app_context():
        for i in range(8):
            r = FietsRoute(titel=f"Route {i}", slug=f"np-{i}", afstand_km=20,
                           duur_min=120, moeilijkheid="vlak", is_lus=True,
                           pending=False, hidden=False, gemeente="Roeselare",
                           regio="Leiestreek", start_lat=50.94 + i * 0.01,
                           start_lng=3.12, beschrijving="Een fijne lus.")
            db.session.add(r)
            db.session.flush()
            for j in range(25):
                ev = Event(title=f"P{i}-{j}", slug=f"np-{i}-{j}", source="osm",
                           ext_id=f"np-{i}-{j}", is_permanent=True,
                           pending=False, hidden=False, lat=50.95, lng=3.13,
                           subtype="horeca" if j % 2 else "playground",
                           indoor=bool(j % 2))
                db.session.add(ev)
                db.session.flush()
                db.session.add(RouteBuurt(route_id=r.id, event_id=ev.id,
                                          afstand_m=100, route_km=j * 0.3))
        db.session.commit()
        motor = db.engine
    teller = {"n": 0}

    @sa_event.listens_for(motor, "before_cursor_execute")
    def _tel(conn, cursor, stmt, params, context, executemany):
        teller["n"] += 1

    try:
        h = client.get("/fietsroutes").get_data(as_text=True)
    finally:
        sa_event.remove(motor, "before_cursor_execute", _tel)
    assert "Route 0" in h and "× ravotten" in h
    assert teller["n"] < 25, f"te veel queries: {teller['n']}"
