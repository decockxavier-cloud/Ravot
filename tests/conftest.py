import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timedelta

import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.models import Event, Family, Child, Organizer, Venue, EditionSeries, PostcodeCentroid


@pytest.fixture()
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def seed(app):
    """Twee gezinnen + drie events in Roeselare/Gent."""
    org = Organizer(uit_id="o1", name="Jeugddienst Roeselare", slug="jeugddienst-roeselare")
    ven = Venue(uit_id="v1", name="Domein", gemeente="Roeselare", postcode="8800", lat=50.946, lng=3.123)
    db.session.add_all([org, ven]); db.session.flush()
    series = EditionSeries(slug="kinderboerderij-roeselare", name="Kinderboerderij", organizer_id=org.id, venue_id=ven.id)
    db.session.add(series); db.session.flush()
    start = datetime.utcnow() + timedelta(hours=3)
    events = [
        Event(uit_id="e1", slug="kinderboerderij-roeselare-1", title="Kinderboerderij",
              start=start, end=start + timedelta(hours=3), gemeente="Roeselare", postcode="8800",
              lat=50.946, lng=3.123, age_min=2, age_max=10, categories=["natuur"], is_free=True,
              price_info=[{"name": "basis", "price": 0}], organizer_id=org.id, venue_id=ven.id, series_id=series.id),
        Event(uit_id="e2", slug="tienerlab-roeselare", title="Tienerlab",
              start=start, end=start + timedelta(hours=2), gemeente="Roeselare", postcode="8800",
              lat=50.95, lng=3.12, age_min=13, age_max=17, categories=["leren"], is_free=False,
              price_info=[{"name": "basis", "price": 10}]),
        Event(uit_id="e3", slug="poppentheater-gent", title="Poppentheater",
              start=start, end=start + timedelta(hours=2), gemeente="Gent", postcode="9000",
              lat=51.054, lng=3.722, age_min=3, age_max=8, categories=["cultuur"], is_free=False,
              price_info=[{"name": "basis", "price": 8},
                          {"name": "kinderen 3 tot 12", "price": 5, "min_age": 3, "max_age": 12}]),
    ]
    db.session.add_all(events)
    db.session.add(PostcodeCentroid(postcode="8800", gemeente="Roeselare", lat=50.946, lng=3.123, n_events=2))
    fam_a = Family(email="a@test.be", postcode="8800", display_name="Familie A")
    fam_b = Family(email="b@test.be", postcode="9000")
    db.session.add_all([fam_a, fam_b]); db.session.flush()
    db.session.add_all([Child(family_id=fam_a.id, birth_year=datetime.utcnow().year - 4),
                        Child(family_id=fam_a.id, birth_year=datetime.utcnow().year - 7),
                        Child(family_id=fam_b.id, birth_year=datetime.utcnow().year - 15)])
    # Feestjesmodule staat in productie standaard uit (uitrol na genoeg
    # partners); voor de tests zetten we ze aan zodat de flow dekbaar blijft.
    from app.models import Setting
    db.session.merge(Setting(key="feestjes_aan", value="1"))
    db.session.commit()
    return {"fam_a": fam_a, "fam_b": fam_b, "events": events}


def login_as(client, family):
    with client.session_transaction() as s:
        s["family_id"] = family.id
