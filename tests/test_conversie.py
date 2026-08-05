"""Patch 183: conversie naar gezinsprofiel — puntentaal op de fiche, voordelen
op de aanmeldpagina en een Ravotpas-herinnering in de weekmail. Bewust géén
extra knoppen: dezelfde plekken, een sterkere reden."""
from flask import render_template

from app.extensions import db
from app.models import Beloning, Event, Family, RavotPunt, Setting


def test_fiche_cta_spreekt_puntentaal(client, app):
    with app.app_context():
        db.session.add(Event(title="Speeltuin", slug="cv1", source="osm",
                             ext_id="cv1", is_permanent=True, pending=False,
                             hidden=False, lat=50.9, lng=3.1,
                             subtype="playground"))
        db.session.commit()
    h = client.get("/e/cv1").get_data(as_text=True)
    assert "Verdien ravotpunten op deze plek" in h
    assert "+10" in h and "+15" in h                  # echte puntwaarden
    assert h.count("Verdien ravotpunten →") == 1      # één knop, geen stapel
    assert "Maak een gratis profiel om te bewaren" not in h   # oude dubbele weg


def test_aanmeldpagina_legt_voordelen_uit(client):
    h = client.get("/login").get_data(as_text=True)
    assert "Wat krijg je met een gezinsprofiel" in h
    assert "Ravotpunten" in h and "Vossenkoning" in h
    assert "weekmail" in h.lower()


def test_weekmail_bevat_ravotpas_herinnering(app):
    with app.app_context():
        db.session.add(Setting(key="beloningen_aan", value="1"))
        fam = Family(email="wm@t.be", postcode="8800")
        db.session.add(fam)
        db.session.flush()
        for i in range(12):
            db.session.add(RavotPunt(family_id=fam.id, punten=10,
                                     reden="review", ref_id=i))
        db.session.add(Beloning(naam="Cadeaubon", punten=300, waarde_eur=15.0,
                                actief=True, is_bon=True))
        db.session.commit()
        from app.services.weekendmail import pas_blok
        blok = pas_blok(fam)
        assert blok["saldo"] == 120 and blok["naam"] == "Speurneus"
        assert blok["beloning"].naam == "Cadeaubon"
        with app.test_request_context():
            html = render_template("mail/weekendmail.html", family=fam, picks=[],
                                   blogartikel=None, pas=blok, unsub_url="#",
                                   site="https://ravot.be")
        assert "Jullie Ravotpas" in html and "Speurneus" in html
        assert "Cadeaubon" in html and "Ravotter" in html


def test_geen_pasblok_als_beloningen_uit(app):
    with app.app_context():
        from app.models import wis_settings_cache
        fam = Family(email="uit@t.be", postcode="8800")
        db.session.add(fam)
        db.session.add(Setting(key="beloningen_aan", value="0"))
        db.session.commit()
        wis_settings_cache()
        from app.services.weekendmail import pas_blok
        assert pas_blok(fam) is None


def test_login_terug_parameter_is_slash_vrij(client, app):
    """Patch 185: NPM's Block Common Exploits geeft 403 op paden in de
    querystring; knoppen sturen daarom een slash-vrij token mee."""
    import re
    with app.app_context():
        db.session.add(Event(title="Speeltuin", slug="npm2", source="osm",
                             ext_id="npm2", is_permanent=True, pending=False,
                             hidden=False, lat=50.9, lng=3.1,
                             subtype="playground"))
        db.session.commit()
    h = client.get("/e/npm2").get_data(as_text=True)
    m = re.search(r'href="([^"]*login[^"]*)"[^>]*>Verdien ravotpunten', h)
    url = m.group(1).replace("&amp;", "&")
    assert "?" in url and "/" not in url.split("?")[1]     # geen pad in query
    client.get(url)
    with client.session_transaction() as s:
        assert s.get("na_login") == "/e/npm2"


def test_login_terug_feestje_en_misbruik(client):
    client.get("/login?terug=feestje")
    with client.session_transaction() as s:
        assert s.get("na_login") == "/mijn/feestje/nieuw"
    fresh = client
    with fresh.session_transaction() as s:
        s.pop("na_login", None)
    fresh.get("/login?terug=..%2F..%2Fetc")
    with fresh.session_transaction() as s:
        assert s.get("na_login") is None
