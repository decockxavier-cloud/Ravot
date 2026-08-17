"""Patch 263: onderweg een plek toevoegen vanaf de routekaart.

Wie tijdens een fietstocht een speeltuin ontdekt die er nog niet op staat, wil
die kunnen melden zonder eerst een adres op te zoeken. De knop verschijnt
zodra de gps-positie bekend is en geeft die mee aan het formulier — daarna kom
je weer op de route uit, want je zit midden in een tocht.
"""
from app.extensions import db
from app.models import Event, FietsRoute, Family


def _opzet(app, client, ingelogd=True):
    with app.app_context():
        db.session.add(FietsRoute(titel="Lus", slug="po263", afstand_km=18,
                                  duur_min=110, moeilijkheid="vlak",
                                  is_lus=True, pending=False, hidden=False,
                                  start_lat=50.94, start_lng=3.12,
                                  geometrie=[[50.94, 3.12], [50.95, 3.13]],
                                  routebeschrijving="Knooppunten: 74 – 32"))
        fam = Family(email="po@t.be", postcode="8800")
        db.session.add(fam)
        db.session.commit()
        fid = fam.id
    if ingelogd:
        with client.session_transaction() as s:
            s["family_id"] = fid
    return fid


def test_knop_staat_klaar_maar_verborgen(client, app):
    """Pas zichtbaar als de positie bekend is — anders zou de speld nergens
    staan."""
    _opzet(app, client)
    h = client.get("/fietsroutes/po263").get_data(as_text=True)
    assert 'id="plek-hier"' in h
    blok = h[h.index("plek-hier"):h.index("plek-hier") + 220]
    assert "hidden" in blok
    assert "mijn/toevoegen" in blok


def test_gast_gaat_eerst_aanmelden(client, app):
    _opzet(app, client, ingelogd=False)
    h = client.get("/fietsroutes/po263").get_data(as_text=True)
    blok = h[h.index("plek-hier"):h.index("plek-hier") + 220]
    assert "/login" in blok


def test_positie_vult_de_speld_vooraf(client, app):
    _opzet(app, client)
    h = client.get(
        "/mijn/toevoegen?lat=50.9464&lng=3.1233").get_data(as_text=True)
    assert 'data-lat="50.9464"' in h
    assert "waar je stond" in h


def test_onzinnige_coordinaten_worden_genegeerd(client, app):
    _opzet(app, client)
    for query in ("?lat=999&lng=abc", "?lat=0&lng=0", "?lat=&lng="):
        h = client.get("/mijn/toevoegen" + query).get_data(as_text=True)
        assert "data-lat" not in h


def test_na_indienen_terug_naar_de_route(client, app):
    _opzet(app, client)
    h = client.get("/mijn/toevoegen?lat=50.946&lng=3.123&route=po263"
                   ).get_data(as_text=True)
    assert 'name="route" value="po263"' in h
    r = client.post("/mijn/toevoegen", data={
        "titel": "Nieuwe speeltuin", "soort": "playground",
        "lat": "50.946", "lng": "3.123", "route": "po263",
        "age_min": "0", "age_max": "12"})
    assert "/fietsroutes/po263" in r.headers.get("Location", "")
    with app.app_context():
        assert Event.query.filter_by(title="Nieuwe speeltuin").count() == 1


def test_onbekende_route_leidt_gewoon_naar_huis(client, app):
    _opzet(app, client)
    r = client.post("/mijn/toevoegen", data={
        "titel": "Zonder route", "soort": "playground",
        "lat": "50.946", "lng": "3.123", "route": "bestaatniet",
        "age_min": "0", "age_max": "12"})
    assert "/vandaag" in r.headers.get("Location", "")


def test_kaartscript_koppelt_positie_aan_de_knop():
    with open("app/static/js/route-kaart.js", encoding="utf-8") as f:
        src = f.read()
    assert "plek-hier" in src
    assert "werkPlekknopBij" in src
