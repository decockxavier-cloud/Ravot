"""Patch 102: middel-velden (cuisine, veggie, afhaal, reserveren, honden,
uitbater), Overture-curatievelden (brand/confidence), nieuwe praktische
filters en het scroll-herstel bij filterkliks."""
from app.extensions import db
from app.models import Event, HorecaKandidaat
from app.services.sources.osm import _horeca_extra


def test_horeca_extra_signalen(app):
    uit = _horeca_extra({"cuisine": "Friture;fries", "diet:vegetarian": "yes",
                         "takeaway": "yes", "reservation": "recommended",
                         "dog": "leashed", "operator": "Stad Gent"})
    assert uit == {"cuisine": "friture", "veggie": True, "afhaal": True,
                   "reserveren": True, "huisdieren": True,
                   "uitbater_naam": "Stad Gent"}
    # negatieve of afwezige signalen: niets zetten
    assert _horeca_extra({"dog": "no", "takeaway": "no"}) == {}


def test_fiche_toont_middel_badges_en_uitbater(client, app):
    with app.app_context():
        db.session.add(Event(
            uit_id="102a", slug="frituur-goud", title="Frituur 't Goud",
            is_permanent=True, gemeente="Gent", postcode="9000",
            lat=51.0, lng=3.7, age_min=0, age_max=12, subtype="horeca",
            cuisine="friture", veggie=True, afhaal=True, reserveren=True,
            uitbater_naam="Familie Goud"))
        db.session.commit()
    html = client.get("/e/frituur-goud").data.decode()
    for verwacht in ("🍽️ frituur", "🥦 vegetarische opties", "🥡 om mee te nemen",
                     "🗓️ reserveren aangeraden", "Uitgebaat door Familie Goud"):
        assert verwacht in html, verwacht


def test_nieuwe_filters_werken(client, app):
    with app.app_context():
        db.session.add(Event(uit_id="102b", slug="met-toilet", title="Met Toilet",
                             is_permanent=True, gemeente="Gent", postcode="9000",
                             lat=51.0, lng=3.7, age_min=0, age_max=12,
                             toilet=True, nagekeken=True, curated=True))
        db.session.add(Event(uit_id="102c", slug="zonder", title="Zonder Alles",
                             is_permanent=True, gemeente="Gent", postcode="9000",
                             lat=51.0, lng=3.7, age_min=0, age_max=12,
                             nagekeken=True, curated=True))
        db.session.commit()
    html = client.get("/ontdek?ouder=toilet").data.decode()
    assert "Met Toilet" in html and "Zonder Alles" not in html
    # de nieuwe chips staan in de filterbalk
    assert "🚻 Toilet" in html and "🥦 Vegetarisch" in html


def test_overture_kandidaat_brand_confidence(app):
    with app.app_context():
        db.session.add(HorecaKandidaat(ext_id="ov102", naam="Ketenzaak",
                                       lat=51.0, lng=3.7, gemeente="Gent",
                                       brand="FritKing", confidence=0.93))
        db.session.commit()
        from app.services.sources.overture import zoek_kandidaten
        rij = [r for r in zoek_kandidaten(51.0, 3.7, 5)
               if r["ext_id"] == "ov102"][0]
        assert rij["brand"] == "FritKing"
        assert rij["confidence"] == 0.93


def test_scrollherstel_script_aanwezig(client, app):
    js = open("app/static/js/filters.js").read()
    assert "ravot-filterscroll" in js and "scrollTo" in js
    with app.app_context():
        pass
    html = client.get("/ontdek").data.decode()
    assert "js/filters.js" in html
