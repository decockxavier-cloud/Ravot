"""Tests voor de OSM/Overture opening_hours-parser."""
from app.services.openingsuren import parse_osm_uren


def test_eenvoudig_dagbereik():
    r = parse_osm_uren("Mo-Fr 09:00-17:00")
    assert r["ma"] == ["09:00", "17:00"]
    assert r["vr"] == ["09:00", "17:00"]
    assert "za" not in r and "zo" not in r


def test_meerdere_blokken_en_zaterdag():
    r = parse_osm_uren("Mo-Fr 09:00-17:00; Sa 10:00-14:00")
    assert r["za"] == ["10:00", "14:00"]
    assert r["ma"] == ["09:00", "17:00"]


def test_24_7():
    r = parse_osm_uren("24/7")
    assert all(r[d] == ["00:00", "23:59"] for d in
               ["ma", "di", "wo", "do", "vr", "za", "zo"])


def test_gesloten_dag():
    r = parse_osm_uren("Tu-Su 10:00-18:00; Mo off")
    assert r["ma"] is None
    assert r["di"] == ["10:00", "18:00"]


def test_losse_dagen():
    r = parse_osm_uren("Mo,We,Fr 14:00-20:00")
    assert set(k for k, v in r.items() if v) == {"ma", "wo", "vr"}


def test_meerdere_tijdsblokken_ruimste():
    # ochtend + namiddag -> ruimste opening (vroegste open, laatste sluit)
    r = parse_osm_uren("Mo-Fr 09:00-12:00,13:00-18:00")
    assert r["ma"] == ["09:00", "18:00"]


def test_feestdagen_genegeerd():
    r = parse_osm_uren("Mo-Fr 08:00-17:00; PH off")
    assert r["ma"] == ["08:00", "17:00"]
    assert "PH" not in r


def test_onleesbaar_geeft_leeg():
    assert parse_osm_uren("sunrise-sunset") == {}
    assert parse_osm_uren("op afspraak") == {}
    assert parse_osm_uren("") == {}
    assert parse_osm_uren(None) == {}


def test_24_uur_notatie():
    # 24:00 wordt 23:59 (geldige tijd)
    r = parse_osm_uren("Mo-Su 06:00-24:00")
    assert r["ma"] == ["06:00", "23:59"]


def test_sync_respecteert_bestaande_uren(app):
    """De sync vult openingsuren als eerste gok in, maar overschrijft nooit
    bestaande (uitbater/beheerder) uren."""
    from app import db
    from app.models import Event
    from app.services.sources.base import upsert_event
    with app.app_context():
        # 1) verse zaak zonder uren -> bron vult in
        data = {"source": "osm", "ext_id": "oh-test-1", "title": "Cafe A",
                "is_permanent": True, "lat": 51.0, "lng": 3.7,
                "gemeente": "Gent", "postcode": "9000",
                "age_min": 0, "age_max": 12, "categories": ["horeca"],
                "openingsuren": {"ma": ["09:00", "17:00"]}}
        upsert_event(data)
        db.session.commit()
        ev = Event.query.filter_by(source="osm", ext_id="oh-test-1").first()
        assert ev.openingsuren == {"ma": ["09:00", "17:00"]}
        # 2) uitbater zet eigen uren
        ev.openingsuren = {"ma": ["08:00", "22:00"], "zo": None}
        db.session.commit()
        # 3) hersync met andere bron-uren -> mag NIET overschrijven
        data["openingsuren"] = {"ma": ["10:00", "16:00"]}
        upsert_event(data)
        db.session.commit()
        db.session.refresh(ev)
        assert ev.openingsuren == {"ma": ["08:00", "22:00"], "zo": None}
