"""Patch 178: puntenlog per gezin (herkomst per punt), admincorrectie met
notitie, en juridische teksten over punten/bonnen."""
from app.extensions import db
from app.models import Admin, Event, Family, RavotPunt


def _opzet(app):
    from argon2 import PasswordHasher
    with app.app_context():
        db.session.add(Admin(email="adm@r.be", pw_hash=PasswordHasher().hash("x"),
                             role="admin", totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        fam = Family(email="log@t.be", postcode="8800")
        ev = Event(title="Speeltuin Ter Walle", slug="ptw", source="osm",
                   ext_id="ptw", is_permanent=True, pending=False, hidden=False,
                   lat=50.9, lng=3.1)
        db.session.add_all([fam, ev])
        db.session.flush()
        db.session.add(RavotPunt(family_id=fam.id, punten=10, reden="review",
                                 ref_id=ev.id))
        db.session.add(RavotPunt(family_id=fam.id, punten=15, reden="foto",
                                 ref_id=ev.id))
        db.session.commit()
        return fam.id, Admin.query.first().id


def test_log_toont_herkomst_per_punt(client, app):
    fid, aid = _opzet(app)
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    h = client.get(f"/beheer/families/{fid}").get_data(as_text=True)
    assert "Ravotscore gegeven" in h and "Foto goedgekeurd" in h
    assert "Speeltuin Ter Walle" in h and "/e/ptw" in h   # klikbare herkomst


def test_admin_kan_punten_toekennen_met_reden(client, app):
    fid, aid = _opzet(app)
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    r = client.post(f"/beheer/families/{fid}",
                    data={"actie": "punten", "aantal": "50",
                          "reden": "compensatie na storing"},
                    follow_redirects=True)
    h = r.get_data(as_text=True)
    assert "Correctie door beheer" in h and "compensatie na storing" in h
    with app.app_context():
        assert sum(p.punten for p in
                   RavotPunt.query.filter_by(family_id=fid).all()) == 75


def test_juridische_teksten_dekken_punten_en_bonnen(client):
    h = client.get("/voorwaarden").get_data(as_text=True)
    assert "Ravotpunten en beloningen" in h and "geen geldwaarde" in h
    assert "Cadeaubonnen" in h and "24 uur" in h and "één jaar geldig" in h
    p = client.get("/privacy").get_data(as_text=True)
    assert "Ravotpunten en beloningen" in p
    assert "geen naam, e-mailadres" in p          # wat we NIET doorgeven


def test_niveau_zakt_niet_bij_inwisselen_of_correctie(client, app):
    """Patch 179: Ravot belooft 'je niveau en badges blijven voor altijd'.
    Het niveau volgt daarom het hóógste totaal ooit, niet het actuele saldo."""
    from app import punten as pas
    fid, aid = _opzet(app)
    with app.app_context():
        for i in range(30):
            db.session.add(RavotPunt(family_id=fid, punten=10, reden="geweest",
                                     ref_id=i))
        db.session.commit()
        assert pas.niveau(pas.niveau_punten(fid))["naam"] == "Supervos"
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    client.post(f"/beheer/families/{fid}",
                data={"actie": "punten", "aantal": "-250", "reden": "test"},
                follow_redirects=True)
    with app.app_context():
        assert pas.totaal(fid) < 300                     # saldo zakt wél
        assert pas.niveau(pas.niveau_punten(fid))["naam"] == "Supervos"


def test_niveau_wel_terugzetbaar_bij_misbruik(client, app):
    from app import punten as pas
    fid, aid = _opzet(app)
    with app.app_context():
        for i in range(30):
            db.session.add(RavotPunt(family_id=fid, punten=10, reden="geweest",
                                     ref_id=i))
        db.session.commit()
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    client.post(f"/beheer/families/{fid}",
                data={"actie": "punten", "aantal": "-300", "reden": "misbruik",
                      "niveau_mee": "1"}, follow_redirects=True)
    with app.app_context():
        assert pas.niveau(pas.niveau_punten(fid))["naam"] == "Welpje"


def test_twee_correcties_kort_na_elkaar(client, app):
    """Vroeger botsten twee correcties binnen dezelfde seconde op de unieke
    index (family_id, reden, ref_id)."""
    fid, aid = _opzet(app)
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    for n in ("10", "20", "30"):
        r = client.post(f"/beheer/families/{fid}",
                        data={"actie": "punten", "aantal": n, "reden": "bonus"},
                        follow_redirects=True)
        assert r.status_code == 200
    with app.app_context():
        assert RavotPunt.query.filter_by(family_id=fid, reden="admin").count() == 3
