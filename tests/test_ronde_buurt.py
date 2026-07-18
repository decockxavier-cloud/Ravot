"""Buurt-zoeken, deze-week scope, en seed-content."""
from app.extensions import db
from app.models import Event, PostcodeCentroid, ContentPage, MailTemplate
from datetime import datetime, timedelta


def _centroids(app):
    with app.app_context():
        # Roeselare en het nabije Izegem (~7km), en het verre Gent (~50km)
        db.session.add(PostcodeCentroid(postcode="8800", gemeente="Roeselare", lat=50.946, lng=3.123, n_events=5))
        db.session.add(PostcodeCentroid(postcode="8870", gemeente="Izegem", lat=50.915, lng=3.208, n_events=3))
        db.session.add(PostcodeCentroid(postcode="9000", gemeente="Gent", lat=51.054, lng=3.725, n_events=9))
        db.session.commit()


def test_buurt_zoeken_toont_naburige_gemeente(client, app):
    """Zoeken op Roeselare toont ook Izegem (dichtbij), niet Gent (ver)."""
    _centroids(app)
    with app.app_context():
        now = datetime.utcnow()
        db.session.add(Event(uit_id="ro", slug="ro", title="RoeselareFeest",
            start=now+timedelta(days=2), end=now+timedelta(days=2, hours=2),
            gemeente="Roeselare", postcode="8800", lat=50.946, lng=3.123,
            age_min=3, age_max=10, categories=[], is_free=True, price_info=[{"name":"b","price":0}]))
        db.session.add(Event(uit_id="iz", slug="iz", title="IzegemFeest",
            start=now+timedelta(days=2), end=now+timedelta(days=2, hours=2),
            gemeente="Izegem", postcode="8870", lat=50.915, lng=3.208,
            age_min=3, age_max=10, categories=[], is_free=True, price_info=[{"name":"b","price":0}]))
        db.session.add(Event(uit_id="ge", slug="ge", title="GentFeest",
            start=now+timedelta(days=2), end=now+timedelta(days=2, hours=2),
            gemeente="Gent", postcode="9000", lat=51.054, lng=3.725,
            age_min=3, age_max=10, categories=[], is_free=True, price_info=[{"name":"b","price":0}]))
        db.session.commit()
    html = client.get("/ontdek?q=Roeselare&wanneer=alle").get_data(as_text=True)
    assert "RoeselareFeest" in html   # de gezochte gemeente
    assert "IzegemFeest" in html      # buurgemeente (~7km) mee
    assert "GentFeest" not in html    # ver weg (~50km) niet


def test_deze_week_route(client, app):
    r = client.get("/deze-week")
    assert r.status_code == 200


def test_seed_content_laadt(app):
    """De standaardteksten worden ingeladen en bevatten de bedrijfsgegevens."""
    from app.seed_content import seed_standaard_content
    with app.app_context():
        seed_standaard_content()
        db.session.commit()
        privacy = db.session.get(ContentPage, "privacy")
        assert privacy is not None
        assert "YAMY BV" in privacy.inhoud_md
        assert db.session.get(MailTemplate, "inlogcode") is not None
        # contact met het juiste mailadres
        contact = db.session.get(ContentPage, "contact")
        assert "info@complemy.com" in contact.inhoud_md
