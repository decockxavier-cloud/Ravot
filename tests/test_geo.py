"""Geo-resolutie: postcode via statische tabel, plaatsnaam via centroid/cache."""
from app.extensions import db
from app.models import PostcodeCentroid, GeoCache
from app import geo


def test_postcode_uit_statische_tabel(app):
    """Zonder event-afgeleide centroid valt een postcode terug op POSTCODE_COORDS."""
    with app.app_context():
        coord = geo.postcode_coord("1000")          # Brussel, staat in de statische tabel
        assert coord is not None and 50.7 < coord[0] < 51.0


def test_postcode_centroid_heeft_voorrang(app):
    with app.app_context():
        db.session.add(PostcodeCentroid(postcode="8800", gemeente="Roeselare",
                                        lat=50.9, lng=3.1, n_events=5))
        db.session.commit()
        assert geo.postcode_coord("8800") == (50.9, 3.1)


def test_zoek_centrum_postcode(app):
    with app.app_context():
        coord = geo.zoek_centrum("9000")            # Gent
        assert coord is not None


def test_zoek_centrum_gemeente_offline_canoniek(app):
    """Plaatsnamen worden opgelost via de canonieke offline lijst (consistent,
    geen netwerk) — die heeft voorrang op event-afgeleide centroids."""
    with app.app_context():
        db.session.add(PostcodeCentroid(postcode="8500", gemeente="Kortrijk",
                                        lat=50.83, lng=3.26, n_events=3))
        db.session.commit()
        coord = geo.zoek_centrum("Kortrijk")
        assert coord is not None
        assert abs(coord[0] - 50.82) < 0.1 and abs(coord[1] - 3.26) < 0.15


def test_geocode_gebruikt_cache_zonder_netwerk(app):
    """Een gecachte plaatsnaam wordt zonder externe call opgelost."""
    with app.app_context():
        db.session.add(GeoCache(term="parijs", lat=48.85, lng=2.35))
        db.session.commit()
        assert geo.zoek_centrum("Parijs") == (48.85, 2.35)   # uit cache, geen netwerk
