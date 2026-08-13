"""Patch 224: 'Ook leuk in de buurt' op de fiche.

Antwoord op de grootste val in de trechter: 64 sessies openden één fiche,
slechts 1 bekeek er drie. Een tweede klik moet aangeboden worden op het
moment dat iemand net iets bekeek.
"""
from datetime import timedelta

from app.extensions import db
from app.models import Event, utcnow

BASIS = (50.946, 3.123)


def _plekken(app):
    with app.app_context():
        tot = utcnow().replace(tzinfo=None) + timedelta(days=200)
        db.session.add(Event(title="Speeltuin Sterrebos", slug="bt-0",
                             source="osm", ext_id="bt-0", is_permanent=True,
                             pending=False, hidden=False, lat=BASIS[0],
                             lng=BASIS[1], subtype="playground", quality=70,
                             gemeente="Roeselare", categories=["buiten"]))
        db.session.add(Event(title="IJssalon Dichtbij", slug="bt-1",
                             source="osm", ext_id="bt-1", is_permanent=True,
                             pending=False, hidden=False, lat=BASIS[0] + 0.01,
                             lng=BASIS[1] + 0.01, subtype="horeca",
                             indoor=True, quality=60, gemeente="Roeselare"))
        db.session.add(Event(title="Partner Frituur", slug="bt-2",
                             source="osm", ext_id="bt-2", is_permanent=True,
                             pending=False, hidden=False, lat=BASIS[0] + 0.02,
                             lng=BASIS[1] + 0.02, subtype="horeca",
                             indoor=True, quality=50, gemeente="Roeselare",
                             partner_until=tot))
        db.session.add(Event(title="Museum Nabij", slug="bt-3", source="osm",
                             ext_id="bt-3", is_permanent=True, pending=False,
                             hidden=False, lat=BASIS[0] + 0.015, lng=BASIS[1],
                             subtype="museum", indoor=True, quality=65,
                             gemeente="Roeselare"))
        db.session.add(Event(title="Verre Speeltuin", slug="bt-4",
                             source="osm", ext_id="bt-4", is_permanent=True,
                             pending=False, hidden=False, lat=BASIS[0] + 0.9,
                             lng=BASIS[1], subtype="playground", quality=90,
                             gemeente="Tielt", categories=["buiten"]))
        db.session.add(Event(title="Geschrapte Plek", slug="bt-5",
                             source="osm", ext_id="bt-5", is_permanent=True,
                             pending=False, hidden=True, lat=BASIS[0] + 0.005,
                             lng=BASIS[1], subtype="playground", quality=90))
        db.session.commit()


def _blok(client):
    h = client.get("/e/bt-0").get_data(as_text=True)
    if "Ook leuk in de buurt" not in h:
        return ""
    return h[h.index("Ook leuk in de buurt"):][:2000]


def test_toont_plekken_dichtbij(client, app):
    _plekken(app)
    blok = _blok(client)
    assert blok
    assert "IJssalon Dichtbij" in blok or "Museum Nabij" in blok
    assert blok.count("buurt-kaart") <= 3          # 2 à 3, geen lijst
    assert "Speeltuin Sterrebos" not in blok       # nooit zichzelf


def test_partner_krijgt_voorrang_en_is_herkenbaar(client, app):
    _plekken(app)
    blok = _blok(client)
    assert "Partner Frituur" in blok
    assert "⭐ Partner" in blok                     # eerlijk gelabeld


def test_ver_weg_en_geschrapt_blijven_weg(client, app):
    _plekken(app)
    blok = _blok(client)
    assert "Verre Speeltuin" not in blok           # buiten de straal
    assert "Geschrapte Plek" not in blok           # verborgen telt niet


def test_zonder_buren_geen_leeg_blok(client, app):
    with app.app_context():
        db.session.add(Event(title="Eenzame Plek", slug="bt-solo",
                             source="osm", ext_id="bt-solo",
                             is_permanent=True, pending=False, hidden=False,
                             lat=49.5, lng=2.5, subtype="playground",
                             quality=50))
        db.session.commit()
    h = client.get("/e/bt-solo").get_data(as_text=True)
    assert "Ook leuk in de buurt" not in h
