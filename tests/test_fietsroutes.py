"""Patch 160: gezinsfietsroutes — GPX-verwerking, koppeling, publiek en beheer."""
import io
import math
from datetime import datetime, timedelta

from app.extensions import db
from app.models import Event, FietsRoute, RouteBuurt


def _gpx_lus():
    uit = ('<?xml version="1.0"?>'
           '<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>')
    for i in range(120):
        h = 2 * math.pi * i / 120
        uit += (f'<trkpt lat="{50.95 + 0.02*math.sin(h):.6f}" '
                f'lon="{3.12 + 0.03*math.cos(h):.6f}"><ele>10</ele></trkpt>')
    return uit + "</trkseg></trk></gpx>"


def test_gpx_autovelden(app):
    with app.app_context():
        from app.services import routes_gis as g
        pts = g.parse_gpx(_gpx_lus().encode())
        st = g.route_stats(pts)
        assert 10 < st["afstand_km"] < 15
        assert st["is_lus"] is True
        assert st["bbox_n"] > st["bbox_z"]
        assert len(g.vereenvoudig(pts)) < len(pts)


def test_koppeling_met_partnerzoom(app):
    with app.app_context():
        from app.services import routes_gis as g
        pts = g.parse_gpx(_gpx_lus().encode())
        st = g.route_stats(pts)
        r = FietsRoute(titel="T", slug="t-lus", pending=False,
                       geometrie=g.vereenvoudig(pts),
                       **{k: st[k] for k in ("afstand_km", "bbox_n", "bbox_z",
                                             "bbox_o", "bbox_w", "start_lat",
                                             "start_lng", "eind_lat", "eind_lng",
                                             "is_lus")})
        dichtbij = Event(title="Speeltuin", slug="sp1", source="osm", ext_id="a",
                         is_permanent=True, pending=False, hidden=False,
                         lat=50.9701, lng=3.12, subtype="speeltuin")
        partner_600m = Event(title="IJs", slug="ijs1", source="user",
                             is_permanent=True, pending=False, hidden=False,
                             lat=50.9755, lng=3.12, subtype="horeca",
                             partner_until=datetime.utcnow() + timedelta(days=99),
                             partner_plan="partner")
        gewoon_600m = Event(title="Frituur", slug="fr1", source="osm", ext_id="b",
                            is_permanent=True, pending=False, hidden=False,
                            lat=50.9755, lng=3.125, subtype="horeca")
        db.session.add_all([r, dichtbij, partner_600m, gewoon_600m])
        db.session.commit()
        n = g.koppel_route(r)
        ids = {b.event_id for b in RouteBuurt.query.all()}
        assert dichtbij.id in ids
        assert partner_600m.id in ids       # ruimere partnergrens (800 m)
        assert gewoon_600m.id not in ids    # gewone grens (400 m)
        assert n == 2


def test_publieke_pagina_en_gpx(client, app):
    with app.app_context():
        from app.services import routes_gis as g
        pts = g.parse_gpx(_gpx_lus().encode())
        st = g.route_stats(pts)
        r = FietsRoute(titel="Lusje", slug="lusje", pending=False,
                       geometrie=g.vereenvoudig(pts), regio="Leiestreek",
                       afstand_km=st["afstand_km"], duur_min=80,
                       **{k: st[k] for k in ("bbox_n", "bbox_z", "bbox_o",
                                             "bbox_w", "start_lat", "start_lng",
                                             "eind_lat", "eind_lng", "is_lus")})
        db.session.add(r)
        db.session.commit()
    h = client.get("/fietsroutes").get_data(as_text=True)
    assert "Lusje" in h and "Leiestreek" in h
    h = client.get("/fietsroutes/lusje").get_data(as_text=True)
    assert 'id="route-data"' in h and "TouristTrip" in h
    assert client.get("/fietsroutes/lusje/gpx").status_code == 404  # geen bestand


def test_pending_route_publiek_onzichtbaar(client, app):
    with app.app_context():
        db.session.add(FietsRoute(titel="C", slug="concept", pending=True))
        db.session.commit()
    assert client.get("/fietsroutes/concept").status_code == 404
    assert "concept" not in client.get("/fietsroutes").get_data(as_text=True)


def test_ravotscore_op_route(client, app):
    from app.models import Family, RouteReview
    with app.app_context():
        f1 = Family(email="s1@t.be", postcode="8800")
        f2 = Family(email="s2@t.be", postcode="8800")
        r = FietsRoute(titel="Scorelus", slug="scorelus", pending=False,
                       afstand_km=10, duur_min=60)
        db.session.add_all([f1, f2, r])
        db.session.commit()
        f1id, f2id, rid = f1.id, f2.id, r.id
    with client.session_transaction() as s:
        s["family_id"] = f1id
    rv = client.post(f"/mijn/route-review/{rid}",
                     data={"kid_score": "5", "parent_score": "3"},
                     follow_redirects=True)
    assert "Bedankt" in rv.get_data(as_text=True)
    # tweede poging van hetzelfde gezin wijzigt niets
    client.post(f"/mijn/route-review/{rid}",
                data={"kid_score": "1", "parent_score": "1"})
    with client.session_transaction() as s:
        s["family_id"] = f2id
    client.post(f"/mijn/route-review/{rid}",
                data={"kid_score": "4", "parent_score": "2"})
    with app.app_context():
        revs = RouteReview.query.filter_by(route_id=rid).all()
        assert len(revs) == 2
        assert {x.kid_score for x in revs} == {5, 4}
    h = client.get("/fietsroutes/scorelus").get_data(as_text=True)
    assert "4.5" in h and "2 gezinnen" in h
    assert "aggregateRating" in h


def test_leuk_onderweg_inklap(client, app):
    import math
    with app.app_context():
        from app.services.routes_gis import koppel_route
        geom = [[50.95 + 0.02 * math.sin(2 * math.pi * i / 40),
                 3.12 + 0.03 * math.cos(2 * math.pi * i / 40)] for i in range(40)]
        r = FietsRoute(titel="Vollus", slug="vollus", pending=False,
                       geometrie=geom, bbox_n=50.97, bbox_z=50.93,
                       bbox_o=3.15, bbox_w=3.09, start_lat=50.95,
                       start_lng=3.15, eind_lat=50.95, eind_lng=3.15)
        db.session.add(r)
        for i in range(9):
            db.session.add(Event(title=f"P{i}", slug=f"kp{i}", source="osm",
                                 ext_id=f"k{i}", is_permanent=True,
                                 pending=False, hidden=False, lat=50.9699,
                                 lng=3.112 + i * 0.002, subtype="speeltuin"))
        db.session.commit()
        n = koppel_route(r)
        assert n > 6
    h = client.get("/fietsroutes/vollus").get_data(as_text=True)
    assert "meer-onderweg" in h              # details-element aanwezig
    assert f"Toon alle {n} plekken" in h
