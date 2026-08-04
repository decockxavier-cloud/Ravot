"""Patch 169: Search Console meldde 'ontbrekend veld startDate' — permanente
plekken kregen ten onrechte het Event-schema."""
import json
from datetime import datetime, timedelta

from app import seo
from app.extensions import db
from app.models import Event, Photo


def test_permanente_plek_is_geen_event(app):
    with app.app_context():
        sp = Event(title="Speeltuin — Kerkstraat", slug="sd1", source="osm",
                   ext_id="sd1", is_permanent=True, pending=False, hidden=False,
                   lat=50.95, lng=3.12, gemeente="Roeselare", postcode="8800",
                   adres="Kerkstraat", subtype="playground", is_free=True,
                   description="Fijne speeltuin.")
        db.session.add(sp)
        db.session.flush()
        db.session.add(Photo(event_id=sp.id, filename="x.jpg", soort="zaak",
                             status="approved"))
        db.session.commit()
        d = json.loads(seo.event_jsonld(sp, {"avg": 4.5, "count": 3}))
        assert d["@type"] == "Playground"
        assert "startDate" not in d          # de kritieke fout
        assert d["address"]["streetAddress"] == "Kerkstraat"
        assert "geo" in d and d["image"].endswith("/foto/1")
        assert d["aggregateRating"]["reviewCount"] == 3


def test_horeca_krijgt_restaurant_schema(app):
    with app.app_context():
        fr = Event(title="Frituur", slug="sd2", source="osm", ext_id="sd2",
                   is_permanent=True, pending=False, hidden=False, lat=50.9,
                   lng=3.1, gemeente="Roeselare", subtype="horeca")
        db.session.add(fr)
        db.session.commit()
        assert json.loads(seo.event_jsonld(fr))["@type"] == "Restaurant"


def test_gedateerd_event_heeft_alle_kernvelden(app):
    with app.app_context():
        ev = Event(title="Poppentheater", slug="sd3", source="uit", ext_id="sd3",
                   is_permanent=False, pending=False, hidden=False, lat=51.0,
                   lng=3.7, gemeente="Gent", postcode="9000", is_free=True,
                   start=datetime.utcnow() + timedelta(days=3))
        db.session.add(ev)
        db.session.commit()
        d = json.loads(seo.event_jsonld(ev))
        assert d["@type"] == "Event"
        assert d["startDate"] and d["endDate"]      # endDate afgeleid
        assert d["location"]["geo"]["latitude"] == 51.0
        assert d["description"]                     # nooit leeg
        assert d["offers"][0]["price"] == "0"       # gratis = geldige Offer
