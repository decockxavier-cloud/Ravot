"""Patch 98: horeca-fiches met nette openingsuren, typebadge en juiste knoptekst.

- Import zet uren voortaan enkel in het gestructureerde veld (urentabel op de
  fiche), nooit meer als ruwe tekst in de beschrijving.
- `flask backfill-openingsuren` kuist bestaande fiches op.
- De fiche toont een typebadge (bv. 🍴), en de kaartpopup zegt "Bekijk eetplek"
  voor horeca en "Bekijk plek" voor andere vaste plekken.
"""
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import Event


def _plek(**kw):
    basis = dict(is_permanent=True, gemeente="Merelbeke", postcode="9820",
                 lat=50.99, lng=3.75, age_min=0, age_max=12)
    basis.update(kw)
    ev = Event(**basis)
    db.session.add(ev)
    db.session.commit()
    return ev


def test_backfill_zet_ruwe_tekst_om(app):
    runner = app.test_cli_runner()
    with app.app_context():
        _plek(uit_id="98a", slug="bergstraat",
              title="De Bergstraat", subtype="horeca",
              description="Restaurant. Kindvriendelijke zaak met verschoontafel. "
                          "Openingsuren: Th-Sa 11:30-14:30,18:00-22:00; Su 11:30-14:30,18:00-20:30")
    uit = runner.invoke(args=["backfill-openingsuren"])
    assert "Gestructureerd gevuld: 1" in uit.output
    with app.app_context():
        ev = Event.query.filter_by(slug="bergstraat").first()
        assert "Openingsuren:" not in ev.description
        assert ev.description.startswith("Restaurant.")
        assert ev.openingsuren["do"] == [["11:30", "14:30"], ["18:00", "22:00"]]
        assert ev.openingsuren["zo"] == [["11:30", "14:30"], ["18:00", "20:30"]]
        assert ev.openingsuren.get("ma") is None or "ma" not in ev.openingsuren


def test_backfill_laat_onleesbare_tekst_staan(app):
    runner = app.test_cli_runner()
    with app.app_context():
        _plek(uit_id="98b", slug="vaag", title="Vaag",
              description="Leuke zaak. Openingsuren: Apr-Sep 08:30-20:00; Oct-Mar 08:30-18:30")
    uit = runner.invoke(args=["backfill-openingsuren"])
    assert "onleesbaar gelaten: 1" in uit.output
    with app.app_context():
        ev = Event.query.filter_by(slug="vaag").first()
        assert "Openingsuren:" in ev.description   # beter ruw dan niets
        assert not ev.openingsuren


def test_fiche_toont_typebadge_en_urentabel(client, app):
    with app.app_context():
        _plek(uit_id="98c", slug="eethuis", title="Eethuis", subtype="horeca",
              description="Kindvriendelijke zaak met kinderstoelen.",
              openingsuren={"ma": None, "di": ["11:00", "22:00"]})
    html = client.get("/e/eethuis").data.decode()
    assert "badge-type" in html
    assert "Openingsuren" in html and "Dinsdag" in html
    assert "Openingsuren:" not in html.replace("🕒 Openingsuren", "")


def test_markerdata_kent_eet_en_permanent(client, app):
    with app.app_context():
        _plek(uit_id="98d", slug="frietje", title="Frietje", subtype="horeca")
        nu = datetime.utcnow()
        db.session.add(Event(uit_id="98e", slug="voorstelling", title="Show",
                             start=nu + timedelta(days=1),
                             end=nu + timedelta(days=1, hours=2),
                             gemeente="Merelbeke", postcode="9820",
                             lat=50.99, lng=3.75))
        db.session.commit()
    import json, re
    html = client.get("/verkennen").data.decode()
    blok = re.search(r'<script type="application/json" id="map-data">(.*?)</script>',
                     html, re.S)
    assert blok, "map-data ontbreekt"
    markers = {m["title"]: m for m in json.loads(blok.group(1))["markers"]}
    assert markers["Frietje"]["eet"] is True
    assert markers["Frietje"]["permanent"] is True
    assert markers["Show"]["permanent"] is False


def test_statuscheck_openingsuren(app):
    with app.app_context():
        _plek(uit_id="98f", slug="p1", title="Met uren",
              openingsuren={"ma": ["09:00", "17:00"]})
        _plek(uit_id="98g", slug="p2", title="Ruw",
              description="Zaak. Openingsuren: Mo-Fr 09:00-17:00")
        from app.services.health import _openingsuren
        ok, detail = _openingsuren()
    assert ok is None            # zachte melding zolang er ruwe tekst is
    assert "backfill-openingsuren" in detail


# ---------------------------------------------------------------- patch 99 --

def test_parser_dagloze_uren_gelden_elke_dag(app):
    from app.services.openingsuren import parse_osm_uren
    r = parse_osm_uren("08:00-20:00")
    assert r["ma"] == [["08:00", "20:00"]] and r["zo"] == [["08:00", "20:00"]]
    r = parse_osm_uren("09:00-13:00,15:00-20:00")
    assert r["wo"] == [["09:00", "13:00"], ["15:00", "20:00"]]   # pauze blijft
    assert parse_osm_uren("Mo-Su,PH 08:00-18:00+")["za"] == [["08:00", "18:00"]]
    # Seizoens- en daglichtnotaties blijven bewust onparseerbaar
    assert parse_osm_uren("Apr-Sep 08:30-20:00; Oct-Mar 08:30-18:30") == {}
    assert parse_osm_uren("sunrise-sunset") == {}


def test_backfill_daglicht_en_afspraak_worden_zinnen(app):
    runner = app.test_cli_runner()
    with app.app_context():
        _plek(uit_id="99a", slug="speelplein-99",
              title="Speelplein", description="Speeltuin. Openingsuren: sunrise-sunset")
        _plek(uit_id="99b", slug="museum-99",
              title="Museumpje", description="Leuk. Openingsuren: \"op afspraak\"")
        _plek(uit_id="99c", slug="dicht-99",
              title="Dicht", description="Zaak. Openingsuren: off")
        _plek(uit_id="99d", slug="dagloos-99",
              title="Dagloos", description="Zaak. Openingsuren: 08:00-20:00")
    uit = runner.invoke(args=["backfill-openingsuren"])
    assert "omschreven (daglicht/afspraak): 2" in uit.output
    with app.app_context():
        s = Event.query.filter_by(slug="speelplein-99").first()
        assert "zonsopgang tot zonsondergang" in s.description
        assert "Openingsuren:" not in s.description
        m = Event.query.filter_by(slug="museum-99").first()
        assert "Enkel op afspraak." in m.description
        d = Event.query.filter_by(slug="dicht-99").first()
        assert d.description == "Zaak."
        dl = Event.query.filter_by(slug="dagloos-99").first()
        assert dl.openingsuren["zo"] == [["08:00", "20:00"]]
        assert "Openingsuren:" not in dl.description


# --------------------------------------------------------------- patch 100 --

def test_parser_bewaart_middagpauze(app):
    from app.services.openingsuren import parse_osm_uren
    r = parse_osm_uren("Th-Sa 11:30-14:30,18:00-22:00; Su 11:30-14:30,18:00-20:30")
    assert r["do"] == [["11:30", "14:30"], ["18:00", "22:00"]]
    assert r["zo"] == [["11:30", "14:30"], ["18:00", "20:30"]]
    # overlappende blokken worden samengevoegd
    assert parse_osm_uren("Mo 09:00-13:00,12:00-17:00")["ma"] == [["09:00", "17:00"]]


def test_status_dicht_tijdens_pauze(app):
    from datetime import datetime
    from app.services.openingsuren import status
    class Ev:
        openingsuren = {"wo": [["11:30", "14:30"], ["18:00", "22:00"]]}
    wo_pauze = datetime(2026, 7, 22, 16, 0)     # woensdag 16u
    wo_avond = datetime(2026, 7, 22, 19, 0)
    assert status(Ev(), nu=wo_pauze)[0] == "dicht"
    st, sluit = status(Ev(), nu=wo_avond)
    assert st == "open" and sluit == "22:00"


def test_oud_formaat_blijft_werken(app):
    from datetime import datetime
    from app.services.openingsuren import status, uren_overzicht
    class Ev:
        openingsuren = {"wo": ["09:00", "17:00"]}   # oud éénbloks-formaat
    assert status(Ev(), nu=datetime(2026, 7, 22, 10, 0))[0] == "open"
    assert ("Woensdag", "09:00–17:00") in uren_overzicht(Ev())


def test_fiche_toont_pauze(client, app):
    with app.app_context():
        _plek(uit_id="100a", slug="pauze-zaak", title="Pauzezaak", subtype="horeca",
              openingsuren={"do": [["11:30", "14:30"], ["18:00", "22:00"]],
                            "ma": None})
    html = client.get("/e/pauze-zaak").data.decode()
    assert "11:30–14:30 en 18:00–22:00" in html
    assert "gesloten" in html


def test_sync_ververst_uren_maar_niet_handmatige(app):
    from app.services.sources.base import upsert_event
    basis = dict(source="osm", ext_id="p100", title="Zaak", is_permanent=True,
                 gemeente="Gent", postcode="9000", lat=51.0, lng=3.7,
                 age_min=0, age_max=12, categories=[], indoor=True,
                 is_free=False, price_info=[], image_url=None, description="Zaak.",
                 start=None, end=None)
    with app.app_context():
        upsert_event({**basis, "openingsuren": {"ma": [["09:00", "17:00"]]}})
        db.session.commit()
        ev = Event.query.filter_by(ext_id="p100").first()
        # bron levert bij hersync betere uren mét pauze -> verversen
        upsert_event({**basis, "openingsuren":
                      {"ma": [["09:00", "12:00"], ["13:00", "17:00"]]}})
        db.session.commit()
        assert ev.openingsuren["ma"] == [["09:00", "12:00"], ["13:00", "17:00"]]
        # beheerder stelt handmatig in -> sync blijft eraf
        ev.openingsuren = {"ma": [["10:00", "16:00"]], "_handmatig": True}
        db.session.commit()
        upsert_event({**basis, "openingsuren": {"ma": [["08:00", "18:00"]]}})
        db.session.commit()
        assert ev.openingsuren["ma"] == [["10:00", "16:00"]]
