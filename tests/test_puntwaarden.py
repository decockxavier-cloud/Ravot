"""Patch 180: veldstemmen waren onbeperkt te farmen (gerandomiseerde hash +
geen eigen dagplafond); puntwaarden zijn nu instelbaar."""
from zlib import crc32

from app.extensions import db
from app.models import Event, Family, Setting


def _opzet(app, aantal=20):
    with app.app_context():
        fam = Family(email="vs@t.be", postcode="8800")
        db.session.add(fam)
        db.session.flush()
        ids = []
        for i in range(aantal):
            e = Event(title=f"Speeltuin {i}", slug=f"pw{i}", source="osm",
                      ext_id=f"pw{i}", is_permanent=True, pending=False,
                      hidden=False, lat=50.9, lng=3.1, subtype="playground")
            db.session.add(e)
            db.session.flush()
            ids.append(e.id)
        db.session.commit()
        return fam.id, ids


def test_zelfde_stem_beloont_maar_een_keer(app):
    with app.app_context():
        fid, ids = _opzet(app, 2)
        from app.punten import ken_toe
        sleutel = ids[0] * 100 + crc32(b"toilet") % 100
        eerste = ken_toe(fid, "veld_stem", ref_id=sleutel)
        db.session.commit()
        tweede = ken_toe(fid, "veld_stem", ref_id=sleutel)
        db.session.commit()
        assert eerste == 3 and tweede == 0


def test_dagplafond_op_veldstemmen(app):
    with app.app_context():
        fid, ids = _opzet(app)
        from app.punten import ken_toe
        beloond = 0
        for eid in ids:
            if ken_toe(fid, "veld_stem", ref_id=eid * 100 + crc32(b"x") % 100):
                beloond += 1
            db.session.commit()
        assert beloond == 8          # standaard veldstem_dag_max


def test_puntwaarde_instelbaar(app):
    with app.app_context():
        fid, ids = _opzet(app, 2)
        db.session.add(Setting(key="punt_veld_stem", value="5"))
        db.session.add(Setting(key="veldstem_dag_max", value="0"))
        db.session.commit()
        from app.punten import ken_toe
        assert ken_toe(fid, "veld_stem", ref_id=ids[0] * 100 + 7) == 5


def test_niveauladder_is_uitdagender_en_instelbaar(app, client):
    """Patch 181: Vossenkoning moet een meerjarendoel zijn, geen kwestie van
    twintig uitstappen. De drempels zijn instelbaar."""
    from app import punten as pas
    with app.app_context():
        assert pas.niveau(600)["naam"] != "Vossenkoning"   # was vroeger wél
        assert pas.niveau(2500)["naam"] == "Vossenkoning"
        assert pas.niveau(100)["naam"] == "Speurneus"
        db.session.add(Setting(key="niveau_drempels", value="0,200,600,1500,4000"))
        db.session.commit()
        assert pas.niveau(1500)["naam"] == "Supervos"
        assert pas.niveau(3999)["naam"] != "Vossenkoning"
        # onzin-invoer valt terug op de standaard
        Setting.query.filter_by(key="niveau_drempels").delete()
        db.session.add(Setting(key="niveau_drempels", value="kapot"))
        db.session.commit()
        assert pas.niveau(2500)["naam"] == "Vossenkoning"


def test_dagplafonds_publiek_zichtbaar(client):
    h = client.get("/ravotscore").get_data(as_text=True)
    assert "60 punten per dag" in h
    assert "3 beloonde bezoeken per dag" in h
    assert "8 beloonde" in h
    assert "één keer per plek" in h
    v = client.get("/voorwaarden").get_data(as_text=True)
    assert "dagelijkse grenzen" in v and "/ravotscore" in v
