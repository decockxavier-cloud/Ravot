"""Patch 174: cadeaubonnen — unieke code, 24 u verwerkingstijd, 1 jaar geldig,
mail in huisstijl naar het gezin en een melding naar de webshop."""
from datetime import datetime
from unittest.mock import patch

import pytest

from app.extensions import db
from app.models import Beloning, Family, Inwissel, RavotPunt, Setting


@pytest.fixture
def bon_opzet(app):
    with app.app_context():
        db.session.add(Setting(key="beloningen_aan", value="1"))
        db.session.add(Setting(key="wissel_min_dagen", value="0"))
        fam = Family(email="gezin@test.be", postcode="8800")
        b = Beloning(naam="Cadeaubon www.k-bouter.be", punten=300,
                     waarde_eur=15.0, actief=True, is_bon=True,
                     bon_winkel="K'Bouter", bon_url="https://www.k-bouter.be",
                     bon_logo="kbouter.png", bon_mail="info@k-bouter.be",
                     voorraad=5)
        db.session.add_all([fam, b])
        db.session.flush()
        for i in range(31):
            db.session.add(RavotPunt(family_id=fam.id, punten=10,
                                     reden="review", ref_id=i))
        db.session.commit()
        return fam.id, b.id


def test_bon_inwisselen_geeft_code_en_geldigheid(client, app, bon_opzet):
    fid, bid = bon_opzet
    with client.session_transaction() as s:
        s["family_id"] = fid
    with patch("app.bonnen.send_mail"):
        r = client.post(f"/mijn/beloningen/{bid}/wissel", data={},
                        follow_redirects=True)
    h = r.get_data(as_text=True)
    assert "cadeaubon" in h.lower() and "24 uur" in h
    with app.app_context():
        iw = Inwissel.query.first()
        kern = iw.code.split("-")[1]
        assert len(kern) >= 8
        assert not set("IO01") & set(kern)      # geen verwarrende tekens
        nu = datetime.utcnow()
        va = iw.geldig_vanaf.replace(tzinfo=None)
        tot = iw.geldig_tot.replace(tzinfo=None)
        assert 23 < (va - nu).total_seconds() / 3600 < 25
        assert (tot - va).days == 365
        assert db.session.get(Beloning, bid).voorraad == 4


def test_twee_mails_met_huisstijl(client, app, bon_opzet):
    fid, bid = bon_opzet
    verstuurd = []
    with client.session_transaction() as s:
        s["family_id"] = fid
    with patch("app.bonnen.send_mail",
               side_effect=lambda to, subj, html, txt=None, **k:
               verstuurd.append((to, subj, html))):
        client.post(f"/mijn/beloningen/{bid}/wissel", data={},
                    follow_redirects=True)
    assert len(verstuurd) == 2
    gezin = [m for m in verstuurd if m[0] == "gezin@test.be"][0]
    winkel = [m for m in verstuurd if m[0] == "info@k-bouter.be"][0]
    assert "vosje.png" in gezin[2] and "kbouter.png" in gezin[2]
    assert "24 uur verwerkingstijd" in gezin[2]
    assert "niet combineerbaar" in gezin[2]
    assert "Cadeaubon aanmaken" in winkel[1] and "15.00" in winkel[2]


def test_codes_zijn_uniek(app):
    with app.app_context():
        from app.bonnen import maak_code
        assert len({maak_code() for _ in range(300)}) == 300
