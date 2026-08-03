"""Patch 163: straatnamen voor naamloze plekken + vrije Commons-foto's."""
import io
from unittest.mock import patch

from PIL import Image

import pytest
from argon2 import PasswordHasher

from app.extensions import db
from app.models import Admin, Event, Photo


@pytest.fixture
def admin_client(client, app):
    with app.app_context():
        db.session.add(Admin(email="a@ravot.be",
                             pw_hash=PasswordHasher().hash("x"),
                             totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        db.session.commit()
        aid = Admin.query.first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    return client


class _Resp:
    status_code = 200

    def __init__(self, data, content=b""):
        self._d, self._c = data, content

    def json(self):
        return self._d

    @property
    def content(self):
        return self._c


def test_straatnamen_alleen_generieke_titels(app):
    with app.app_context():
        for i, titel in enumerate(("Speeltuin", "Speeltuin — Gent", "Park",
                                   "Speelplein De Vos")):
            db.session.add(Event(title=titel, slug=f"st{i}", source="osm",
                                 ext_id=f"st{i}", is_permanent=True,
                                 pending=False, hidden=False, lat=51.0,
                                 lng=3.7, gemeente="Gent", postcode="9000",
                                 subtype="playground"))
        db.session.commit()
        with patch("app.services.verrijking.requests.get",
                   return_value=_Resp({"address": {"road": "Kerkstraat"}})), \
             patch("app.services.verrijking.time.sleep"):
            from app.services.verrijking import vul_straatnamen
            n, kandidaten = vul_straatnamen()
        assert kandidaten == 3 and n == 3
        titels = {e.title for e in Event.query.all()}
        assert "Speeltuin — Kerkstraat" in titels
        assert "Speelplein De Vos" in titels        # echte naam onaangeroerd
        assert Event.query.filter_by(slug="st0").first().adres == "Kerkstraat"


def test_commons_zoek_weert_onvrije_licenties(app):
    data = {"query": {"pages": {
        "1": {"title": "File:Vrij.jpg", "imageinfo": [{
            "url": "https://upload.wikimedia.org/a.jpg",
            "thumburl": "https://upload.wikimedia.org/t.jpg",
            "extmetadata": {"LicenseShortName": {"value": "CC BY 4.0"},
                            "Artist": {"value": "X"}}}]},
        "2": {"title": "File:NietCommercieel.jpg", "imageinfo": [{
            "url": "https://upload.wikimedia.org/b.jpg",
            "extmetadata": {"LicenseShortName": {"value": "CC BY-NC 2.0"}}}]},
    }}}
    with app.app_context():
        with patch("app.services.verrijking.requests.get",
                   return_value=_Resp(data)):
            from app.services.verrijking import commons_zoek
            uit = commons_zoek(51.0, 3.7)
        assert len(uit) == 1 and uit[0]["licentie"] == "CC BY 4.0"


def test_commons_import_alleen_wikimedia_domein(admin_client, app):
    with app.app_context():
        db.session.add(Event(title="Speeltuin", slug="ci1", source="osm",
                             ext_id="ci1", is_permanent=True, pending=False,
                             hidden=False, lat=51.0, lng=3.7,
                             subtype="playground"))
        db.session.commit()
        eid = Event.query.filter_by(slug="ci1").first().id
    r = admin_client.post(f"/beheer/activiteit/{eid}/vrije-fotos/import",
                          data={"url": "https://kwaadaardig.be/x.jpg"},
                          follow_redirects=True)
    assert "Wikimedia zelf" in r.get_data(as_text=True)
    jpg = io.BytesIO()
    Image.new("RGB", (900, 700), (10, 120, 40)).save(jpg, "JPEG")
    with patch("app.services.verrijking.requests.get",
               return_value=_Resp({}, jpg.getvalue())):
        r = admin_client.post(f"/beheer/activiteit/{eid}/vrije-fotos/import", data={
            "url": "https://upload.wikimedia.org/x.jpg",
            "fotograaf": "Jan", "licentie": "CC BY 4.0",
            "pagina": "https://commons.wikimedia.org/p/1"},
            follow_redirects=True)
    assert "bronvermelding" in r.get_data(as_text=True)
    with app.app_context():
        p = Photo.query.filter_by(bron="commons").first()
        assert p is not None and p.fotograaf == "Jan" and p.status == "approved"
    h = admin_client.get("/e/ci1").get_data(as_text=True)
    assert "Jan" in h and "CC BY 4.0" in h
