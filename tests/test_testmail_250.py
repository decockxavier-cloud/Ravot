"""Patch 250: mails proefdraaien vanuit het beheer.

Zonder dit moest je datums in de databank vervalsen om te zien hoe een mail
eruitziet. Nu: één knop, echte inhoud, naar je eigen adres — en het gezin dat
als voorbeeld dient krijgt niets en wordt niet gewijzigd.
"""
from unittest.mock import patch

from argon2 import PasswordHasher

from app.extensions import db
from app.models import (Admin, Child, Event, Family, PostcodeCentroid)


def _opzet(app, client, met_gezin=True):
    with app.app_context():
        db.session.add(PostcodeCentroid(postcode="8800", gemeente="Roeselare",
                                        lat=50.9464, lng=3.1233))
        db.session.add(Admin(email="beheer@ravot.be",
                             pw_hash=PasswordHasher().hash("x"), role="admin",
                             totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        for n in range(5):
            db.session.add(Event(
                title=f"Speeltuin {n}", slug=f"tm250-{n}", source="osm",
                ext_id=f"tm250-{n}", is_permanent=True, pending=False,
                hidden=False, lat=50.95, lng=3.13, gemeente="Roeselare",
                subtype="playground", quality=70))
        if met_gezin:
            f = Family(email="gezin@t.be", postcode="8800")
            db.session.add(f)
            db.session.flush()
            db.session.add(Child(family_id=f.id, birth_year=2018))
        db.session.commit()
        aid = Admin.query.filter_by(email="beheer@ravot.be").first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"


def test_welkomstmail_gaat_naar_de_beheerder(client, app):
    _opzet(app, client)
    verstuurd = []

    def nep(naar, onderwerp, html, text=None, **kw):
        verstuurd.append((naar, onderwerp, html))

    with patch("app.services.magic.send_mail", side_effect=nep):
        r = client.post("/beheer/test-mail/welkom", follow_redirects=True)
    assert verstuurd
    naar, onderwerp, html = verstuurd[0]
    assert naar == "beheer@ravot.be"
    assert onderwerp.startswith("[TEST]")        # niet te verwarren met echt
    assert "Speeltuin" in html                   # echte inhoud, geen dummy
    assert "kreeg niets" in r.get_data(as_text=True)


def test_voorbeeldgezin_wordt_niet_gewijzigd(client, app):
    """Anders zou testen de echte verzending van dat gezin blokkeren."""
    _opzet(app, client)
    with patch("app.services.magic.send_mail"):
        client.post("/beheer/test-mail/welkom", follow_redirects=True)
    with app.app_context():
        assert Family.query.first().welkomstmail_op is None


def test_zonder_gezin_nette_melding(client, app):
    _opzet(app, client, met_gezin=False)
    r = client.post("/beheer/test-mail/welkom", follow_redirects=True)
    assert "Nog geen enkel gezin" in r.get_data(as_text=True)


def test_onbekende_soort_bestaat_niet(client, app):
    _opzet(app, client)
    assert client.post("/beheer/test-mail/onzin").status_code == 404
