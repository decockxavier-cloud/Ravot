"""Patch 219: een geschrapte plek verdwijnt overal van de route.

De buurt wordt één keer gelegd bij het promoveren; wie daarna verborgen wordt
of terug in nazicht gaat, bleef op de route staan.
"""
from app.extensions import db
from app.models import Event, FietsRoute, RouteBuurt


def _login_gezin(client, app):
    """Printblad, GPX en bingo vragen sinds patch 226 een gratis profiel."""
    from app.models import Family
    with app.app_context():
        fam = Family(email=f"slot-{id(client)}@t.be", postcode="8800")
        db.session.add(fam)
        db.session.commit()
        fid = fam.id
    with client.session_transaction() as s:
        s["family_id"] = fid


def _opzet(app):
    with app.app_context():
        r = FietsRoute(titel="Testlus", slug="gs-1", afstand_km=18,
                       duur_min=110, moeilijkheid="vlak", is_lus=True,
                       pending=False, hidden=False, gemeente="Roeselare",
                       regio="Leiestreek", start_lat=50.94, start_lng=3.12,
                       geometrie=[[50.94, 3.12], [50.95, 3.13]],
                       routebeschrijving="Knooppunten: 74 – 32")
        db.session.add(r)
        db.session.flush()
        for n, (titel, hidden, pending) in enumerate([
                ("Speeltuin Blijft", False, False),
                ("Speeltuin Geschrapt", True, False),
                ("Frituur Nazicht", False, True)]):
            ev = Event(title=titel, slug=f"gs-{n}", source="osm",
                       ext_id=f"gs-{n}", is_permanent=True, pending=pending,
                       hidden=hidden, lat=50.95, lng=3.13,
                       subtype="playground" if n < 2 else "horeca",
                       indoor=n == 2,
                       categories=["buiten"] if n < 2 else [])
            db.session.add(ev)
            db.session.flush()
            db.session.add(RouteBuurt(route_id=r.id, event_id=ev.id,
                                      afstand_m=100, route_km=n))
        db.session.commit()
        return r.id


def test_geschrapte_plek_verdwijnt_van_de_fiche(client, app):
    _opzet(app)
    h = client.get("/fietsroutes/gs-1").get_data(as_text=True)
    assert "Speeltuin Blijft" in h
    assert "Geschrapt" not in h and "Nazicht" not in h


def test_ook_weg_uit_lijst_en_printblad(client, app):
    _opzet(app)
    lijst = client.get("/fietsroutes").get_data(as_text=True)
    assert "1× ravotten" in lijst          # niet 2
    _login_gezin(client, app)
    print_h = client.get("/fietsroutes/gs-1/print").get_data(as_text=True)
    assert "Speeltuin Blijft" in print_h and "Geschrapt" not in print_h


def test_opruimen_wist_verweesde_koppelingen(app):
    rid = _opzet(app)
    with app.app_context():
        from app.services.routes_gis import ruim_buurt_op
        assert RouteBuurt.query.filter_by(route_id=rid).count() == 3
        assert ruim_buurt_op() == 2                  # verborgen + pending
        assert RouteBuurt.query.filter_by(route_id=rid).count() == 1
