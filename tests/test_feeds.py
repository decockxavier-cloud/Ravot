"""Agenda-feeds (iCal/RSS) + geo-uitbreiding BE/NL/FR."""
from datetime import datetime
from app.services.sources import feeds
from app.models import Feed


ICAL = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:evt-1@ccdespil
SUMMARY:Kindervoorstelling De Kleine Prins
DTSTART:20260920T140000
DTEND:20260920T153000
LOCATION:Grote Zaal, Roeselare
DESCRIPTION:Een poppentheater voor kinderen van 4 tot 10.
URL:https://despil.be/e/1
END:VEVENT
BEGIN:VEVENT
UID:evt-2@ccdespil
SUMMARY:
DTSTART:20260921T100000
END:VEVENT
END:VCALENDAR"""


def test_ical_parsing(app):
    with app.app_context():
        f = Feed(id=1, naam="CC De Spil", url="x", kind="ical",
                 gemeente="Roeselare", postcode="8800", categorie="cultuur", trusted=True)
        items = list(feeds._uit_ical(ICAL, f))
        assert len(items) == 1                      # lege SUMMARY overgeslagen? nee: hier 1 met titel, 1 zonder
        # de tweede zonder titel wordt door normalise geweigerd, maar _uit_ical
        # levert enkel items met een summary
        item = items[0]
        assert "Kleine Prins" in item["title"]
        assert item["start"] == datetime(2026, 9, 20, 14, 0)


def test_feed_normalise_zet_gemeente_en_pending(app):
    with app.app_context():
        f = Feed(id=2, naam="CC Test", url="x", kind="ical",
                 gemeente="Gent", postcode="9000", categorie="cultuur", trusted=False)
        item = {"ext_id": "2:a", "title": "Iets", "start": datetime(2026, 9, 1),
                "end": None, "location": "Zaal", "url": None, "description": ""}
        data = feeds.normalise((f, item))
        assert data["source"] == "feed" and data["gemeente"] == "Gent"
        assert data["pending"] is True             # niet-vertrouwde feed -> wachtrij
        assert data["categories"] == ["cultuur"]


def test_feed_zonder_datum_geweigerd(app):
    with app.app_context():
        f = Feed(id=3, naam="X", url="x", kind="rss", trusted=True)
        assert feeds.normalise((f, {"ext_id": "3:a", "title": "Zonder datum",
                                    "start": None})) is None


def test_plaatsen_dekt_nl_en_fr():
    from app.plaatsen import PLAATSEN, PLAATS_LAND
    namen = {p[1] for p in PLAATSEN}
    assert "Gent" in namen and "Breda" in namen and "Lille" in namen
    # Lille 59000 = Frankrijk
    lille_fr = [p for p in PLAATSEN if p[1] == "Lille" and p[0] == "59000"]
    assert lille_fr and PLAATS_LAND["59000"] == "FR"


def test_autocomplete_toont_buitenland_met_vlag(client, app):
    d = client.get("/api/plaatsen?q=breda").get_json()
    assert any(s["gemeente"] == "Breda" and s["land"] == "NL" for s in d["suggesties"])
    d2 = client.get("/api/plaatsen?q=59000").get_json()
    assert any(s["postcode"] == "59000" for s in d2["suggesties"])


def test_geo_resolveert_5cijfer_franse_postcode(app):
    from app import geo
    with app.app_context():
        coord = geo.zoek_centrum("Lille (59000)")
        assert coord is not None and abs(coord[0] - 50.6) < 0.3
