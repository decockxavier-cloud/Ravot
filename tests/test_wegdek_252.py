"""Patch 252: wegdek en verkeersdrukte uit de open data van Toerisme Vlaanderen.

Verhard/onverhard vulden we met de hand in; autovrij bestond niet. Beide staan
per netwerksegment in dezelfde WFS die we al voor de knooppunten gebruiken.

De belangrijkste regel is eerlijkheid over dekking: de wegdeklaag dekt bijna
het hele netwerk, de verkeerslaag maar ongeveer een vijfde. Zonder deftige
dekking tonen we geen cijfer.
"""
from unittest.mock import patch

from argon2 import PasswordHasher

from app.extensions import db
from app.models import Admin, FietsRoute

GEO = [[50.9400 + i * 0.0005, 3.1200] for i in range(20)]


def _nep_wfs(url, **kw):
    laag = (kw.get("params") or {}).get("typeNames", "")

    class A:
        def raise_for_status(self):
            pass

        def json(self):
            veld = ("ground", "verhard") if "wegdek" in laag \
                else ("traffic", "autovrij")
            return {"features": [{
                "properties": {veld[0]: veld[1]},
                "geometry": {"type": "LineString",
                             "coordinates": [[3.12, 50.94], [3.12, 50.9500]]}}]}
    return A()


def _opzet(app, client):
    with app.app_context():
        db.session.add(Admin(email="wd@r.be", pw_hash=PasswordHasher().hash("x"),
                             role="admin", totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        db.session.add(FietsRoute(titel="Nieuw", slug="wd252-1", afstand_km=18,
                                  duur_min=110, moeilijkheid="vlak",
                                  is_lus=True, pending=False, hidden=False,
                                  geometrie=GEO,
                                  routebeschrijving="Knooppunten: 74 – 32"))
        db.session.add(FietsRoute(titel="Al gemeten", slug="wd252-2",
                                  afstand_km=12, duur_min=70,
                                  moeilijkheid="vlak", is_lus=True,
                                  pending=False, hidden=False, geometrie=GEO,
                                  verhard_pct=40,
                                  routebeschrijving="Knooppunten: 1 – 2"))
        db.session.commit()
        aid = Admin.query.filter_by(email="wd@r.be").first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"


def test_meting_vult_wegdek_en_autovrij(client, app):
    _opzet(app, client)
    import app.services.wegdek as W
    with patch.object(W.requests, "get", side_effect=_nep_wfs):
        client.post("/beheer/routes/meet-ondergrond", follow_redirects=True)
    with app.app_context():
        r = FietsRoute.query.filter_by(slug="wd252-1").first()
        assert r.verhard_pct == 100
        assert r.autovrij_pct == 100
        assert r.buggyvriendelijk is True        # ≥80% verhard


def test_bestaande_meting_blijft_tenzij_je_alles_vraagt(client, app):
    """Bestaande routes moeten bijgewerkt kúnnen worden, maar niet per ongeluk."""
    _opzet(app, client)
    import app.services.wegdek as W
    with patch.object(W.requests, "get", side_effect=_nep_wfs):
        h = client.post("/beheer/routes/meet-ondergrond",
                        follow_redirects=True).get_data(as_text=True)
    assert "1 overgeslagen" in h
    with app.app_context():
        assert FietsRoute.query.filter_by(slug="wd252-2").first().verhard_pct == 40
    with patch.object(W.requests, "get", side_effect=_nep_wfs):
        client.post("/beheer/routes/meet-ondergrond", data={"alles": "1"},
                    follow_redirects=True)
    with app.app_context():
        assert FietsRoute.query.filter_by(slug="wd252-2").first().verhard_pct == 100


def test_zonder_dekking_geen_cijfer(app):
    """Een percentage over vier vijfde onbekend terrein is een verzinsel."""
    import app.services.wegdek as W

    class Leeg:
        def raise_for_status(self):
            pass

        def json(self):
            return {"features": []}

    with patch.object(W.requests, "get", return_value=Leeg()):
        pct, dekking = W.meet_wegdek(GEO)
    assert pct is None and dekking == 0.0
    assert W.betrouwbaar(0.8) and not W.betrouwbaar(0.3)


def test_storing_laat_de_route_ongemoeid(client, app):
    _opzet(app, client)
    import app.services.wegdek as W
    with patch.object(W.requests, "get", side_effect=RuntimeError("wfs plat")):
        client.post("/beheer/routes/meet-ondergrond", follow_redirects=True)
    with app.app_context():
        r = FietsRoute.query.filter_by(slug="wd252-1").first()
        assert r.verhard_pct is None             # leeg, niet nul


def test_badges_verschijnen_op_de_routepagina(client, app):
    _opzet(app, client)
    import app.services.wegdek as W
    with patch.object(W.requests, "get", side_effect=_nep_wfs):
        client.post("/beheer/routes/meet-ondergrond", follow_redirects=True)
    h = client.get("/fietsroutes/wd252-1").get_data(as_text=True)
    assert "% verhard" in h
    assert "autovrij" in h
