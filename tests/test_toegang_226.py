"""Patch 226: één inlogdeur, optionele geboortejaren, Google voor uitbaters,
en de meeneem-extra's van een route achter een gratis profiel."""
from app.extensions import db
from app.models import FietsRoute, Family, Operator, TrechterTeller


def _route(app):
    with app.app_context():
        r = FietsRoute(titel="Testlus", slug="tg-1", afstand_km=18,
                       duur_min=110, moeilijkheid="vlak", is_lus=True,
                       pending=False, hidden=False, gemeente="Roeselare",
                       start_lat=50.94, start_lng=3.12, gpx_bestand="x.gpx",
                       geometrie=[[50.94, 3.12], [50.95, 3.13], [50.94, 3.12]],
                       routebeschrijving="Knooppunten: 74 – 32")
        fam = Family(email="tg@t.be", postcode="8800")
        db.session.add_all([r, fam])
        db.session.commit()
        return fam.id


def test_extras_vragen_profiel_route_blijft_open(client, app):
    _route(app)
    for pad in ("/fietsroutes/tg-1/gpx", "/fietsroutes/tg-1/print",
                "/fietsroutes/tg-1/bingo"):
        h = client.get(pad).get_data(as_text=True)
        assert "gratis Ravotpas" in h, pad
    # de route zelf blijft volledig zichtbaar (SEO + waarde vóór de vraag);
    # er staat wel een vriendelijke hint bij de vergrendelde knoppen
    h = client.get("/fietsroutes/tg-1").get_data(as_text=True)
    assert "Testlus" in h
    assert "Knooppunten" in h                    # inhoud gewoon leesbaar
    assert "meenemen?" not in h                  # geen slotpagina
    assert "🔒" in h                              # wel eerlijk gelabeld


def test_ingelogd_gezin_krijgt_de_extras(client, app):
    fid = _route(app)
    with client.session_transaction() as s:
        s["family_id"] = fid
    assert "print-blad" in client.get(
        "/fietsroutes/tg-1/print").get_data(as_text=True)
    assert "bingo-vak" in client.get(
        "/fietsroutes/tg-1/bingo").get_data(as_text=True)


def test_slot_wordt_gemeten(client, app):
    _route(app)
    client.get("/fietsroutes/tg-1/print")
    with app.app_context():
        tel = {t.stap: t.aantal for t in TrechterTeller.query.all()}
        assert tel.get("slot_geraakt") == 1


def test_een_deur_voor_inloggen_en_aanmelden(client, app):
    h = client.get("/").get_data(as_text=True)
    assert "Inloggen of aanmelden" in h
    assert "Gratis profiel</a>" not in h        # geen twee losse knoppen meer


def test_geboortejaren_zijn_optioneel(client, app):
    with client.session_transaction() as s:
        s["pending_email"] = "nieuw@test.be"
    r = client.post("/mijn/start", data={"postcode": "8800"},
                    follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        fam = Family.query.filter_by(email="nieuw@test.be").first()
        assert fam is not None                  # profiel zonder geboortejaren
        assert fam.postcode == "8800"


def test_registratie_zonder_postcode(client, app):
    """p229 keert p226 om: ook de postcode is geen tolpoort meer. We vragen
    hem pas nadat iemand gezocht heeft — zie test_registratie_229."""
    with client.session_transaction() as s:
        s["pending_email"] = "geen-pc@test.be"
    client.post("/mijn/start", data={}, follow_redirects=True)
    with app.app_context():
        fam = Family.query.filter_by(email="geen-pc@test.be").first()
        assert fam is not None
        assert not fam.postcode


def test_uitbater_google_vraagt_bestaand_account(client, app):
    """Een zaak claimen blijft een bewuste stap: Google logt alleen bekende
    uitbaters in, het maakt er geen aan."""
    from unittest.mock import patch
    app.config["GOOGLE_CLIENT_ID"] = "test-id"
    app.config["GOOGLE_CLIENT_SECRET"] = "test-secret"
    with app.app_context():
        db.session.add(Operator(email="baas@zaak.be", bedrijfsnaam="Zaak BV",
                                active=True))
        db.session.commit()
        oid = Operator.query.filter_by(email="baas@zaak.be").first().id
    client.get("/uitbater/login/google")
    with client.session_transaction() as s:
        state = s["op_google_state"]
    with patch("app.services.google_login.email_uit_code",
               return_value="baas@zaak.be"):
        client.get(f"/uitbater/login/google/terug?code=a&state={state}")
    with client.session_transaction() as s:
        assert s.get("operator_id") == oid

    client.get("/uitbater/login/google")
    with client.session_transaction() as s:
        state = s["op_google_state"]
    with patch("app.services.google_login.email_uit_code",
               return_value="onbekend@zaak.be"):
        r = client.get(f"/uitbater/login/google/terug?code=a&state={state}",
                       follow_redirects=True)
    assert "geen uitbatersaccount" in r.get_data(as_text=True)
