"""Patch 243: geen belofte die niet klopt.

Ravot zei "zonder reclame", maar heeft uitgelichte partners die daarvoor
betalen. Tegenover een overheidsdienst is dat verschil belangrijk: als ze het
later ontdekken, ben je je krediet kwijt.
"""


def test_over_pagina_legt_het_verdienmodel_uit(client, app):
    h = client.get("/over").get_data(as_text=True)
    assert "Hoe Ravot betaald wordt" in h
    assert "Partner" in h
    assert "nooit" in h                     # geen invloed op score of volgorde
    assert "zonder reclame" not in h        # te absolute claim weg


def test_aanmeldpagina_belooft_niet_te_veel(client, app):
    h = client.get("/login").get_data(as_text=True)
    assert "zonder reclame" not in h
    assert "bannerreclame" in h             # wél waar: geen banners of pop-ups


def test_gemeentemail_is_open_over_partners(client, app):
    """Een dienst toerisme moet weten waar ze aan begint."""
    from argon2 import PasswordHasher
    from app.extensions import db
    from app.models import Admin, Event
    with app.app_context():
        db.session.add(Admin(email="eh@r.be", pw_hash=PasswordHasher().hash("x"),
                             role="admin", totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        db.session.add(Event(title="S", slug="eh-1", source="osm",
                             ext_id="eh-1", is_permanent=True, pending=False,
                             hidden=False, lat=50.94, lng=3.12,
                             gemeente="Roeselare", subtype="playground"))
        db.session.commit()
        aid = Admin.query.filter_by(email="eh@r.be").first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    h = client.get("/beheer/gemeentecontacten/roeselare").get_data(as_text=True)
    assert "betaald partnerschap" in h
    assert "Xavier Decock" in h              # juiste schrijfwijze
    assert "Xavier De Cock" not in h
