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


def test_verrijking_overleeft_hersync(app):
    """De continu draaiende OSM-sync mag straat-titel en adres niet terugzetten
    (de bug waardoor 'flask straatnamen' telkens dezelfde records verrijkte)."""
    with app.app_context():
        from app.services.sources.base import upsert_event
        data = {"source": "osm", "ext_id": "node/9", "title": "Speeltuin",
                "is_permanent": True, "lat": 51.0, "lng": 3.7, "adres": None,
                "gemeente": "Gent", "postcode": "9000",
                "subtype": "playground", "pending": False}
        upsert_event(dict(data))
        db.session.commit()
        with patch("app.services.verrijking.requests.get",
                   return_value=_Resp({"address": {"road": "Kerkstraat"}})), \
             patch("app.services.verrijking.time.sleep"):
            from app.services.verrijking import vul_straatnamen
            vul_straatnamen()
        upsert_event(dict(data))          # hersync met kale data
        db.session.commit()
        ev = Event.query.filter_by(ext_id="node/9").first()
        assert ev.title == "Speeltuin — Kerkstraat"
        assert ev.adres == "Kerkstraat"
        # echte naam uit de bron wint wél
        data["title"] = "Speelplein De Warande"
        upsert_event(dict(data))
        db.session.commit()
        assert Event.query.filter_by(ext_id="node/9").first().title == \
            "Speelplein De Warande"


def test_osm_commons_tag_naar_auto_import(app):
    """Patch 165: OSM-mapper koppelde zelf een Commons-foto -> volautomatische
    import met attributie; vreemde hosts worden genegeerd."""
    import io
    from PIL import Image
    from app.services.sources.osm import _commons_bestand
    assert _commons_bestand({"wikimedia_commons": "File:X.jpg"}) == "File:X.jpg"
    assert _commons_bestand({"image": "https://mijnsite.be/f.jpg"}) is None
    jpg = io.BytesIO()
    Image.new("RGB", (900, 700), (80, 140, 60)).save(jpg, "JPEG")

    def nep(url, **kw):
        if "commons.wikimedia.org" in url:
            return _Resp({"query": {"pages": {"1": {"imageinfo": [{
                "thumburl": "https://upload.wikimedia.org/x/k.jpg",
                "url": "https://upload.wikimedia.org/x/k_full.jpg",
                "descriptionshorturl": "https://commons.wikimedia.org/p/9",
                "extmetadata": {"LicenseShortName": {"value": "CC BY 4.0"},
                                "Artist": {"value": "An Fotograve"}}}]}}}})
        return _Resp({}, jpg.getvalue())

    with app.app_context():
        from app.services.sources.base import upsert_event
        upsert_event({"source": "osm", "ext_id": "way/70", "title": "Kasteel",
                      "is_permanent": True, "lat": 50.98, "lng": 3.55,
                      "subtype": "castle", "pending": False,
                      "commons_file": "File:X.jpg"})
        db.session.commit()
        with patch("app.services.verrijking.requests.get", side_effect=nep), \
             patch("app.services.verrijking.time.sleep"):
            from app.services.verrijking import importeer_osm_fotos
            n, gep = importeer_osm_fotos()
        assert (n, gep) == (1, 1)
        p = Photo.query.filter_by(bron="commons").first()
        assert p.fotograaf == "An Fotograve" and p.status == "approved"
        # tweede run: niets meer te doen
        with patch("app.services.verrijking.requests.get", side_effect=nep), \
             patch("app.services.verrijking.time.sleep"):
            assert importeer_osm_fotos() == (0, 0)


def test_illustratie_fallback_op_fiche(client, app):
    with app.app_context():
        db.session.add(Event(title="Frituur Kaal", slug="frk", source="osm",
                             ext_id="frk", is_permanent=True, pending=False,
                             hidden=False, lat=51.0, lng=3.7,
                             subtype="horeca"))
        db.session.add(Event(title="Zwembad Kaal", slug="zwk", source="osm",
                             ext_id="zwk", is_permanent=True, pending=False,
                             hidden=False, lat=51.0, lng=3.7,
                             subtype="zwembad"))
        db.session.commit()
    h = client.get("/e/frk").get_data(as_text=True)
    assert "cat-smullen.svg" in h and "fiche-beeld-illustratie" in h
    h = client.get("/e/zwk").get_data(as_text=True)
    assert "cat-zwem.svg" in h


def test_eigen_type_illustratie(admin_client, app):
    """Patch 167: beheerder vervangt een type-illustratie; die geldt meteen
    in lijst en fiche, wordt genormaliseerd naar 800x400 en is terugzetbaar."""
    import io as _io
    import os
    import shutil
    from PIL import Image as _Im
    shutil.rmtree("/data/uploads/typen", ignore_errors=True)
    with app.app_context():
        db.session.add(Event(title="Frituur", slug="il1", source="osm",
                             ext_id="il1", is_permanent=True, pending=False,
                             hidden=False, lat=50.95, lng=3.12,
                             subtype="horeca", indoor=True))
        db.session.commit()
    groot = _io.BytesIO()
    _Im.new("RGB", (1600, 1000), (240, 130, 50)).save(groot, "PNG")
    groot.seek(0)
    r = admin_client.post("/beheer/illustraties/smullen",
                          data={"beeld": (groot, "eigen.png")},
                          content_type="multipart/form-data",
                          follow_redirects=True)
    assert "vervangen" in r.get_data(as_text=True)
    with _Im.open("/data/uploads/typen/smullen.jpg") as im:
        assert im.size == (800, 400)
    h = admin_client.get("/e/il1").get_data(as_text=True)
    assert "/typebeeld/smullen" in h
    assert admin_client.get("/typebeeld/smullen").status_code == 200
    assert admin_client.get("/typebeeld/xx").status_code == 404
    admin_client.post("/beheer/illustraties/smullen",
                      data={"actie": "terugzetten"}, follow_redirects=True)
    h = admin_client.get("/e/il1").get_data(as_text=True)
    assert "cat-smullen.svg" in h
    assert not os.path.exists("/data/uploads/typen/smullen.jpg")
