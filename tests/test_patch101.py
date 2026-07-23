"""Patch 101: extra OSM/Overture-gegevens op de fiche.

Contact (telefoon/e-mail/socials), fee/charge (gratis of echte prijs),
toilet/drinkwater/picknick/bbq, ♿-badge, en het speeltuinpakket
(toestellen, echte leeftijden, omheining).
"""
from app.extensions import db
from app.models import Event
from app.services.sources.osm import normalise, _fee_prijs


def _speeltuin_tags(**extra):
    tags = {"leisure": "playground", "name": "Speelplein Bos",
            "addr:city": "Gent", "addr:postcode": "9000"}
    tags.update(extra)
    return {"type": "node", "id": 7, "lat": 51.0, "lon": 3.7, "tags": tags}


def test_fee_en_charge(app):
    # fee=no -> gratis, ook waar de standaard "betalend" is
    assert _fee_prijs({"fee": "no"}, standaard_gratis=False) == \
        (True, [{"name": "basis", "price": 0}])
    # fee=yes met bedrag -> echte prijs
    gratis, prijs = _fee_prijs({"fee": "yes", "charge": "3,50 EUR"},
                               standaard_gratis=True)
    assert gratis is False and prijs == [{"name": "basis", "price": 3.5}]
    # geen fee-tag -> standaardgedrag blijft
    assert _fee_prijs({}, standaard_gratis=True)[0] is True
    assert _fee_prijs({}, standaard_gratis=False)[0] is False


def test_speeltuin_pakket(app):
    d = normalise(_speeltuin_tags(**{
        "playground:slide": "yes", "playground:zipwire": "yes",
        "playground:sandpit": "yes", "playground:onbekend_toestel": "yes",
        "min_age": "3", "max_age": "10", "fenced": "yes",
        "toilets": "yes", "drinking_water": "yes", "picnic_table": "yes",
        "wheelchair": "yes", "phone": "+32 9 123 45 67",
        "email": "speel@gent.be"}))
    assert d["speeltoestellen"] == ["glijbaan", "kabelbaan", "zandbak"]
    assert d["age_min"] == 3 and d["age_max"] == 10
    assert d["omheind"] is True and d["toegankelijk"] is True
    assert d["toilet"] is True and d["drinkwater"] is True and d["picknick"] is True
    assert d["telefoon"] == "+32 9 123 45 67"
    assert d["email"] == "speel@gent.be"
    assert d["is_free"] is True   # speeltuin blijft standaard gratis


def test_upsert_vult_en_wist_nooit(app):
    from app.services.sources.base import upsert_event
    basis = dict(source="osm", ext_id="p101", title="Park", is_permanent=True,
                 gemeente="Gent", postcode="9000", lat=51.0, lng=3.7,
                 age_min=0, age_max=12, categories=[], indoor=False,
                 is_free=True, price_info=[], image_url=None, description="Park.",
                 start=None, end=None)
    with app.app_context():
        upsert_event({**basis, "toilet": True, "telefoon": "09/11 22 33",
                      "speeltoestellen": ["glijbaan"]})
        db.session.commit()
        ev = Event.query.filter_by(ext_id="p101").first()
        assert ev.toilet is True and ev.telefoon == "09/11 22 33"
        # hersync zonder die velden -> niets wordt gewist
        upsert_event(basis)
        db.session.commit()
        assert ev.toilet is True
        assert ev.telefoon == "09/11 22 33"
        assert ev.speeltoestellen == ["glijbaan"]


def test_fiche_toont_contact_en_voorzieningen(client, app):
    with app.app_context():
        db.session.add(Event(
            uit_id="101f", slug="rijk-plein", title="Rijk Plein",
            is_permanent=True, gemeente="Gent", postcode="9000",
            lat=51.0, lng=3.7, age_min=2, age_max=10, subtype="playground",
            toilet=True, drinkwater=True, picknick=True, bbq=True,
            toegankelijk=True, telefoon="09 12 34 56",
            email="info@plein.be", socials=["https://facebook.com/plein"],
            speeltoestellen=["glijbaan", "waterspeeltuin"]))
        db.session.commit()
    html = client.get("/e/rijk-plein").data.decode()
    for verwacht in ("🚻 toilet", "🚰 drinkwater", "🧺 picknicktafels", "🍖 bbq",
                     "♿ toegankelijk", "📞 Bel 09 12 34 56",
                     'href="mailto:info@plein.be"', "📘 Facebook",
                     "🛝 Op de speeltuin: glijbaan · waterspeeltuin"):
        assert verwacht in html, verwacht
