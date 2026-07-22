"""Patch 94: rustige bewaarknop, werkende/wegklikbare installatietip.

Achtergrond: de gele btn-zon op een niet-bewaarde kaart oogde als 'al geliked',
en de installatieknop was door een CSS-conflict ([hidden] verloor van .btn)
op elk toestel zichtbaar terwijl enkel Chrome/Android er iets mee deed.
"""
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import Event, Family, Child


@pytest.fixture()
def gezin_met_event(app, client):
    with app.app_context():
        fam = Family(email="g@t.be", postcode="8800")
        db.session.add(fam)
        db.session.flush()
        db.session.add(Child(family_id=fam.id, birth_year=2019))
        nu = datetime.utcnow()
        ev = Event(uit_id="p94", slug="p94-ev", title="Speelnamiddag",
                   start=nu + timedelta(hours=3), end=nu + timedelta(hours=5),
                   gemeente="Roeselare", postcode="8800")
        db.session.add(ev)
        db.session.commit()
        fid, eid = fam.id, ev.id
    with client.session_transaction() as s:
        s["family_id"] = fid
    return client, eid


def test_onbewaarde_kaart_geen_gele_bewaarknop(gezin_met_event):
    client, _ = gezin_met_event
    html = client.get("/vandaag").data.decode()
    # De niet-bewaarde toestand hoort btn-stil te zijn, nooit btn-zon.
    for regel in html.splitlines():
        if "🤍 Bewaar" in regel or ("Bewaar" in regel and "bewaar/" in regel):
            assert "btn-zon" not in regel
    assert "🤍 Bewaar" in html
    assert 'btn-stil' in html


def test_detailpagina_zelfde_logica(gezin_met_event):
    client, _ = gezin_met_event
    html = client.get("/e/p94-ev").data.decode()
    kop = [r for r in html.splitlines() if "🤍 Bewaar" in r]
    assert kop, "bewaarknop ontbreekt op detailpagina"
    assert all("btn-zon" not in r for r in kop)


def test_bewaarde_toestand_actie_aan(gezin_met_event):
    client, eid = gezin_met_event
    import re
    html = client.get("/e/p94-ev").data.decode()
    tok = re.search(r'name="csrf_token" value="([^"]+)"', html)
    client.post(f"/mijn/bewaar/{eid}",
                data={"csrf_token": tok.group(1)} if tok else {})
    html = client.get("/e/p94-ev").data.decode()
    kop = [r for r in html.splitlines() if "❤️ Bewaard" in r]
    assert kop and all("actie-aan" in r for r in kop)


def test_install_rij_start_verborgen_en_heeft_uitleg(client, app):
    html = client.get("/vandaag").data.decode()
    assert 'id="install-rij"' in html and "hidden" in \
        html.split('id="install-rij"')[0].rsplit("<div", 1)[1] + "hidden"
    assert 'id="install-ios-uitleg"' in html
    assert "Zet op beginscherm" in html


def test_css_hidden_wint_altijd():
    css = open("app/static/css/ravot.css").read()
    assert "[hidden] { display: none !important; }" in css
