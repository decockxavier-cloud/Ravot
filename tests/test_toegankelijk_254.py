"""Patch 254: toegankelijkheid uit OSM, en een geslaagde sync wist zijn fout.

Twee dingen die elkaar niet raken maar samen zijn gevonden:

- SyncStatus hield een oude foutmelding vast na een geslaagde run. Daardoor
  leek de OSM-sync maanden kapot terwijl hij vannacht nog gewoon draaide.
- wheelchair=no gooiden we weg, terwijl "niet toegankelijk" een gezin een
  vergeefse rit bespaart. En de verwerking stond op drie plekken.
"""
from app.extensions import db
from app.models import SyncStatus


def _fiche(app, tags):
    with app.app_context():
        from app.services.sources.osm import normalise
        return normalise({"type": "node", "id": 1, "lat": 51.0, "lon": 3.5,
                          "tags": {"leisure": "playground", "name": "T", **tags}})


def test_geslaagde_run_wist_de_vorige_fout(app):
    with app.app_context():
        from app.services.sources import _set_status
        _set_status("osm", "error", error="oude fout")
        assert db.session.get(SyncStatus, "osm").last_error == "oude fout"
        _set_status("osm", "done", result="4982 verwerkt")
        rij = db.session.get(SyncStatus, "osm")
        assert rij.last_error is None
        assert rij.last_result == "4982 verwerkt"


def test_echte_fout_blijft_wel_staan(app):
    with app.app_context():
        from app.services.sources import _set_status
        _set_status("osm", "done", result="ok")
        _set_status("osm", "error", error="netwerk plat")
        assert db.session.get(SyncStatus, "osm").last_error == "netwerk plat"


def test_toegankelijk_ja_en_nee_worden_allebei_bewaard(app):
    ja = _fiche(app, {"wheelchair": "yes"})
    assert ja.get("toegankelijk") is True
    assert ja.get("buggy_ok") is True
    nee = _fiche(app, {"wheelchair": "no"})
    assert nee.get("toegankelijk") is False       # bespaart een vergeefse rit


def test_onzekerheid_blijft_leeg(app):
    """'limited' is geen oordeel waar we op willen gokken."""
    beperkt = _fiche(app, {"wheelchair": "limited"})
    assert "toegankelijk" not in beperkt


def test_speeloppervlak_zegt_niets_over_toegankelijkheid(app):
    """Bij een speeltuin duw je de buggy enkele meters en zet je hem aan de
    kant — surface=sand maakt de plek niet onbereikbaar."""
    for grond in ("sand", "grass", "woodchips", "paved"):
        uit = _fiche(app, {"surface": grond})
        assert "toegankelijk" not in uit
        assert "buggy_ok" not in uit


def test_negatieve_badge_op_de_fiche(client, app):
    from app.models import Event
    with app.app_context():
        db.session.add(Event(title="Niet toegankelijk", slug="tg254",
                             source="osm", ext_id="tg254", is_permanent=True,
                             pending=False, hidden=False, lat=51.0, lng=3.5,
                             subtype="playground", toegankelijk=False))
        db.session.commit()
    h = client.get("/e/tg254").get_data(as_text=True)
    assert "niet toegankelijk" in h
