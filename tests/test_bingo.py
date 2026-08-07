"""Patch 200: fietsbingo — afdrukbare kaart per route per maand, met een
wedstrijdflow (upload, moderatie, punten)."""
import io

from PIL import Image

from app.extensions import db
from app.models import (Admin, BingoInzending, Event, FietsRoute, Family,
                        RavotPunt, RouteBuurt)


def _opzet(app):
    from argon2 import PasswordHasher
    with app.app_context():
        db.session.add(Admin(email="bg@r.be", pw_hash=PasswordHasher().hash("x"),
                             role="admin", totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        r = FietsRoute(titel="Knuffelgeitenronde", slug="bg-route",
                       afstand_km=18.0, duur_min=110, moeilijkheid="vlak",
                       is_lus=True, pending=False, hidden=False,
                       gemeente="Roeselare", start_lat=50.946, start_lng=3.123,
                       geometrie=[[50.946, 3.123], [50.95, 3.13]],
                       routebeschrijving="Knooppunten: 74 – 32 – 2 – 85 – 17")
        sp = Event(title="Speeltuin", slug="bg-s", source="osm", ext_id="bg-s",
                   is_permanent=True, pending=False, hidden=False, lat=50.95,
                   lng=3.13, subtype="playground", categories=["buiten"])
        fr = Event(title="Frituur Smulbox", slug="bg-f", source="osm",
                   ext_id="bg-f", is_permanent=True, pending=False,
                   hidden=False, lat=50.951, lng=3.129, subtype="horeca",
                   indoor=True)
        fam = Family(email="bingo@t.be", postcode="8800")
        db.session.add_all([r, sp, fr, fam])
        db.session.flush()
        db.session.add(RouteBuurt(route_id=r.id, event_id=sp.id,
                                  afstand_m=120, route_km=6.2))
        db.session.add(RouteBuurt(route_id=r.id, event_id=fr.id,
                                  afstand_m=200, route_km=11.0))
        db.session.commit()
        return fam.id, Admin.query.filter_by(email="bg@r.be").first().id


def _foto():
    b = io.BytesIO()
    Image.new("RGB", (900, 700), (200, 180, 90)).save(b, "JPEG")
    b.seek(0)
    return b


def test_bingokaart_is_afdrukbaar_en_eerlijk(client, app):
    _opzet(app)
    h = client.get("/fietsroutes/bg-route/bingo").get_data(as_text=True)
    assert h.count('class="bingo-vak"') == 16
    assert "knooppuntbordje" in h                  # uit de route zelf
    assert "glijbaan" in h and "ijsje of frietje" in h   # uit echte data
    assert "Smulbox" not in h                     # geen zaaknamen (partnerregel)
    assert "Print of bewaar als PDF" in h
    assert "Gratis gezinsprofiel" in h            # gast ziet wedstrijd-CTA


def test_kaart_wisselt_per_maand(app):
    _opzet(app)
    with app.app_context():
        from app.services.bingo import items_voor_route
        r = FietsRoute.query.filter_by(slug="bg-route").first()
        juli = items_voor_route(r, 202607)
        aug = items_voor_route(r, 202608)
        assert juli == items_voor_route(r, 202607)   # deterministisch
        assert juli != aug                           # maar vers per maand


def test_upload_en_goedkeuring_met_punten(client, app):
    fid, aid = _opzet(app)
    with client.session_transaction() as s:
        s["family_id"] = fid
    r1 = client.post("/fietsroutes/bg-route/bingo",
                     data={"kaart": (_foto(), "k.jpg")},
                     content_type="multipart/form-data", follow_redirects=True)
    assert "bingokaart is binnen" in r1.get_data(as_text=True)
    r2 = client.post("/fietsroutes/bg-route/bingo",
                     data={"kaart": (_foto(), "k2.jpg")},
                     content_type="multipart/form-data", follow_redirects=True)
    assert "al ingestuurd" in r2.get_data(as_text=True)   # 1 per maand
    with client.session_transaction() as s:
        s.pop("family_id", None)
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    with app.app_context():
        bid = BingoInzending.query.first().id
    client.post("/beheer/bingo", data={"bid": str(bid), "actie": "goed"},
                follow_redirects=True)
    with app.app_context():
        p = RavotPunt.query.filter_by(family_id=fid, reden="bingo").all()
        assert len(p) == 1 and p[0].punten == 15
        assert BingoInzending.query.first().status == "goed"
