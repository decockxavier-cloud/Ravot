"""Patch 237: redactionele tekst per gemeente.

Gemeentepagina's waren volledig afgeleid uit de fiches — een lijst zonder
eigen woorden. Dat is precies waar een stadsdienst of gevestigde gids het
wint. Dit voegt de menselijke laag toe, per gemeente en optioneel.
"""
from argon2 import PasswordHasher

from app.extensions import db
from app.models import Admin, Event, GemeenteTekst


def _opzet(app, client):
    with app.app_context():
        db.session.add(Admin(email="gt@r.be", pw_hash=PasswordHasher().hash("x"),
                             role="admin", totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        for n in range(6):
            db.session.add(Event(
                title=f"P{n}", slug=f"gt237-{n}", source="osm",
                ext_id=f"gt237-{n}", is_permanent=True, pending=False,
                hidden=False, lat=50.94, lng=3.12, gemeente="Roeselare",
                subtype="playground", quality=70, description="Iets."))
        db.session.add(Event(title="Klein", slug="gt237-klein", source="osm",
                             ext_id="gt237-klein", is_permanent=True,
                             pending=False, hidden=False, lat=51.2, lng=3.2,
                             gemeente="Dorpje", subtype="playground",
                             quality=70))
        db.session.commit()
        aid = Admin.query.filter_by(email="gt@r.be").first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"


def test_werklijst_toont_waar_het_loont(client, app):
    _opzet(app, client)
    h = client.get("/beheer/gemeenteteksten").get_data(as_text=True)
    assert h.index("Roeselare") < h.index("Dorpje")   # meeste aanbod eerst
    assert "nog niets" in h


def test_tekst_schrijven_en_tonen(client, app):
    _opzet(app, client)
    client.post("/beheer/gemeenteteksten/roeselare", data={
        "intro_md": "In **Roeselare** ravot je het best in het Bergmolenbos.",
        "slot_md": "Parkeren doe je gratis aan de Kop van de Vaart.",
        "auteur": "Xavier"}, follow_redirects=True)
    with app.app_context():
        t = db.session.get(GemeenteTekst, "roeselare")
        assert t and t.heeft_tekst and t.auteur == "Xavier"
    pub = client.get("/roeselare").get_data(as_text=True)
    assert "Bergmolenbos" in pub and "<strong>Roeselare" in pub   # markdown
    assert "Kop van de Vaart" in pub
    assert pub.index("Bergmolenbos") < pub.index("Kop van de Vaart")


def test_pagina_zonder_tekst_blijft_gewoon_werken(client, app):
    _opzet(app, client)
    h = client.get("/dorpje").get_data(as_text=True)
    assert "gemeente-intro" not in h        # geen leeg blok
    assert "Klein" in h                     # de lijst staat er gewoon


def test_gemeente_met_tekst_komt_altijd_in_de_sitemap(client, app):
    """Waar bewust in geïnvesteerd is, hoort vindbaar te zijn — ook als het
    aanbod nog klein is."""
    _opzet(app, client)
    sm = client.get("/sitemap-gemeenten.xml").get_data(as_text=True)
    assert "/dorpje<" not in sm             # te dun, geen tekst
    client.post("/beheer/gemeenteteksten/dorpje",
                data={"intro_md": "Klein maar fijn."}, follow_redirects=True)
    sm = client.get("/sitemap-gemeenten.xml").get_data(as_text=True)
    assert "/dorpje<" in sm


def test_alleen_beheer_kan_schrijven(client, app):
    _opzet(app, client)
    with client.session_transaction() as s:
        s.clear()
    r = client.post("/beheer/gemeenteteksten/roeselare",
                    data={"intro_md": "Kwaadaardig."}, follow_redirects=False)
    assert r.status_code in (302, 401, 403)
    with app.app_context():
        assert db.session.get(GemeenteTekst, "roeselare") is None
