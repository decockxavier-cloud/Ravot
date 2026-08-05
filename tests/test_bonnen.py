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


def test_webshoplogo_uploaden_via_beheer(app, client, tmp_path, monkeypatch):
    """Patch 175: het logo wordt geüpload in het beheer (niet via git) en
    verschijnt in de catalogus, het formulier én de bonmail."""
    import io
    from PIL import Image as _Im
    from app import media
    monkeypatch.setattr(media, "BON_LOGO_MAP", str(tmp_path / "bonlogos"))
    from app.models import Admin
    from argon2 import PasswordHasher
    with app.app_context():
        db.session.add(Admin(email="a@r.be", pw_hash=PasswordHasher().hash("x"),
                             totp_secret="JBSWY3DPEHPK3PXP", totp_confirmed=True))
        b = Beloning(naam="Cadeaubon", punten=300, waarde_eur=15.0, actief=True,
                     is_bon=True, bon_winkel="K'Bouter")
        db.session.add(b)
        db.session.commit()
        bid, aid = b.id, Admin.query.first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    logo = io.BytesIO()
    _Im.new("RGBA", (900, 300), (240, 130, 50, 255)).save(logo, "PNG")
    logo.seek(0)
    r = client.post("/beheer/beloningen", data={
        "actie": "bewerk", "bid": str(bid), "naam": "Cadeaubon",
        "punten": "300", "waarde": "15", "is_bon": "1",
        "bon_winkel": "K'Bouter", "bon_url": "www.k-bouter.be",
        "bon_logo_bestand": (logo, "kbouter.png")},
        content_type="multipart/form-data", follow_redirects=True)
    assert "bijgewerkt" in r.get_data(as_text=True)
    with _Im.open(f"{tmp_path}/bonlogos/{bid}.png") as im:
        assert im.size[0] <= 240 and im.size[1] <= 120
    assert client.get(f"/bonlogo/{bid}.png").status_code == 200
    assert f"/bonlogo/{bid}.png" in client.get("/beheer/beloningen").get_data(as_text=True)
    with app.app_context():
        # URL krijgt automatisch https:// en het logo is gemarkeerd als upload
        b = db.session.get(Beloning, bid)
        assert b.bon_url == "https://www.k-bouter.be"
        assert b.bon_logo == "upload"
