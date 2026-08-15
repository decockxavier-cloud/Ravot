"""Patch 238: bereik meten per gemeente en streek, routeacties tellen, de
login-muur op routes schakelbaar maken en routes bewaarbaar.

Doel: bezoekers zijn het product dat aan partners verkocht wordt. Dan moet je
kunnen tonen waar er gekeken wordt — en mag een muur die bereik kost, weg.
"""
from argon2 import PasswordHasher

from app.extensions import db
from app.models import (Admin, Event, FietsRoute, Family, SavedRoute, Setting)


def _opzet(app):
    with app.app_context():
        db.session.add(Admin(email="br@r.be", pw_hash=PasswordHasher().hash("x"),
                             role="admin", totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        for gem, slug in (("Roeselare", "br-1"), ("Roeselare", "br-2"),
                          ("Genk", "br-3")):
            db.session.add(Event(title=slug, slug=slug, source="osm",
                                 ext_id=slug, is_permanent=True,
                                 pending=False, hidden=False, lat=50.94,
                                 lng=3.12, gemeente=gem, subtype="playground",
                                 quality=70, description="X"))
        db.session.add(FietsRoute(titel="Lus", slug="br-route", afstand_km=18,
                                  duur_min=110, moeilijkheid="vlak",
                                  is_lus=True, pending=False, hidden=False,
                                  gemeente="Roeselare", regio="Leiestreek",
                                  start_lat=50.94, start_lng=3.12,
                                  gpx_bestand="x.gpx",
                                  geometrie=[[50.94, 3.12], [50.95, 3.13]],
                                  routebeschrijving="Knooppunten: 74 – 32"))
        fam = Family(email="br@t.be", postcode="8800")
        db.session.add(fam)
        db.session.commit()
        return (fam.id, Admin.query.filter_by(email="br@r.be").first().id,
                FietsRoute.query.first().id)


def test_bezoeken_rollen_op_naar_gemeente_en_streek(client, app):
    fid, aid, rid = _opzet(app)
    client.get("/e/br-1")
    client.get("/e/br-1")
    client.get("/e/br-2")
    client.get("/e/br-3")
    with app.app_context():
        from app.statistiek import per_gemeente, per_streek
        gem = {g["gemeente"]: g for g in per_gemeente()}
        assert gem["Roeselare"]["bezoeken"] == 3
        assert gem["Roeselare"]["fiches"] == 2
        assert gem["Genk"]["bezoeken"] == 1
        streken = {s["streek"]: s for s in per_streek()}
        assert streken["Leiestreek"]["bezoeken"] == 3


def test_routeacties_worden_geteld(client, app):
    fid, aid, rid = _opzet(app)
    client.get("/fietsroutes/br-route")
    client.get("/fietsroutes/br-route/gpx")
    client.get("/fietsroutes/br-route/bingo")
    with app.app_context():
        from app.statistiek import route_cijfers
        r = route_cijfers()[0]
        assert r["bekeken"] == 1
        assert r["gpx"] == 1 and r["bingo"] == 1
        assert r["meenames"] == 2        # de maat voor "wordt echt gereden"


def test_muur_staat_standaard_aan(client, app):
    _opzet(app)
    slot = client.get("/fietsroutes/br-route/bingo").get_data(as_text=True)
    assert "gratis Ravotpas" in slot


def test_login_muur_is_schakelbaar(client, app):
    """Bereik telt zwaarder dan registraties: de muur moet uit kunnen.
    (Instelling vóór het eerste verzoek zetten — de settings-cache leeft per
    app-context, zie de testafspraak uit patch 181.)"""
    with app.app_context():
        db.session.add(Setting(key="routes_login_vereist", value="0"))
        db.session.commit()
    _opzet(app)
    vrij = client.get("/fietsroutes/br-route/bingo").get_data(as_text=True)
    assert "bingo-vak" in vrij                # muur uit: vrij te printen


def test_route_bewaren_en_terugvinden(client, app):
    fid, aid, rid = _opzet(app)
    with client.session_transaction() as s:
        s["family_id"] = fid
    client.post(f"/mijn/route-bewaar/{rid}", follow_redirects=True)
    with app.app_context():
        assert SavedRoute.query.filter_by(family_id=fid, route_id=rid).count() == 1
    h = client.get("/fietsroutes/br-route").get_data(as_text=True)
    assert "💚 Bewaard" in h
    dash = client.get("/mijn/profiel").get_data(as_text=True)
    assert "Bewaarde fietsroutes" in dash and "Lus" in dash
    # nog eens klikken haalt hem weer weg
    client.post(f"/mijn/route-bewaar/{rid}", follow_redirects=True)
    with app.app_context():
        assert SavedRoute.query.count() == 0


def test_bereikpagina_toont_de_verkoopcijfers(client, app):
    fid, aid, rid = _opzet(app)
    client.get("/e/br-1")
    client.get("/fietsroutes/br-route/gpx")
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    h = client.get("/beheer/bereik").get_data(as_text=True)
    assert "Roeselare" in h and "Leiestreek" in h
    assert "Meenames" in h and "Lus" in h
