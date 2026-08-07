"""Patch 198: levendige routelijst — gesprokkelde foto van onderweg,
tekstsnippet, onderweg-profiel en een overzichtskaart met streeklabels."""
from app.extensions import db
from app.models import Event, FietsRoute, RouteBuurt


def _opzet(app):
    with app.app_context():
        r = FietsRoute(titel="Schommelronde", slug="rl-1", afstand_km=18.0,
                       duur_min=110, moeilijkheid="vlak", is_lus=True,
                       pending=False, hidden=False, gemeente="Roeselare",
                       regio="Leiestreek", start_lat=50.946, start_lng=3.123,
                       beschrijving="Een vrolijke lus vol speelplezier voor "
                                    "het hele gezin, met plekjes om te "
                                    "pauzeren en iets lekkers te smullen.",
                       geometrie=[[50.946, 3.123], [50.95, 3.13]])
        sp = Event(title="Speeltuin Sterrebos", slug="rl-sp", source="osm",
                   ext_id="rl-sp", is_permanent=True, pending=False,
                   hidden=False, lat=50.95, lng=3.13, subtype="playground",
                   categories=["buiten"],
                   image_url="https://ravot.be/media/sterrebos.jpg")
        fr = Event(title="Frituur", slug="rl-fr", source="osm", ext_id="rl-fr",
                   is_permanent=True, pending=False, hidden=False, lat=50.951,
                   lng=3.129, subtype="horeca", indoor=True)
        db.session.add_all([r, sp, fr])
        db.session.flush()
        db.session.add(RouteBuurt(route_id=r.id, event_id=sp.id,
                                  afstand_m=120, route_km=6.2))
        db.session.add(RouteBuurt(route_id=r.id, event_id=fr.id,
                                  afstand_m=200, route_km=11.0))
        db.session.commit()


def test_lijst_toont_foto_snippet_en_profiel(client, app):
    _opzet(app)
    h = client.get("/fietsroutes").get_data(as_text=True)
    assert "sterrebos.jpg" in h                       # gesprokkelde foto
    assert "Onderweg: Speeltuin Sterrebos" in h       # eerlijk gelabeld
    assert "vrolijke lus vol speelplezier" in h       # snippet
    assert "1× ravotten" in h and "1× smullen" in h   # onderweg-profiel


def test_lijst_heeft_kaart_met_streeklabel(client, app):
    _opzet(app)
    h = client.get("/fietsroutes").get_data(as_text=True)
    assert 'id="routes-kaart"' in h
    assert "Leiestreek" in h                          # streeklabel-data
    assert "routes_kaart.js" in h                     # extern script (CSP)
    assert "leaflet@1.9.4" in h
