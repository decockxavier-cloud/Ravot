"""Patch 233: het welkomstscherm vraagt nog één ding.

Zeven velden vlak na het inloggen kostte meer registraties dan het opleverde —
en de meeste ervan (straal, categorieën, budget, weergavenaam) verfijnen het
portaal, terwijl mensen daar toch zelf zoeken.
"""
from app.extensions import db
from app.models import Family


def _nieuw(client):
    with client.session_transaction() as s:
        s["pending_email"] = "welkom@test.be"


def test_scherm_vraagt_alleen_de_postcode(client, app):
    _nieuw(client)
    h = client.get("/mijn/start").get_data(as_text=True)
    assert h.count('type="text"') == 1            # één invoerveld
    assert "Hoe ver willen jullie rijden" not in h
    assert "Budget" not in h
    assert "Naam voor je vrienden" not in h
    assert "Sla over" in h                        # en het mag ook zonder


def test_weekmail_is_echte_opt_in(client, app):
    """Een vooraangevinkt vakje is onder de GDPR geen geldige toestemming."""
    _nieuw(client)
    h = client.get("/mijn/start").get_data(as_text=True)
    assert 'name="newsletter" checked' not in h
    assert "Ja, stuur me donderdags" in h         # wel duidelijk aangeboden


def test_overslaan_maakt_gewoon_een_profiel(client, app):
    _nieuw(client)
    client.post("/mijn/start", data={"overslaan": "1"}, follow_redirects=True)
    with app.app_context():
        fam = Family.query.filter_by(email="welkom@test.be").first()
        assert fam is not None
        assert not fam.newsletter_opt_in          # niets stilzwijgend aan
        assert fam.radius_km == 25                # nette standaard
        assert fam.budget_pref == "all"


def test_postcode_en_mail_worden_bewaard(client, app):
    _nieuw(client)
    client.post("/mijn/start", data={"postcode": "8800", "newsletter": "on"},
                follow_redirects=True)
    with app.app_context():
        fam = Family.query.filter_by(email="welkom@test.be").first()
        assert fam.postcode == "8800"
        assert fam.newsletter_opt_in is True


def test_weggehaalde_velden_blijven_instelbaar(client, app):
    _nieuw(client)
    client.post("/mijn/start", data={"overslaan": "1"}, follow_redirects=True)
    with app.app_context():
        fid = Family.query.filter_by(email="welkom@test.be").first().id
    with client.session_transaction() as s:
        s["family_id"] = fid
    h = client.get("/mijn/instellingen").get_data(as_text=True)
    assert 'name="radius"' in h and 'name="budget"' in h
    assert 'name="display_name"' in h
