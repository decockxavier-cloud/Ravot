"""Patch 249: contacten ophalen uit de open data van Toerisme Vlaanderen.

Officiële dataset (Modellicentie Gratis Hergebruik) in plaats van handmatig
adressen verzamelen. Wat de beheerder zelf invulde wint altijd — een open
dataset kan verouderd zijn.
"""
from unittest.mock import patch

from argon2 import PasswordHasher

from app.extensions import db
from app.models import Admin, Event, GemeenteContact

NEP = {"tourist": {"centers": [
    {"name": "Toerisme Roeselare", "main_city_name": "Roeselare",
     "city_name": "Roeselare", "postal_code": "8800",
     "email": "toerisme@roeselare.be", "phone1": "051 26 96 17",
     "sub_type": "Infokantoor", "changed_time": "2025-03-11T09:00:00"},
    {"name": "Visit Brugge", "main_city_name": "Brugge", "city_name": "Brugge",
     "postal_code": "8000", "email": "toerisme@brugge.be",
     "phone1": "050 44 46 46"},
    {"name": "Verwijderd kantoor", "main_city_name": "Roeselare",
     "email": "oud@roeselare.be", "deleted": "1"},
    {"name": "Zonder mail", "main_city_name": "Tielt", "email": ""},
    {"name": "Onbekende gemeente", "main_city_name": "Verweggistan",
     "email": "x@verweg.be"},
    {"name": "Toerisme Roeselare", "main_city_name": "Roeselare",
     "email": "dubbel@roeselare.be"},
]}}


class _Antw:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return NEP


def _opzet(app, client):
    with app.app_context():
        db.session.add(Admin(email="ik@r.be", pw_hash=PasswordHasher().hash("x"),
                             role="admin", totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        for gem in ("Roeselare", "Brugge", "Tielt"):
            db.session.add(Event(title=f"S {gem}", slug=f"ik249-{gem}",
                                 source="osm", ext_id=f"ik249-{gem}",
                                 is_permanent=True, pending=False,
                                 hidden=False, lat=51.0, lng=3.5,
                                 gemeente=gem, subtype="playground"))
        db.session.commit()
        aid = Admin.query.filter_by(email="ik@r.be").first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"


def _ophalen(client):
    from app.services import infokantoren as IK
    with patch.object(IK.requests, "get", return_value=_Antw()):
        return client.post("/beheer/gemeentecontacten/ophalen",
                           follow_redirects=True).get_data(as_text=True)


def test_contacten_worden_gekoppeld(client, app):
    _opzet(app, client)
    _ophalen(client)
    with app.app_context():
        ro = db.session.get(GemeenteContact, "roeselare")
        assert ro.email == "toerisme@roeselare.be"
        assert ro.dienst == "Toerisme Roeselare"
        assert "051 26 96 17" in (ro.notitie or "")
        assert ro.token_geldig                # link meteen bruikbaar


def test_eigen_invoer_wint_van_open_data(client, app):
    _opzet(app, client)
    with app.app_context():
        db.session.add(GemeenteContact(gemeente="brugge", email="mijn@eigen.be"))
        db.session.commit()
    h = _ophalen(client)
    assert "1 overgeslagen" in h
    with app.app_context():
        assert db.session.get(GemeenteContact, "brugge").email == "mijn@eigen.be"


def test_rommel_uit_de_bron_wordt_geweerd(client, app):
    """Verwijderde records, dubbels en records zonder e-mail."""
    _opzet(app, client)
    _ophalen(client)
    with app.app_context():
        ro = db.session.get(GemeenteContact, "roeselare")
        assert ro.email == "toerisme@roeselare.be"     # niet het dubbel
        assert db.session.get(GemeenteContact, "tielt") is None   # geen mail
        assert db.session.get(GemeenteContact, "verweggistan") is None


def test_niet_gekoppelde_gemeenten_worden_gemeld(client, app):
    _opzet(app, client)
    h = _ophalen(client)
    assert "Verweggistan" in h
    assert "geen aanbod in Ravot" in h


def test_storing_bij_de_bron_hindert_niet(client, app):
    _opzet(app, client)
    from app.services import infokantoren as IK
    with patch.object(IK.requests, "get", side_effect=RuntimeError("bron plat")):
        h = client.post("/beheer/gemeentecontacten/ophalen",
                        follow_redirects=True).get_data(as_text=True)
    assert "Ophalen mislukt" in h


def test_schrijfwijzeverschillen_koppelen_toch(client, app):
    """Patch 251: Sint-Truiden werd gemeld als 'geen aanbod' terwijl er 152
    fiches stonden — de brondata schrijft gemeenten net anders."""
    from app.models import GemeenteContact
    varianten = {"data": [
        {"name": "Toerisme Sint-Truiden", "main_city_name": "Sint Truiden",
         "city_name": "Sint-Truiden", "email": "toerisme@sint-truiden.be"},
        {"name": "Visit Schilde", "main_city_name": "Schilde",
         "city_name": "'s Gravenwezel", "email": "toerisme@schilde.be"},
        {"name": "Toerisme Hoegaarden", "main_city_name": "HOEGAARDEN",
         "email": "toerisme@hoegaarden.be"},
        {"name": "Elders", "main_city_name": "Verweggistan", "email": "x@v.be"},
    ]}

    class _V:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return varianten

    with app.app_context():
        db.session.add(Admin(email="ik251@r.be",
                             pw_hash=PasswordHasher().hash("x"), role="admin",
                             totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        for gem in ("Sint-Truiden", "'s Gravenwezel", "Hoegaarden"):
            db.session.add(Event(title="S", slug=f"ik251-{gem}", source="osm",
                                 ext_id=f"ik251-{gem}", is_permanent=True,
                                 pending=False, hidden=False, lat=51.0,
                                 lng=3.5, gemeente=gem, subtype="playground"))
        db.session.commit()
        aid = Admin.query.filter_by(email="ik251@r.be").first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    from app.services import infokantoren as IK
    with patch.object(IK.requests, "get", return_value=_V()):
        h = client.post("/beheer/gemeentecontacten/ophalen",
                        follow_redirects=True).get_data(as_text=True)
    with app.app_context():
        assert db.session.get(GemeenteContact, "sint-truiden") is not None
        assert db.session.get(GemeenteContact, "'s gravenwezel") is not None
        assert db.session.get(GemeenteContact, "hoegaarden") is not None
    assert "Verweggistan" in h            # echt onbekend blijft gemeld
