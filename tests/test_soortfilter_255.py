"""Patch 255: meerdere soorten plek tegelijk kunnen kiezen.

Alle andere filters (categorie, praktisch) konden dit al; 'soort plek' was
als enige een keuzelijst met één optie — terwijl je hier juist wilt
combineren: "speeltuin OF kinderboerderij".
"""
import json
import re

from app.extensions import db
from app.models import Event


def _plekken(app):
    with app.app_context():
        for st in ("playground", "zoo", "museum", "park"):
            for i in range(3):
                db.session.add(Event(
                    title=f"{st} {i}", slug=f"sf255{st}{i}", source="osm",
                    ext_id=f"sf255{st}{i}", is_permanent=True, pending=False,
                    hidden=False, lat=51.0, lng=3.5, subtype=st, quality=70))
        db.session.commit()


def _uniek(client, url):
    h = client.get(url).get_data(as_text=True)
    return len(set(re.findall(r"/e/(sf255[a-z0-9]+)", h)))


def test_meerdere_soorten_in_de_lijst(client, app):
    _plekken(app)
    assert _uniek(client, "/ontdek?soort=playground") == 3
    assert _uniek(client, "/ontdek?soort=playground&soort=zoo") == 6
    assert _uniek(client, "/ontdek?soort=playground&soort=zoo&soort=museum") == 9


def test_meerdere_soorten_op_de_kaart(client, app):
    _plekken(app)
    assert _uniek(client, "/verkennen?soort=playground") == 3
    assert _uniek(client, "/verkennen?soort=playground&soort=zoo") == 6


def test_kaart_api_volgt_mee(client, app):
    _plekken(app)
    bbox = "n=51.5&z=50.5&w=3.0&o=4.0"
    d = json.loads(client.get(
        f"/api/kaart?{bbox}&soort=playground&soort=zoo").get_data(as_text=True))
    items = d if isinstance(d, list) else (d.get("items") or d.get("markers") or [])
    assert len(items) == 6


def test_chips_tonen_de_selectie(client, app):
    _plekken(app)
    h = client.get("/ontdek?soort=playground&soort=zoo").get_data(as_text=True)
    assert h.count("filterchip aan") >= 2
    assert "alle soorten" in h                 # wisknop verschijnt


def test_oude_links_met_een_soort_blijven_werken(client, app):
    """Gedeelde URL's en bestaande links mogen niet breken."""
    _plekken(app)
    assert client.get("/ontdek?soort=playground").status_code == 200
    assert _uniek(client, "/ontdek?soort=playground") == 3


def test_onzin_wordt_genegeerd(client, app):
    _plekken(app)
    assert _uniek(client, "/ontdek?soort=bestaatniet") == 12      # alles
    assert _uniek(client, "/ontdek?soort=playground&soort=bestaatniet") == 3
