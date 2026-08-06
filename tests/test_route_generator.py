"""Patch 188: de routegenerator — lussen zoeken door het netwerk, scoren op
gezinsdichtheid uit de eigen databank, en de redactionele wachtrij."""
import math

from app.extensions import db
from app.models import (Admin, Event, FietsRoute, Knooppunt, NetwerkSegment,
                        RouteBuurt, RouteVoorstel)

BASIS = (50.946, 3.123)
KM_LAT = 1 / 111.0
KM_LNG = 1 / (111.0 * math.cos(math.radians(50.95)))


def _rasternet():
    """Synthetisch 4x4-knooppuntennet met mazen van ±1,6 km."""
    def punt(i, j):
        return [BASIS[1] + j * 1.6 * KM_LNG, BASIS[0] + i * 1.6 * KM_LAT]
    feats = []
    for i in range(4):
        for j in range(4):
            if j < 3:
                feats.append({"type": "Feature", "properties": {},
                              "geometry": {"type": "LineString",
                                           "coordinates": [punt(i, j),
                                                           punt(i, j + 1)]}})
            if i < 3:
                feats.append({"type": "Feature", "properties": {},
                              "geometry": {"type": "LineString",
                                           "coordinates": [punt(i, j),
                                                           punt(i + 1, j)]}})
    return {"type": "FeatureCollection", "features": feats}


def _plekken(app):
    for n, (di, dj, st) in enumerate([(0, 0, "playground"),
                                      (0, 2, "playground"),
                                      (2, 1, "horeca"), (1, 3, "playground"),
                                      (3, 3, "museum"), (2, 2, "horeca")]):
        db.session.add(Event(
            title=f"P{n}", slug=f"rg{n}", source="osm", ext_id=f"rg{n}",
            is_permanent=True, pending=False, hidden=False,
            gemeente="Roeselare", subtype=st, quality=60,
            categories=["buiten"] if st == "playground" else [],
            indoor=st != "playground",
            lat=BASIS[0] + di * 1.6 * KM_LAT + 0.001,
            lng=BASIS[1] + dj * 1.6 * KM_LNG + 0.001))
    db.session.commit()


def test_netwerk_laden_en_snappen(app):
    with app.app_context():
        from app.services.route_generator import laad_netwerk_uit_geojson
        knopen, segmenten = laad_netwerk_uit_geojson(_rasternet(), "test")
        assert knopen == 16 and segmenten == 24
        # elk knooppunt kreeg een nummer (K<id> bij nummerloze bron)
        assert all(k.nummer for k in Knooppunt.query.all())


def test_generator_vindt_en_rangschikt_lussen(app):
    with app.app_context():
        from app.services.route_generator import (genereer_voorstellen,
                                                  laad_netwerk_uit_geojson)
        laad_netwerk_uit_geojson(_rasternet(), "test")
        _plekken(app)
        bewaard, onderzocht = genereer_voorstellen("Roeselare", top=5)
        assert onderzocht > 20 and bewaard == 5
        rijen = (RouteVoorstel.query
                 .order_by(RouteVoorstel.score.desc()).all())
        assert len(rijen) == 5
        # binnen de ingestelde lusgrenzen en gerangschikt op score
        assert all(12 <= r.afstand_km <= 25 for r in rijen)
        assert rijen[0].score >= rijen[-1].score
        assert rijen[0].score_detail.get("ravotten", 0) > 0
        # nogmaals draaien: dubbele lussen worden niet opnieuw bewaard
        bewaard2, _ = genereer_voorstellen("Roeselare", top=5)
        assert RouteVoorstel.query.count() <= 10


def test_promotie_maakt_conceptroute_met_buurt(app):
    with app.app_context():
        from app.services.route_generator import (genereer_voorstellen,
                                                  laad_netwerk_uit_geojson,
                                                  promoveer)
        laad_netwerk_uit_geojson(_rasternet(), "test")
        _plekken(app)
        genereer_voorstellen("Roeselare", top=3)
        v = RouteVoorstel.query.order_by(RouteVoorstel.score.desc()).first()
        route = promoveer(v)
        assert route.pending is True                 # redactie eerst
        assert route.is_lus and route.afstand_km == v.afstand_km
        assert "Knooppunten:" in route.routebeschrijving
        assert RouteBuurt.query.filter_by(route_id=route.id).count() >= 2
        assert v.status == "gepromoveerd" and v.route_id == route.id


def test_beheerwachtrij(client, app):
    from argon2 import PasswordHasher
    with app.app_context():
        from app.services.route_generator import laad_netwerk_uit_geojson
        laad_netwerk_uit_geojson(_rasternet(), "test")
        _plekken(app)
        db.session.add(Admin(email="a@r.be", pw_hash=PasswordHasher().hash("x"),
                             role="admin", totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        db.session.commit()
        aid = Admin.query.first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    r = client.post("/beheer/route-voorstellen",
                    data={"actie": "genereer", "gemeente": "Roeselare"},
                    follow_redirects=True)
    h = r.get_data(as_text=True)
    assert "voorstellen bewaard" in h
    with app.app_context():
        vid = RouteVoorstel.query.order_by(
            RouteVoorstel.score.desc()).first().id
    r = client.post("/beheer/route-voorstellen",
                    data={"actie": "promoveer", "vid": str(vid)},
                    follow_redirects=True)
    assert "conceptroute" in r.get_data(as_text=True)
    with app.app_context():
        assert FietsRoute.query.filter_by(
            bron_naam="Ravot-routegenerator").count() == 1


def test_echte_knooppuntnummers_gekoppeld(app):
    """Patch 189: de knopenlaag levert de échte nummers, zodat je onderweg de
    bordjes kunt volgen in plaats van interne K-codes."""
    with app.app_context():
        from app.services.route_generator import (laad_netwerk_uit_geojson,
                                                  nummer_knopen_uit_geojson)
        laad_netwerk_uit_geojson(_rasternet(), "test")

        def punt(i, j):
            return [BASIS[1] + j * 1.6 * KM_LNG, BASIS[0] + i * 1.6 * KM_LAT]
        knopen_data = {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "properties": {"knooppuntnummer": str(10 + i * 4 + j)},
             "geometry": {"type": "Point", "coordinates": punt(i, j)}}
            for i in range(4) for j in range(4)]}
        assert nummer_knopen_uit_geojson(knopen_data) == 16
        assert all(not k.nummer.startswith("K")
                   for k in Knooppunt.query.all())


def test_top_is_divers_en_scores_onderscheiden(app):
    """Patch 189: geen top vol varianten van dezelfde lus, en in dicht gebied
    verzadigen de scores niet meer tot één waarde."""
    import random
    with app.app_context():
        from app.services.route_generator import (genereer_voorstellen,
                                                  laad_netwerk_uit_geojson)
        laad_netwerk_uit_geojson(_rasternet(), "test")
        random.seed(7)
        for k in range(30):
            st = random.choice(["playground", "playground", "horeca", "museum"])
            db.session.add(Event(
                title=f"D{k}", slug=f"dv{k}", source="osm", ext_id=f"dv{k}",
                is_permanent=True, pending=False, hidden=False,
                gemeente="Roeselare", subtype=st,
                quality=random.randint(20, 80),
                categories=["buiten"] if st == "playground" else [],
                indoor=st != "playground",
                lat=BASIS[0] + random.uniform(0, 4.8) * KM_LAT,
                lng=BASIS[1] + random.uniform(0, 4.8) * KM_LNG))
        db.session.commit()
        genereer_voorstellen("Roeselare", top=6)
        rijen = (RouteVoorstel.query
                 .order_by(RouteVoorstel.score.desc()).all())
        scores = [r.score for r in rijen]
        assert len(set(scores)) >= 4              # geen eenheidsworst
        a, b = set(rijen[0].knooppunten), set(rijen[1].knooppunten)
        assert len(a & b) / len(a | b) <= 0.6     # top-2 echt verschillend


def test_promotie_maakt_gpx_en_ai_naam(app, tmp_path, monkeypatch):
    """Patch 191: promoveren levert een downloadbaar GPX-bestand op en een
    speelse AI-naam/beschrijving op basis van wat er écht langs ligt."""
    import os
    from unittest.mock import patch as _patch
    with app.app_context():
        from app.services.route_generator import (genereer_voorstellen,
                                                  laad_netwerk_uit_geojson,
                                                  promoveer)
        laad_netwerk_uit_geojson(_rasternet(), "test")
        db.session.add(Event(
            title="IJssalon Fresco", slug="gpx-ijs", source="osm",
            ext_id="gpx-ijs", is_permanent=True, pending=False, hidden=False,
            gemeente="Roeselare", subtype="horeca", quality=60, indoor=True,
            lat=BASIS[0] + 0.001, lng=BASIS[1] + 0.001))
        _plekken(app)
        genereer_voorstellen("Roeselare", top=1)
        v = RouteVoorstel.query.first()
        nep = ("NAAM: De IJsjes-safari\n"
               "BESCHRIJVING: Langs IJssalon Fresco, met veel speelplezier.")
        with _patch("app.enrich._generate", return_value=nep) as m:
            r = promoveer(v)
        assert "IJssalon Fresco" in m.call_args[0][0]     # echte plekken in prompt
        assert r.titel == "De IJsjes-safari"
        assert "Knooppunten:" in r.routebeschrijving
        assert r.gpx_bestand and os.path.exists(
            f"/data/uploads/gpx/{r.gpx_bestand}")
        inhoud = open(f"/data/uploads/gpx/{r.gpx_bestand}").read()
        assert "<trkpt" in inhoud and "De IJsjes-safari" in inhoud


def test_promotie_overleeft_ai_storing(app):
    from unittest.mock import patch as _patch
    with app.app_context():
        from app.services.route_generator import (genereer_voorstellen,
                                                  laad_netwerk_uit_geojson,
                                                  promoveer)
        laad_netwerk_uit_geojson(_rasternet(), "test")
        _plekken(app)
        genereer_voorstellen("Roeselare", top=1)
        v = RouteVoorstel.query.first()
        with _patch("app.enrich._generate", side_effect=RuntimeError("uit")):
            r = promoveer(v)
        assert r.titel.startswith("Gezinslus")            # nette terugval
        assert r.gpx_bestand                              # GPX komt er toch


def test_beheer_gpx_download_ook_pending(client, app):
    from argon2 import PasswordHasher
    with app.app_context():
        from app.services.route_generator import (genereer_voorstellen,
                                                  laad_netwerk_uit_geojson,
                                                  promoveer)
        from unittest.mock import patch as _patch
        laad_netwerk_uit_geojson(_rasternet(), "test")
        _plekken(app)
        genereer_voorstellen("Roeselare", top=1)
        with _patch("app.enrich._generate", side_effect=RuntimeError("uit")):
            r = promoveer(RouteVoorstel.query.first())
        db.session.add(Admin(email="g@r.be", pw_hash=PasswordHasher().hash("x"),
                             role="admin", totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        db.session.commit()
        rid, aid = r.id, Admin.query.first().id
        assert r.pending is True
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    resp = client.get(f"/beheer/routes/{rid}/gpx")
    assert resp.status_code == 200
    assert "gpx" in resp.headers.get("Content-Disposition", "")
