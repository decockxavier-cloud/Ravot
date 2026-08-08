"""Patch 208: beheer-UX — sorteerbare routelijst, zijmenu dat openblijft,
en filterchips die verdoffen zonder onklikbaar te worden."""
from argon2 import PasswordHasher

from app.extensions import db
from app.models import Admin, FietsRoute


def _admin(app, client):
    with app.app_context():
        db.session.add(Admin(email="ux@r.be", pw_hash=PasswordHasher().hash("x"),
                             role="admin", totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        db.session.commit()
        aid = Admin.query.filter_by(email="ux@r.be").first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"


def _routes(app):
    with app.app_context():
        for t, sl, gem, reg, km in (
                ("Zeelus", "ux-1", "Oostende", "De Kust", 30.0),
                ("Aardlus", "ux-2", "Kasterlee", "Antwerpse Kempen", 10.0),
                ("Middenlus", "ux-3", "Roeselare", "Leiestreek", 20.0)):
            db.session.add(FietsRoute(titel=t, slug=sl, afstand_km=km,
                                      duur_min=90, moeilijkheid="vlak",
                                      is_lus=True, pending=False, hidden=False,
                                      gemeente=gem, regio=reg,
                                      start_lat=51.0, start_lng=3.5))
        db.session.commit()


def test_routelijst_sorteerbaar(client, app):
    _admin(app, client)
    _routes(app)
    h = client.get("/beheer/routes?sorteer=afstand").get_data(as_text=True)
    assert h.index("Aardlus") < h.index("Middenlus") < h.index("Zeelus")
    h2 = client.get("/beheer/routes?sorteer=afstand&omlaag=1").get_data(as_text=True)
    assert h2.index("Zeelus") < h2.index("Middenlus") < h2.index("Aardlus")
    h3 = client.get("/beheer/routes?sorteer=route").get_data(as_text=True)
    assert h3.index("Aardlus") < h3.index("Middenlus")     # alfabetisch
    assert "sorteer-kop" in h3                              # klikbare koppen


def test_zijmenu_blijft_open_op_fietsroutes(client, app):
    _admin(app, client)
    h = client.get("/beheer/routes").get_data(as_text=True)
    # de Content-groep moet open staan wanneer je op Fietsroutes zit
    i = h.index("<summary>Content</summary>")
    assert "open" in h[max(0, i - 120):i]


def test_filterchips_verdoffen_maar_blijven_klikbaar(client, app):
    _routes(app)
    import re

    def chip(naam, html):
        m = re.search(r'class="chip ([a-z]*)"[^>]*>' + re.escape(naam), html)
        return m.group(1) if m else "?"

    h = client.get("/fietsroutes?provincie=West-Vlaanderen").get_data(as_text=True)
    assert chip("Leiestreek", h) == "aan" and chip("De Kust", h) == "aan"
    assert chip("Antwerpse Kempen", h) == "uit"
    h2 = client.get("/fietsroutes?regio=Antwerpse Kempen").get_data(as_text=True)
    assert chip("Antwerpen", h2) == "aan"          # eigen provincie blijft aan
    assert chip("West-Vlaanderen", h2) == "uit"
    assert h2.count('href="/fietsroutes?regio=') >= 3   # alles klikbaar
