"""Patch 232: Mijn Ravot toont alleen wat vrijgegeven is, en alle
gezinsinstellingen staan bij elkaar in plaats van half hier, half daar."""
from app.extensions import db
from app.models import Child, Family, Setting


def _gezin(app, client, **kw):
    with app.app_context():
        fam = Family(email="mr@t.be", postcode="8800", **kw)
        db.session.add(fam)
        db.session.flush()
        db.session.add(Child(family_id=fam.id, birth_year=2018))
        db.session.commit()
        fid = fam.id
    with client.session_transaction() as s:
        s["family_id"] = fid
    return fid


def test_uitgeschakelde_functies_worden_niet_getoond(client, app):
    """Een gezin dat 'Plan een feestje' aanklikt terwijl de functie uitstaat,
    botst op een 404 — dus tonen we hem niet."""
    _gezin(app, client)
    h = client.get("/mijn/profiel").get_data(as_text=True)
    assert "Plan een feestje" not in h
    assert "Verjaardagsfeestje" not in h


def test_feestjes_verschijnen_zodra_vrijgegeven(client, app):
    _gezin(app, client)
    with app.app_context():
        db.session.add(Setting(key="feestjes_aan", value="1"))
        db.session.commit()
    h = client.get("/mijn/profiel").get_data(as_text=True)
    assert "Plan een feestje" in h


def test_gezinsblok_toont_de_instellingen_in_een_oogopslag(client, app):
    _gezin(app, client, newsletter_opt_in=True)
    h = client.get("/mijn/profiel").get_data(as_text=True)
    assert "8800" in h                     # postcode
    assert "kinderen" in h                 # aantal kinderen
    assert "weekmail" in h                 # mailvoorkeur
    assert "⚙️ Instellingen" in h          # één duidelijke ingang


def test_instellingenpagina_bundelt_alles(client, app):
    _gezin(app, client)
    h = client.get("/mijn/instellingen").get_data(as_text=True)
    assert "Ons gezin" in h                # postcode, kinderen, straal
    assert "Mails van Ravot" in h          # eigen kop voor de mails
    assert "Wie zit er mee" in h           # gezinsleden
    assert "Mijn Ravot" in h               # terug-link naar het dashboard
