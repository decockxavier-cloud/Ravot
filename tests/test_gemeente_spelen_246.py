"""Patch 246: één vraag aan de gemeente — foto's van de speelterreinen.

Evenementen cureert UiTdatabank al, eetzaken doen de uitbaters zelf. Een
dienst toerisme daarmee lastigvallen levert niets op en schrikt af.
"""
import re

from argon2 import PasswordHasher

from app.extensions import db
from app.models import Admin, Event, Setting


def _token(app, client):
    with app.app_context():
        db.session.add(Setting(key="uit_zichtbaar", value="0"))
        db.session.add(Admin(email="gs@r.be", pw_hash=PasswordHasher().hash("x"),
                             role="admin", totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        for n in range(12):
            db.session.add(Event(
                title=f"Speeltuin {n}", slug=f"gs246-s{n}", source="osm",
                ext_id=f"gs246-s{n}", is_permanent=True, pending=False,
                hidden=False, lat=50.85, lng=4.35, gemeente="Brussel",
                subtype="playground", categories=["buiten"],
                image_url="https://x/y.jpg" if n < 3 else None))
        for n in range(200):
            db.session.add(Event(
                title=f"Frituur {n}", slug=f"gs246-h{n}", source="osm",
                ext_id=f"gs246-h{n}", is_permanent=True, pending=False,
                hidden=False, lat=50.85, lng=4.35, gemeente="Brussel",
                subtype="horeca", indoor=True))
        for n in range(50):
            db.session.add(Event(
                title=f"Museum {n}", slug=f"gs246-m{n}", source="osm",
                ext_id=f"gs246-m{n}", is_permanent=True, pending=False,
                hidden=False, lat=50.85, lng=4.35, gemeente="Brussel",
                subtype="museum", indoor=True))
        db.session.commit()
        aid = Admin.query.filter_by(email="gs@r.be").first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    det = client.get("/beheer/gemeentecontacten/brussel").get_data(as_text=True)
    return re.search(r"gemeente-bijdrage/([\w\-]+)", det).group(1), det


def test_alleen_speelplekken_in_de_lijst(client, app):
    token, _ = _token(app, client)
    h = client.get(f"/gemeente-bijdrage/{token}").get_data(as_text=True)
    assert "Speeltuin 3" in h
    assert "Frituur" not in h and "Museum" not in h
    # Sinds patch 265 toont de standaardweergave ALLE speelplekken (ook die
    # mét foto — daar staan nu de veldvragen); de fotofilter blijft bestaan
    # als 'zonder=1' en geeft nog steeds exact de plekken zonder foto.
    assert h.count('name="fotos"') == 12
    h2 = client.get(f"/gemeente-bijdrage/{token}?zonder=1").get_data(as_text=True)
    assert h2.count('name="fotos"') == 9       # 12 minus 3 die al een foto hebben


def test_geen_soortfilters_meer(client, app):
    """Eén vraag, geen keuzemenu: dat is wat het haalbaar maakt."""
    token, _ = _token(app, client)
    h = client.get(f"/gemeente-bijdrage/{token}").get_data(as_text=True)
    assert "Eten &amp; drinken" not in h
    alle = client.get(f"/gemeente-bijdrage/{token}?alles=1").get_data(as_text=True)
    assert alle.count('name="fotos"') == 12


def test_mail_telt_speelplekken_en_stelt_gerust(client, app):
    _, det = _token(app, client)
    assert "12 speelterreinen" in det          # niet 262
    assert "Van 9 daarvan" in det
    assert "eetzaken houden de uitbaters" in det
