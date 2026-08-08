"""Patch 217: het printblad toont een échte kaart (OSM-tegels) in plaats van
een lijn op een effen vlak, met caching en een nette terugval.

De tegelserver wordt hier gemockt: tests mogen niet van een externe (gratis)
dienst afhangen. In gewone tests slaat de kaartbouw zichzelf over; alleen met
forceer=True wordt hij bewust uitgevoerd.
"""
import io
import os
from unittest.mock import patch

from PIL import Image

from app.extensions import db
from app.models import FietsRoute


def _route(app):
    with app.app_context():
        r = FietsRoute(titel="Kaartlus", slug="sk-1", afstand_km=16,
                       duur_min=95, moeilijkheid="vlak", is_lus=True,
                       pending=False, hidden=False, start_lat=51.10,
                       start_lng=5.79,
                       geometrie=[[51.10, 5.79], [51.12, 5.82],
                                  [51.11, 5.85], [51.09, 5.83],
                                  [51.10, 5.79]])
        db.session.add(r)
        db.session.commit()
        return r.id


class _NepTegel:
    status_code = 200

    def raise_for_status(self):
        pass

    @property
    def content(self):
        b = io.BytesIO()
        Image.new("RGB", (256, 256), (233, 229, 220)).save(b, "PNG")
        return b.getvalue()


def test_kaart_wordt_samengesteld_en_gecachet(app, tmp_path):
    rid = _route(app)
    with app.app_context():
        from app.models import FietsRoute as FR
        from app.services import statische_kaart as SK
        r = db.session.get(FR, rid)
        teller = {"n": 0}

        def nep(url, **kw):
            teller["n"] += 1
            return _NepTegel()

        with patch.object(SK.requests, "get", side_effect=nep):
            pad = SK.kaart_bestand(r, map_=str(tmp_path), forceer=True)
        assert pad and os.path.exists(pad)
        assert Image.open(pad).size == (1400, 900)      # liggend A4
        opgehaald = teller["n"]
        assert opgehaald > 0
        with patch.object(SK.requests, "get", side_effect=nep):
            SK.kaart_bestand(r, map_=str(tmp_path), forceer=True)
        assert teller["n"] == opgehaald                 # tweede keer uit cache


def test_zonder_tegels_nette_terugval(client, app, tmp_path):
    _route(app)
    with app.app_context():
        from app.models import FietsRoute as FR
        from app.services import statische_kaart as SK
        r = FR.query.filter_by(slug="sk-1").first()
        with patch.object(SK.requests, "get",
                          side_effect=RuntimeError("offline")):
            assert SK.kaart_bestand(r, map_=str(tmp_path),
                                    forceer=True) is None
    with patch("app.services.statische_kaart.kaart_bestand",
               return_value=None):
        resp = client.get("/fietsroutes/sk-1/kaartje.svg")
    assert resp.status_code == 200 and b"<svg" in resp.data
