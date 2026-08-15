"""Patch 240: toeristische diensten leveren zelf tekst en foto's aan.

Een dienst vrije tijd heeft er belang bij dat het aanbod in zijn stad goed
getoond wordt. Met een jaarlijkse, deelbare link kan die dienst bijdragen
zonder account — alles via de gewone moderatiewachtrij.
"""
import io
import re

from PIL import Image
from argon2 import PasswordHasher

from app.extensions import db
from app.models import (Admin, Event, GemeenteContact, GemeenteTekst, Photo)


def _foto():
    b = io.BytesIO()
    Image.new("RGB", (900, 700), (90, 140, 60)).save(b, "JPEG")
    b.seek(0)
    return b


def _opzet(app, client):
    with app.app_context():
        db.session.add(Admin(email="gc@r.be", pw_hash=PasswordHasher().hash("x"),
                             role="admin", totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        for n in range(4):
            db.session.add(Event(
                title=f"Speeltuin {n}", slug=f"gc240-{n}", source="osm",
                ext_id=f"gc240-{n}", is_permanent=True, pending=False,
                hidden=False, lat=50.94, lng=3.12, gemeente="Roeselare",
                subtype="playground", quality=70))
        db.session.add(Event(title="Elders", slug="gc240-x", source="osm",
                             ext_id="gc240-x", is_permanent=True,
                             pending=False, hidden=False, lat=51.2, lng=4.4,
                             gemeente="Antwerpen", subtype="playground",
                             quality=70))
        db.session.commit()
        aid = Admin.query.filter_by(email="gc@r.be").first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    det = client.get("/beheer/gemeentecontacten/roeselare").get_data(as_text=True)
    return re.search(r"gemeente-bijdrage/([\w\-]+)", det).group(1)


def test_mailtekst_en_link_staan_klaar(client, app):
    token = _opzet(app, client)
    det = client.get("/beheer/gemeentecontacten/roeselare").get_data(as_text=True)
    assert "mogen we uw hulp vragen" in det        # onderwerp
    assert "geen account nodig" in det.lower() or "Geen account" in det
    assert len(token) > 20                          # geraden kan niet


def test_dienst_kan_tekst_aanleveren_maar_niet_publiceren(client, app):
    token = _opzet(app, client)
    client.post(f"/gemeente-bijdrage/{token}/tekst", data={
        "intro_md": "In Roeselare ravot je het best in het Bergmolenbos.",
        "auteur": "Dienst Toerisme"}, follow_redirects=True)
    with app.app_context():
        t = db.session.get(GemeenteTekst, "roeselare")
        assert t.pending and t.van_gemeente          # eerst nazicht
    pub = client.get("/roeselare").get_data(as_text=True)
    assert "Bergmolenbos" not in pub                 # nog niet online


def test_beheer_publiceert_na_nazicht(client, app):
    token = _opzet(app, client)
    client.post(f"/gemeente-bijdrage/{token}/tekst",
                data={"intro_md": "Tekst van de dienst."}, follow_redirects=True)
    client.post("/beheer/gemeenteteksten/roeselare",
                data={"intro_md": "Tekst van de dienst.", "auteur": "Dienst"},
                follow_redirects=True)
    with app.app_context():
        assert db.session.get(GemeenteTekst, "roeselare").pending is False
    assert "Tekst van de dienst" in client.get("/roeselare").get_data(as_text=True)


def test_fotos_alleen_voor_de_eigen_gemeente(client, app):
    """Een token geeft toegang tot bijdragen voor één gemeente, niet tot de
    rest van Vlaanderen."""
    token = _opzet(app, client)
    with app.app_context():
        eigen = Event.query.filter_by(slug="gc240-0").first().id
        vreemd = Event.query.filter_by(slug="gc240-x").first().id
    r = client.post(f"/gemeente-bijdrage/{token}/foto/{eigen}",
                    data={"fotos": (_foto(), "a.jpg")},
                    content_type="multipart/form-data", follow_redirects=True)
    assert "1 foto" in r.get_data(as_text=True)
    r2 = client.post(f"/gemeente-bijdrage/{token}/foto/{vreemd}",
                     data={"fotos": (_foto(), "b.jpg")},
                     content_type="multipart/form-data")
    assert r2.status_code == 403
    with app.app_context():
        assert Photo.query.filter_by(status="pending").count() == 1


def test_onbekend_of_verlopen_token_geeft_niets(client, app):
    _opzet(app, client)
    assert client.get("/gemeente-bijdrage/nepnepnepnepnepnep123").status_code == 404
    with app.app_context():
        from datetime import date, timedelta
        c = db.session.get(GemeenteContact, "roeselare")
        c.token_tot = date.today() - timedelta(days=1)
        db.session.commit()
        verlopen = c.token
    assert client.get(f"/gemeente-bijdrage/{verlopen}").status_code == 404


def test_verstuur_en_verrijk_worden_bijgehouden(client, app):
    """Zodat de jaarlijkse opfrisvraag geen giswerk is."""
    token = _opzet(app, client)
    client.post("/beheer/gemeentecontacten/roeselare",
                data={"actie": "verstuurd"}, follow_redirects=True)
    client.post(f"/gemeente-bijdrage/{token}/tekst",
                data={"intro_md": "Iets."}, follow_redirects=True)
    with app.app_context():
        c = db.session.get(GemeenteContact, "roeselare")
        assert c.laatst_verstuurd is not None
        assert c.laatst_verrijkt is not None
        assert c.aantal_bijdragen >= 1
        assert c.vraagt_opfrissing is False          # net verstuurd
