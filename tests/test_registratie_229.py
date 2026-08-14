"""Patch 229: registratie zonder drempel + uitbatersoverzicht.

De postcode was de laatste tolpoort vóór de eerste ervaring. Nu vragen we hem
pas nadat iemand gezocht heeft — dan is het nut zichtbaar ("hoe ver is dit?").
"""
from argon2 import PasswordHasher

from app.extensions import db
from app.models import (Admin, Event, Family, Operator, OperatorClaim)


def test_registratie_lukt_zonder_postcode(client, app):
    with client.session_transaction() as s:
        s["pending_email"] = "zonderpc@test.be"
    client.post("/mijn/start", data={}, follow_redirects=True)
    with app.app_context():
        fam = Family.query.filter_by(email="zonderpc@test.be").first()
        assert fam is not None                   # geen tolpoort meer


def test_postcode_wordt_pas_gevraagd_na_een_zoekactie(client, app):
    with app.app_context():
        fam = Family(email="zoeker@test.be")
        db.session.add(fam)
        db.session.commit()
        fid = fam.id
    with client.session_transaction() as s:
        s["family_id"] = fid
    stil = client.get("/ontdek").get_data(as_text=True)
    assert "Waar wonen jullie" not in stil       # niet meteen zeuren
    na = client.get("/ontdek?q=Roeselare").get_data(as_text=True)
    assert "Waar wonen jullie" in na             # wél als het nut telt


def test_postcode_en_jaren_achteraf_invullen(client, app):
    with app.app_context():
        fam = Family(email="later@test.be")
        db.session.add(fam)
        db.session.commit()
        fid = fam.id
    with client.session_transaction() as s:
        s["family_id"] = fid
    client.post("/mijn/postcode", data={"postcode": "8800",
                                        "jaren": "2018 2021",
                                        "terug": "ontdek"},
                follow_redirects=True)
    with app.app_context():
        fam = db.session.get(Family, fid)
        assert fam.postcode == "8800"
        assert sorted(k.birth_year for k in fam.children) == [2018, 2021]


def test_gastpostcode_wordt_overgenomen(client, app):
    """Wie als gast al zocht, hoeft niet twee keer hetzelfde in te vullen."""
    with client.session_transaction() as s:
        s["guest"] = {"postcode": "8870"}
        s["pending_email"] = "gast@test.be"
    client.post("/mijn/start", data={}, follow_redirects=True)
    with app.app_context():
        fam = Family.query.filter_by(email="gast@test.be").first()
        assert fam.postcode == "8870"


def test_uitbatersoverzicht_toont_ook_niet_betalende(client, app):
    """De partnerpagina toont wie betaalt; deze toont wie zich registreerde —
    dat verschil is de verkooplijst."""
    with app.app_context():
        db.session.add(Admin(email="uo@r.be", pw_hash=PasswordHasher().hash("x"),
                             role="admin", totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        ev = Event(title="Frituur Test", slug="uo-1", source="user",
                   ext_id="uo-1", is_permanent=True, pending=False,
                   hidden=False, lat=51.0, lng=3.5, subtype="horeca",
                   indoor=True, gemeente="Roeselare")
        op1 = Operator(email="prospect@t.be", bedrijfsnaam="Prospect BV",
                       active=True)
        op2 = Operator(email="leeg@t.be", bedrijfsnaam="Niets BV", active=True)
        db.session.add_all([ev, op1, op2])
        db.session.flush()
        db.session.add(OperatorClaim(operator_id=op1.id, event_id=ev.id,
                                     status="approved"))
        db.session.commit()
        aid = Admin.query.filter_by(email="uo@r.be").first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    h = client.get("/beheer/uitbaters").get_data(as_text=True)
    assert "Prospect BV" in h and "Niets BV" in h
    assert "prospect</span>" in h                 # gelabeld als kans
    assert "Frituur Test" in h                    # met zijn geclaimde zaak
