"""UiT-sync: normalisatie zonder persoonsgegevens, editie-matching, centroids."""
from app.services.uit_sync import normalise, parse_age_range, parse_prices, find_or_create_series
from app.extensions import db
from app.models import Organizer, Venue


FIXTURE = {
    "@id": "https://io.uitdatabank.be/event/abc-123",
    "name": {"nl": "Kermis Izegem 2026"},
    "description": {"nl": "De jaarlijkse kermis."},
    "startDate": "2026-08-01T10:00:00+02:00",
    "endDate": "2026-08-01T18:00:00+02:00",
    "typicalAgeRange": "3-12",
    "terms": [{"label": {"nl": "Sport en beweging"}}],
    "location": {
        "@id": "https://io.uitdatabank.be/place/pl-1",
        "name": {"nl": "Marktplein"},
        "address": {"nl": {"addressLocality": "Izegem", "postalCode": "8870"}},
        "geo": {"latitude": 50.914, "longitude": 3.213},
    },
    "organizer": {"@id": "https://io.uitdatabank.be/organizer/org-1",
                  "name": {"nl": "Stad Izegem"},
                  "contactPoint": {"email": ["prive@izegem.be"], "phone": ["051123456"]}},
    "priceInfo": [{"category": "base", "name": {"nl": "Basistarief"}, "price": 5},
                  {"category": "tariff", "name": {"nl": "kinderen 3 tot 12"}, "price": 2}],
}


def test_normalise_strips_contact_info(app):
    data = normalise(FIXTURE)
    assert "prive@izegem.be" not in str(data)          # publiq-voorwaarde: niet opslaan
    assert "051123456" not in str(data)
    assert data["gemeente"] == "Izegem" and data["postcode"] == "8870"
    assert data["age_min"] == 3 and data["age_max"] == 12
    assert data["price_info"][1]["min_age"] == 3       # kindertarief herkend


def test_parse_age_variants():
    assert parse_age_range("6-") == (6, 99)
    assert parse_age_range("-12") == (0, 12)
    assert parse_age_range(None) == (0, 99)


def test_series_matching_strips_year(app):
    org = Organizer(uit_id="o9", name="Stad Izegem", slug="stad-izegem")
    ven = Venue(uit_id="v9", name="Marktplein", gemeente="Izegem", postcode="8870")
    db.session.add_all([org, ven]); db.session.flush()
    s1 = find_or_create_series("Kermis Izegem 2025", org, ven)
    s2 = find_or_create_series("Kermis Izegem 2026", org, ven)
    assert s1.id == s2.id                              # zelfde reeks over de jaren heen
