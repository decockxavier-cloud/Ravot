"""Patch 245: geen dode links en geen zinloze vragen op de gemeentelink.

Twee fouten: de lijst toonde evenementen uit UiTdatabank (een dienst toerisme
vragen om een foto van "Wintermarkt 2024" slaat nergens op), en fiches die
publiek verborgen zijn (uit_zichtbaar uit) gaven een 404 als je erop klikte.
"""
import re

from argon2 import PasswordHasher

from app.extensions import db
from app.models import Admin, Event, Setting


def _token(app, client):
    with app.app_context():
        db.session.add(Setting(key="uit_zichtbaar", value="0"))
        db.session.add(Admin(email="gb@r.be", pw_hash=PasswordHasher().hash("x"),
                             role="admin", totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        db.session.add(Event(title="Speeltuin Park", slug="gb245-1",
                             source="osm", ext_id="gb245-1", is_permanent=True,
                             pending=False, hidden=False, lat=50.85, lng=4.35,
                             gemeente="Brussel", subtype="playground"))
        db.session.add(Event(title="Wintermarkt", slug="gb245-2", source="uit",
                             ext_id="gb245-2", is_permanent=False,
                             pending=False, hidden=False, lat=50.85, lng=4.35,
                             gemeente="Brussel", subtype="markt"))
        db.session.add(Event(title="Museum uit UiT", slug="gb245-3",
                             source="uit", ext_id="gb245-3", is_permanent=True,
                             pending=False, hidden=False, lat=50.85, lng=4.35,
                             gemeente="Brussel", subtype="museum", indoor=True))
        db.session.commit()
        aid = Admin.query.filter_by(email="gb@r.be").first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    det = client.get("/beheer/gemeentecontacten/brussel").get_data(as_text=True)
    return re.search(r"gemeente-bijdrage/([\w\-]+)", det).group(1)


def test_geen_evenementen_in_de_fotolijst(client, app):
    """Een foto vragen van een voorbije wintermarkt heeft geen zin."""
    token = _token(app, client)
    h = client.get(f"/gemeente-bijdrage/{token}").get_data(as_text=True)
    assert "Speeltuin Park" in h
    assert "Wintermarkt" not in h


def test_geen_links_naar_verborgen_fiches(client, app):
    """Wat publiek verborgen is (uit_zichtbaar uit), geeft een 404 — zulke
    links tonen is de snelste manier om vertrouwen te verliezen."""
    token = _token(app, client)
    h = client.get(f"/gemeente-bijdrage/{token}").get_data(as_text=True)
    assert "Museum uit UiT" not in h
    assert client.get("/e/gb245-3").status_code == 404      # inderdaad dood
    assert client.get("/e/gb245-1").status_code == 200      # de getoonde werkt


def test_alle_getoonde_fiches_zijn_bereikbaar(client, app):
    token = _token(app, client)
    h = client.get(f"/gemeente-bijdrage/{token}").get_data(as_text=True)
    for slug in re.findall(r'/e/([\w\-]+)"', h):
        assert client.get(f"/e/{slug}").status_code == 200, slug
