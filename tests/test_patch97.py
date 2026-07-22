"""Patch 97: meerdere e-mailadressen per gezinsaccount.

Hoofdadres blijft Family.email; extra adressen (FamilyMember) moeten zich
eerst via een gesigneerde maillink bevestigen, kunnen daarna inloggen met
hun eigen inlogcode en krijgen de gezinsmails mee (per adres uitschakelbaar).
"""
import re
from datetime import datetime, timedelta

import pytest
from itsdangerous import URLSafeTimedSerializer

from app.extensions import db
from app.models import (Child, Family, FamilyMember, find_family_by_email,
                        email_in_gebruik)


@pytest.fixture()
def gezin(app, client):
    with app.app_context():
        fam = Family(email="hoofd@t.be", postcode="8800",
                     newsletter_opt_in=True)
        db.session.add(fam)
        db.session.flush()
        db.session.add(Child(family_id=fam.id, birth_year=2019))
        db.session.commit()
        fid = fam.id
    with client.session_transaction() as s:
        s["family_id"] = fid
    return client, fid


def _mails(monkeypatch):
    gestuurd = []
    import app.routes.account as acc
    import app.services.magic as magic
    def vang(to, subject, html, text=None, headers=None):
        gestuurd.append((to, subject, text or html))
    monkeypatch.setattr(magic, "send_mail", vang)
    return gestuurd


def test_uitnodigen_bevestigen_en_inloggen(gezin, app, monkeypatch):
    client, fid = gezin
    gestuurd = _mails(monkeypatch)
    r = client.post("/mijn/gezinsleden/toevoegen",
                    data={"email": "partner@t.be"}, follow_redirects=True)
    assert r.status_code == 200
    assert gestuurd and gestuurd[0][0] == "partner@t.be"
    with app.app_context():
        lid = FamilyMember.query.filter_by(email="partner@t.be").first()
        assert lid is not None and not lid.bevestigd
        # Onbevestigd: nog géén toegang tot het gezin
        assert find_family_by_email("partner@t.be") is None
        lid_id = lid.id
    # Bevestigingslink uit de mail volgen
    link = re.search(r"https?://\S+/gezinslid/\S+", gestuurd[0][2]).group(0)
    pad = "/" + link.split("/", 3)[3]
    r = client.get(pad, follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        lid = db.session.get(FamilyMember, lid_id)
        assert lid.bevestigd
        assert find_family_by_email("partner@t.be").id == fid


def test_adres_kan_maar_bij_een_gezin(gezin, app, monkeypatch):
    client, _ = gezin
    _mails(monkeypatch)
    with app.app_context():
        ander = Family(email="ander@t.be", postcode="9000")
        db.session.add(ander)
        db.session.commit()
    r = client.post("/mijn/gezinsleden/toevoegen",
                    data={"email": "ander@t.be"}, follow_redirects=True)
    assert "al gekoppeld" in r.data.decode() or "hoofdadres" in r.data.decode()
    with app.app_context():
        assert FamilyMember.query.filter_by(email="ander@t.be").count() == 0
        assert email_in_gebruik("ander@t.be")


def test_vervalste_bevestigingslink_geweigerd(client, app):
    s = URLSafeTimedSerializer("verkeerde-sleutel", salt="gezinslid")
    r = client.get(f"/gezinslid/{s.dumps(1)}", follow_redirects=True)
    assert "klopt niet" in r.data.decode()


def test_loskoppelen(gezin, app, monkeypatch):
    client, fid = gezin
    _mails(monkeypatch)
    client.post("/mijn/gezinsleden/toevoegen", data={"email": "weg@t.be"})
    with app.app_context():
        lid_id = FamilyMember.query.filter_by(email="weg@t.be").first().id
    client.post(f"/mijn/gezinsleden/{lid_id}/verwijder", follow_redirects=True)
    with app.app_context():
        assert FamilyMember.query.filter_by(email="weg@t.be").count() == 0


def test_weekendmail_naar_alle_bevestigde_leden(gezin, app, monkeypatch):
    client, fid = gezin
    with app.app_context():
        db.session.add(FamilyMember(family_id=fid, email="mee@t.be",
                                    bevestigd=True, mail_aan=True))
        db.session.add(FamilyMember(family_id=fid, email="stil@t.be",
                                    bevestigd=True, mail_aan=False))
        db.session.add(FamilyMember(family_id=fid, email="nognietbev@t.be",
                                    bevestigd=False))
        db.session.commit()
        from app.services import weekendmail
        fam = db.session.get(Family, fid)
        picks = [{"event": type("E", (), {"title": "X", "gemeente": "Roeselare",
                                          "slug": "x", "id": 1})()}]
        monkeypatch.setattr(weekendmail, "top_events_for", lambda f: picks)
        monkeypatch.setattr(weekendmail, "render_template", lambda *a, **k: "x")
        naar = []
        with app.test_request_context():
            weekendmail.send_weekend_mail(fam, lambda to, *a, **k: naar.append(to))
    assert set(naar) == {"hoofd@t.be", "mee@t.be"}


def test_export_bevat_extra_adressen(gezin, app):
    client, fid = gezin
    with app.app_context():
        db.session.add(FamilyMember(family_id=fid, email="partner2@t.be",
                                    bevestigd=True))
        db.session.commit()
    data = client.get("/mijn/export").get_json()
    assert data["extra_adressen"][0]["email"] == "partner2@t.be"


def test_gdpr_verwijdering_neemt_leden_mee(gezin, app):
    client, fid = gezin
    with app.app_context():
        db.session.add(FamilyMember(family_id=fid, email="cascade@t.be"))
        db.session.commit()
        fam = db.session.get(Family, fid)
        db.session.delete(fam)
        db.session.commit()
        assert FamilyMember.query.filter_by(email="cascade@t.be").count() == 0
