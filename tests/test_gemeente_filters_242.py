"""Patch 242: de bijdragepagina schaalt naar Brussel én laat kiezen.

Twee problemen: de lijst kapte af op 400 (Brussel heeft er duizenden), en een
dienst toerisme moest honderden eetzaken voorbij scrollen om bij zijn eigen
speelterreinen te komen.
"""
import re

from argon2 import PasswordHasher

from app.extensions import db
from app.models import Admin, Event


def _token(app, client):
    with app.app_context():
        db.session.add(Admin(email="gf@r.be", pw_hash=PasswordHasher().hash("x"),
                             role="admin", totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        for n in range(250):
            db.session.add(Event(
                title=f"Frituur {n}", slug=f"gf-h{n}", source="osm",
                ext_id=f"gf-h{n}", is_permanent=True, pending=False,
                hidden=False, lat=50.85, lng=4.35, gemeente="Brussel",
                subtype="horeca", indoor=True, quality=50))
        for n in range(30):
            db.session.add(Event(
                title=f"Speeltuin {n}", slug=f"gf-s{n}", source="osm",
                ext_id=f"gf-s{n}", is_permanent=True, pending=False,
                hidden=False, lat=50.85, lng=4.35, gemeente="Brussel",
                subtype="playground", quality=60, categories=["buiten"]))
        for n in range(10):
            db.session.add(Event(
                title=f"Museum {n}", slug=f"gf-m{n}", source="osm",
                ext_id=f"gf-m{n}", is_permanent=True, pending=False,
                hidden=False, lat=50.85, lng=4.35, gemeente="Brussel",
                subtype="museum", indoor=True, quality=70,
                image_url="https://x/y.jpg" if n < 5 else None))
        db.session.commit()
        aid = Admin.query.filter_by(email="gf@r.be").first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    det = client.get("/beheer/gemeentecontacten/brussel").get_data(as_text=True)
    return re.search(r"gemeente-bijdrage/([\w\-]+)", det).group(1)


def test_filters_per_soort_met_tellers(client, app):
    token = _token(app, client)
    h = client.get(f"/gemeente-bijdrage/{token}").get_data(as_text=True)
    assert "Spelen (30)" in h
    assert "Eten &amp; drinken (250)" in h
    assert "Beleven (10)" in h


def test_filteren_op_speelterreinen(client, app):
    """Een dienst toerisme kiest zijn speeltuinen zonder door frituren te
    scrollen."""
    token = _token(app, client)
    h = client.get(f"/gemeente-bijdrage/{token}?groep=ravotten").get_data(as_text=True)
    assert h.count('name="fotos"') == 30
    assert "Frituur" not in h


def test_standaard_enkel_zonder_foto(client, app):
    token = _token(app, client)
    h = client.get(f"/gemeente-bijdrage/{token}?groep=beleven").get_data(as_text=True)
    assert h.count('name="fotos"') == 5          # 5 van de 10 hebben al een foto
    alles = client.get(
        f"/gemeente-bijdrage/{token}?groep=beleven&alles=1").get_data(as_text=True)
    assert alles.count('name="fotos"') == 10


def test_paginering_verbergt_geen_werk(client, app):
    """De oude limiet van 400 verborg in Brussel honderden plekken."""
    token = _token(app, client)
    h = client.get(f"/gemeente-bijdrage/{token}").get_data(as_text=True)
    assert h.count('name="fotos"') == 100        # per pagina
    assert "pagina 1 van" in h
    p3 = client.get(f"/gemeente-bijdrage/{token}?p=3").get_data(as_text=True)
    assert "pagina 3 van" in p3


def test_niemand_moet_alles_doen(client, app):
    token = _token(app, client)
    h = client.get(f"/gemeente-bijdrage/{token}").get_data(as_text=True)
    assert "zeker niet alles" in h               # geruststelling in de tekst
