"""Patch 172: uitnodigingen voor uitgelogde bezoekers op alle looppaden."""
from app.extensions import db
from app.models import Event, Family


def _plek(app):
    with app.app_context():
        db.session.add(Event(title="Speeltuin — Kerkstraat", slug="cta1",
                             source="osm", ext_id="cta1", is_permanent=True,
                             pending=False, hidden=False, lat=50.95, lng=3.12,
                             gemeente="Roeselare", postcode="8800",
                             subtype="playground", quality=80, curated=True))
        db.session.commit()


def test_landing_slot_cta_staat_hoger(client):
    h = client.get("/").get_data(as_text=True)
    i_cta = h.find("Klaar om te ravotten")
    i_stappen = h.find("Zo simpel gaat het")
    assert 0 < i_cta < i_stappen        # vóór de uitleg, niet helemaal onderaan
    assert "slot-herhaling" in h        # plus een herhaling voor doorlezers


def test_fiche_en_lijst_nodigen_uit(client, app):
    _plek(app)
    h = client.get("/e/cta1").get_data(as_text=True)
    assert "fiche-cta" in h and "Bewaar deze plek" in h
    h = client.get("/vandaag").get_data(as_text=True)
    assert "lijst-slot-cta" in h


def test_gast_krijgt_bewaaraanbod_zonder_blokkade(client, app):
    _plek(app)
    client.post("/proberen", data={"postcode": "8800", "birth_year": "2018",
                                   "radius": "25"}, follow_redirects=True)
    h = client.get("/vandaag").get_data(as_text=True)
    assert "bewaar-cta" in h                       # aanbod
    assert "Speeltuin — Kerkstraat" in h           # maar de lijst is zichtbaar


def test_ingelogd_gezin_ziet_geen_cta(client, app):
    _plek(app)
    with app.app_context():
        db.session.add(Family(email="cta@z.be", postcode="8800"))
        db.session.commit()
        fid = Family.query.filter_by(email="cta@z.be").first().id
    with client.session_transaction() as s:
        s["family_id"] = fid
    h = client.get("/vandaag").get_data(as_text=True)
    assert "lijst-slot-cta" not in h and "bewaar-cta" not in h
    assert "fiche-cta" not in client.get("/e/cta1").get_data(as_text=True)


def test_ravotpas_prominent_op_landing(client):
    """Patch 173: de Ravotpas is de sterkste reden voor een profiel en staat
    daarom als eigen blok bovenaan, niet verstopt tussen de features."""
    h = client.get("/").get_data(as_text=True)
    i_pas = h.find("ravotpas-blok")
    i_waarom = h.find("Waarom Ravot")
    assert 0 < i_pas < i_waarom
    assert "+15" in h and "+10" in h                 # concrete puntenwaarden
    assert "Welpje" in h and "Vossenkoning" in h     # de vosjes-niveaus
    assert "Start je Ravotpas" in h                  # eigen CTA
    assert h.count("De Ravotpas") == 1               # oude tegel verwijderd
