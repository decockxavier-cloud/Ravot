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
