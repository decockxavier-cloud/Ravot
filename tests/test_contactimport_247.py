"""Patch 247: contactgegevens van toeristische diensten invoeren uit een lijst.

Belangrijk uitgangspunt: wat jij zelf invulde of corrigeerde wint altijd van
een geïmporteerde lijst.
"""
import io

from argon2 import PasswordHasher

from app.extensions import db
from app.models import Admin, Event, GemeenteContact

CSV = (
    "Provincie,Gemeente,Dienst,E-mail,Telefoon,Plaats,Bron,Opmerking\n"
    "West-Vlaanderen,Bredene,Toeristische Dienst Bredene,toerisme@bredene.be,"
    "+32 59 56 19 70,8450 Bredene,bron.be,Specifiek toerisme-adres\n"
    "West-Vlaanderen,Kortrijk,Toerisme Kortrijk,toerisme@kortrijk.be,"
    "+32 56 27 78 40,8500 Kortrijk,bron.be,Specifiek toerisme-adres\n"
    "Limburg,Onbestaandegem,Dienst X,x@onbestaande.be,,,bron.be,\n"
)


def _admin(app, client):
    with app.app_context():
        db.session.add(Admin(email="ci@r.be", pw_hash=PasswordHasher().hash("x"),
                             role="admin", totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        for gem in ("Bredene", "Kortrijk"):
            db.session.add(Event(title=f"S {gem}", slug=f"ci-{gem}",
                                 source="osm", ext_id=f"ci-{gem}",
                                 is_permanent=True, pending=False,
                                 hidden=False, lat=51.0, lng=3.5,
                                 gemeente=gem, subtype="playground"))
        db.session.commit()
        aid = Admin.query.filter_by(email="ci@r.be").first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"


def _import(client, tekst=CSV):
    return client.post("/beheer/gemeentecontacten/import",
                       data={"bestand": (io.BytesIO(tekst.encode()), "d.csv")},
                       content_type="multipart/form-data",
                       follow_redirects=True).get_data(as_text=True)


def test_contacten_worden_gekoppeld(client, app):
    _admin(app, client)
    h = _import(client)
    assert "2 nieuwe contacten" in h
    with app.app_context():
        b = db.session.get(GemeenteContact, "bredene")
        assert b.email == "toerisme@bredene.be"
        assert b.dienst == "Toeristische Dienst Bredene"
        assert "56 19 70" in (b.notitie or "")        # telefoon bewaard
        assert "Specifiek toerisme-adres" in (b.notitie or "")
        assert b.token_geldig                          # link meteen bruikbaar


def test_eigen_invoer_wordt_niet_overschreven(client, app):
    """Wat jij corrigeerde weegt zwaarder dan een lijst."""
    _admin(app, client)
    with app.app_context():
        db.session.add(GemeenteContact(gemeente="kortrijk",
                                       email="mijn@eigen.be"))
        db.session.commit()
    h = _import(client)
    assert "1 overgeslagen" in h
    with app.app_context():
        assert db.session.get(GemeenteContact, "kortrijk").email == "mijn@eigen.be"


def test_onbekende_gemeenten_worden_gemeld(client, app):
    _admin(app, client)
    h = _import(client)
    assert "niet gevonden in Ravot" in h
    assert "Onbestaandegem" in h
    with app.app_context():
        assert db.session.get(GemeenteContact, "onbestaandegem") is None


def test_rommel_wordt_geweigerd(client, app):
    _admin(app, client)
    h = client.post("/beheer/gemeentecontacten/import",
                    data={"bestand": (io.BytesIO(b"x"), "foto.jpg")},
                    content_type="multipart/form-data",
                    follow_redirects=True).get_data(as_text=True)
    assert ".xlsx- of .csv-bestand" in h
