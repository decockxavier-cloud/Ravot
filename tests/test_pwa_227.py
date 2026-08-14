"""Patch 227: PWA-consistentie — manifest met snelkoppelingen, een tabbar die
klopt voor gast én gezin, en een servicewerker die niets persoonlijks bewaart.
"""
import json
import re

from app.extensions import db
from app.models import Family, Setting


def test_manifest_heeft_snelkoppelingen_en_identiteit(client, app):
    with app.app_context():
        db.session.add(Setting(key="routes_in_menu", value="1"))
        db.session.commit()
    m = json.loads(client.get("/manifest.webmanifest").get_data(as_text=True))
    namen = [s["short_name"] for s in m["shortcuts"]]
    assert "Vandaag" in namen and "Kaart" in namen and "Fietsroutes" in namen
    assert m["id"] == "/" and m["scope"] == "/"      # stabiele app-identiteit
    assert any(i.get("purpose") == "maskable" for i in m["icons"])
    assert m["lang"] == "nl-BE"


def test_snelkoppeling_volgt_de_schakelaar(client, app):
    m = json.loads(client.get("/manifest.webmanifest").get_data(as_text=True))
    namen = [s["short_name"] for s in m["shortcuts"]]
    assert "Fietsroutes" not in namen              # rubriek staat uit


def test_tabbar_spreekt_gast_en_gezin_juist_aan(client, app):
    h = client.get("/").get_data(as_text=True)
    tabs = re.findall(r'tab-tekst">([^<]*)', h)
    assert "Aanmelden" in tabs                     # gast: eerlijke tekst
    assert "Mijn Ravot" not in tabs
    assert h.count("🏠") == 1                       # één huisje: Home bovenaan

    with app.app_context():
        fam = Family(email="pwa@t.be", postcode="8800")
        db.session.add(fam)
        db.session.commit()
        fid = fam.id
    with client.session_transaction() as s:
        s["family_id"] = fid
    h = client.get("/", follow_redirects=True).get_data(as_text=True)
    tabs = re.findall(r'tab-tekst">([^<]*)', h)
    assert "Mijn Ravot" in tabs and "Aanmelden" not in tabs


def test_serviceworker_bewaart_niets_persoonlijks():
    """Op een gedeelde tablet mag een volgende gebruiker geen profielpagina's
    of afgeschermde downloads uit de cache kunnen opvragen."""
    with open("app/static/sw.js", encoding="utf-8") as f:
        src = f.read()
    assert "magInCache" in src
    # de paden staan als regex met escapes: \/mijn\/ enzovoort
    for pad in ("mijn", "beheer", "uitbater", "login", "gpx", "bingo", "print"):
        assert pad in src, pad
    assert "ravot-v5" in src                       # nieuwe cachenaam = verse start
