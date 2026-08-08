"""Patch 220: een speld op de kaart levert altijd een gemeente op.

Zonder gemeente is een fiche onvindbaar bij het zoeken op stad. Alleen het
publieke toevoegen-formulier leidde die af; beheer en uitbater niet.
"""
from argon2 import PasswordHasher

from app.extensions import db
from app.models import Admin, Event, PostcodeCentroid


def _centroids(app):
    with app.app_context():
        db.session.add(PostcodeCentroid(postcode="8800", gemeente="Roeselare",
                                        lat=50.9464, lng=3.1233))
        db.session.add(PostcodeCentroid(postcode="8870", gemeente="Izegem",
                                        lat=50.9186, lng=3.2103))
        db.session.commit()


def test_helper_vindt_dichtstbijzijnde_gemeente(app):
    _centroids(app)
    with app.app_context():
        from app.geo import gemeente_uit_punt
        assert gemeente_uit_punt(50.947, 3.124) == ("Roeselare", "8800")
        assert gemeente_uit_punt(50.919, 3.211) == ("Izegem", "8870")
        assert gemeente_uit_punt(48.0, 2.0) == (None, None)   # te ver: niets
        assert gemeente_uit_punt(None, None) == (None, None)


def test_beheer_speld_vult_gemeente_aan(client, app):
    _centroids(app)
    with app.app_context():
        db.session.add(Admin(email="sp@r.be", pw_hash=PasswordHasher().hash("x"),
                             role="admin", totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        ev = Event(title="Speldplek", slug="sp-1", source="user",
                   ext_id="sp-1", is_permanent=True, pending=False,
                   hidden=False, subtype="playground")
        db.session.add(ev)
        db.session.commit()
        eid, aid = ev.id, Admin.query.filter_by(email="sp@r.be").first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    client.post(f"/beheer/activiteiten/{eid}", data={
        "actie": "bewerk", "titel": "Speldplek", "lat": "50.947",
        "lng": "3.124", "categorie": "ravotten"}, follow_redirects=True)
    with app.app_context():
        ev = db.session.get(Event, eid)
        assert ev.gemeente == "Roeselare" and ev.postcode == "8800"


def test_ingevulde_gemeente_wint_van_speld(client, app):
    """Wat de mens invult blijft staan — de speld vult alleen gaten."""
    _centroids(app)
    with app.app_context():
        db.session.add(Admin(email="sp2@r.be",
                             pw_hash=PasswordHasher().hash("x"), role="admin",
                             totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        ev = Event(title="Eigen naam", slug="sp-2", source="user",
                   ext_id="sp-2", is_permanent=True, pending=False,
                   hidden=False, subtype="playground",
                   gemeente="Rumbeke", postcode="8800")
        db.session.add(ev)
        db.session.commit()
        eid, aid = ev.id, Admin.query.filter_by(email="sp2@r.be").first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    client.post(f"/beheer/activiteiten/{eid}", data={
        "actie": "bewerk", "titel": "Eigen naam", "gemeente": "Rumbeke",
        "postcode": "8800", "lat": "50.947", "lng": "3.124",
        "categorie": "ravotten"}, follow_redirects=True)
    with app.app_context():
        assert db.session.get(Event, eid).gemeente == "Rumbeke"


def test_buitenlandse_punten_krijgen_geen_vlaamse_gemeente(app):
    """Patch 221: bij Maaseik liggen Nederlandse en Duitse plekken op enkele
    kilometers. Met een ruime grens kregen die 'Maaseik' toegewezen — onwaar,
    en erger dan geen gemeente."""
    with app.app_context():
        db.session.add(PostcodeCentroid(postcode="3680", gemeente="Maaseik",
                                        lat=51.0975, lng=5.7869))
        db.session.commit()
        from app.geo import gemeente_uit_punt
        assert gemeente_uit_punt(51.098, 5.788) == ("Maaseik", "3680")
        assert gemeente_uit_punt(51.060, 5.855) == (None, None)   # Susteren NL
        assert gemeente_uit_punt(51.001, 5.869) == (None, None)   # Sittard NL
