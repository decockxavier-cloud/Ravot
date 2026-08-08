"""Patch 222: geen buitenlandse plekken in Ravot.

De Overpass-gebiedsfilter is de echte poort; dit is het tweede slot voor het
geval de regio-instelling ooit ruimer staat.
"""
from app.extensions import db
from app.models import PostcodeCentroid


def _centroid(app):
    with app.app_context():
        db.session.add(PostcodeCentroid(postcode="3680", gemeente="Maaseik",
                                        lat=51.0975, lng=5.7869))
        db.session.commit()


def _element(lat, lng, extra=None):
    tags = {"leisure": "playground", "name": "Testtuin"}
    tags.update(extra or {})
    return {"type": "node", "id": 1, "lat": lat, "lon": lng, "tags": tags}


def test_belgische_plek_komt_binnen(app):
    _centroid(app)
    with app.app_context():
        from app.services.sources.osm import normalise
        assert normalise(_element(51.098, 5.788)) is not None


def test_verre_buitenlandse_plek_geweerd(app):
    _centroid(app)
    with app.app_context():
        from app.services.sources.osm import normalise
        assert normalise(_element(51.271, 5.791)) is None      # Weert (NL)


def test_expliciete_buitenlandse_country_geweerd(app):
    _centroid(app)
    with app.app_context():
        from app.services.sources.osm import normalise
        assert normalise(_element(51.098, 5.788,
                                  {"addr:country": "NL"})) is None
        assert normalise(_element(51.098, 5.788,
                                  {"addr:country": "BE"})) is not None


def test_zonder_referentietabel_blokkeert_niets(app):
    """Faalwijze die telt: zonder postcode-centroïden mag het vangnet de hele
    import niet stilleggen."""
    with app.app_context():
        from app.services.sources.osm import normalise
        assert PostcodeCentroid.query.count() == 0
        assert normalise(_element(51.098, 5.788)) is not None
