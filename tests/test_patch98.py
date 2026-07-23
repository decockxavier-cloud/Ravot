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
        assert ev.openingsuren["do"] == ["11:30", "22:00"]  # ruimste blok
        assert ev.openingsuren["zo"] == ["11:30", "20:30"]
        assert ev.openingsuren.get("ma") is None or "ma" not in ev.openingsuren


def test_backfill_laat_onleesbare_tekst_staan(app):
    runner = app.test_cli_runner()
    with app.app_context():
        _plek(uit_id="98b", slug="vaag", title="Vaag",
              description="Leuke zaak. Openingsuren: sunrise-sunset PH off")
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
