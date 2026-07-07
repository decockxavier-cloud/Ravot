"""Extra bronnen: kindvriendelijk-poort, generieke upsert, POI-zichtbaarheid.

De kernregel van Ravot: enkel kindvriendelijk aanbod komt binnen. Deze tests
bewaken die poort per bron én controleren dat permanente POI's zichtbaar zijn
op Ontdek/Kaart/gemeente, maar de gedateerde Vandaag/Weekend-feeds niet vervuilen.
"""
from datetime import datetime, timedelta

from app.extensions import db
from app.models import Event, get_setting
from app.services.sources import ticketmaster, toerisme, osm
from app.services.sources.base import upsert_event, child_safe


# ------------------------------------------------------------- Ticketmaster --

TM_FAMILY = {
    "id": "TM-FAM-1",
    "name": "Bumba Live Show",
    "url": "https://www.ticketmaster.be/event/bumba",
    "info": "Een vrolijke familievoorstelling.",
    "dates": {"start": {"dateTime": "2026-09-15T14:00:00Z"}},
    "classifications": [{"segment": {"name": "Family"},
                         "genre": {"name": "Theatre"}}],
    "priceRanges": [{"min": 18.0, "max": 25.0}],
    "images": [{"url": "https://img/small.jpg", "width": 100},
               {"url": "https://img/big.jpg", "width": 1024}],
    "_embedded": {"venues": [{"id": "V1", "name": "Capitole", "postalCode": "9000",
                              "city": {"name": "Gent"},
                              "location": {"latitude": "51.05", "longitude": "3.72"}}]},
}

TM_CONCERT = {  # GEEN family -> moet verworpen worden
    "id": "TM-ROCK-1", "name": "Loud Rock Night",
    "dates": {"start": {"dateTime": "2026-09-15T21:00:00Z"}},
    "classifications": [{"segment": {"name": "Music"}, "genre": {"name": "Rock"}}],
    "_embedded": {"venues": [{"id": "V2", "city": {"name": "Antwerpen"}}]},
}


def test_tm_family_wordt_genormaliseerd(app):
    d = ticketmaster.normalise(TM_FAMILY)
    assert d is not None
    assert d["source"] == "tm" and d["ext_id"] == "TM-FAM-1"
    assert d["start"].year == 2026 and d["is_permanent"] is False
    assert d["gemeente"] == "Gent" and d["postcode"] == "9000"
    assert d["source_url"].startswith("https://www.ticketmaster.be")
    assert d["attribution"] == "via Ticketmaster"
    assert d["image_url"] == "https://img/big.jpg"       # grootste beeld gekozen
    assert d["age_max"] == 12                            # gezinsgericht


def test_tm_niet_family_wordt_verworpen(app):
    assert ticketmaster.normalise(TM_CONCERT) is None    # POORT werkt


# ------------------------------------------------------- Toerisme Vlaanderen --

def _tv_row(name, types=("Speeltuin",)):
    return {"id": f"tv-{name}", "attributes": {
        "name": name, "types": list(types),
        "description": "", "address": {"municipality": "Brugge", "postalCode": "8000"},
        "geo": {"latitude": 51.2, "longitude": 3.22}}}


def test_tv_kindvriendelijk_wordt_toegelaten(app):
    d = toerisme.normalise(_tv_row("Speelparadijs De Kabouter"))
    assert d is not None
    assert d["source"] == "tv" and d["is_permanent"] is True
    assert d["gemeente"] == "Brugge" and d["age_max"] == 12


def test_tv_rommel_wordt_verworpen(app):
    # Brouwerij, kathedraal, oorlogssite: geen Ravot-materiaal.
    assert toerisme.normalise(_tv_row("Stadsbrouwerij Het Anker", ("Brouwerij",))) is None
    assert toerisme.normalise(_tv_row("Sint-Baafskathedraal", ("Kerk",))) is None
    assert toerisme.normalise(_tv_row("Museum Passchendaele 1917", ("Oorlogsmuseum",))) is None


# --------------------------------------------------------------------- OSM --

def test_osm_speeltuin(app):
    el = {"type": "node", "id": 42, "lat": 51.0, "lon": 3.7,
          "tags": {"leisure": "playground", "name": "Speeltuin Park"}}
    d = osm.normalise(el)
    assert d is not None
    assert d["source"] == "osm" and d["ext_id"] == "node/42"
    assert d["is_permanent"] is True and d["is_free"] is True
    assert d["categories"] == ["buiten"]


def test_osm_zonder_coordinaten_verworpen(app):
    el = {"type": "node", "id": 7, "tags": {"leisure": "playground"}}
    assert osm.normalise(el) is None


def test_osm_niet_kindvriendelijke_tag_verworpen(app):
    el = {"type": "node", "id": 9, "lat": 51.0, "lon": 3.7,
          "tags": {"leisure": "fitness_centre", "name": "Gym"}}
    assert osm.normalise(el) is None


# ------------------------------------------------------- generieke upsert --

def test_upsert_dedup_op_source_ext_id(app):
    data = {"source": "tm", "ext_id": "X1", "title": "Show", "start": None,
            "is_permanent": False, "categories": ["cultuur"], "is_free": False,
            "source_url": "https://x", "attribution": "via Ticketmaster",
            "age_min": 0, "age_max": 12}
    upsert_event(data); db.session.commit()
    data2 = dict(data, title="Show (bijgewerkt)")
    upsert_event(data2); db.session.commit()
    rows = Event.query.filter_by(source="tm", ext_id="X1").all()
    assert len(rows) == 1                                # geen duplicaat
    assert rows[0].title == "Show (bijgewerkt)"          # bijgewerkt
    assert rows[0].has_vlieg is False                    # nooit Vlieg op niet-UiT
    assert rows[0].uit_id is None                        # geen valse UiT-link


# ------------------------------------------------- child_safe hulpfunctie --

def test_child_safe_poort():
    assert child_safe("Grote speeltuin met glijbaan")
    assert child_safe("Dierenpark met kinderboerderij")
    assert not child_safe("Historische brouwerij en jenevermuseum")
    assert not child_safe("Willekeurig gebouw zonder signaal")   # geen whitelist-hit


# --------------------------------------------- POI-zichtbaarheid in de app --

def _add_dated_and_permanent():
    now = datetime.utcnow()
    dated = Event(source="uit", uit_id="d1", slug="dated-uit",
                  title="Poppentheater Vandaag", start=now + timedelta(hours=2),
                  end=now + timedelta(hours=4), gemeente="Gent", postcode="9000",
                  lat=51.05, lng=3.72, age_min=3, age_max=8, categories=["cultuur"],
                  is_free=True, is_permanent=False)
    poi = Event(source="osm", ext_id="node/1", slug="speeltuin-osm",
                title="Speeltuin Altijd Open", is_permanent=True,
                gemeente="Gent", postcode="9000", lat=51.06, lng=3.73,
                age_min=1, age_max=12, categories=["buiten"], is_free=True,
                source_url="https://osm/1", attribution="© OpenStreetMap-bijdragers (ODbL)")
    db.session.add_all([dated, poi]); db.session.commit()
    return dated, poi


def test_permanente_poi_zichtbaar_op_ontdek(client, app):
    _add_dated_and_permanent()
    r = client.get("/ontdek")
    assert r.status_code == 200
    assert "Speeltuin Altijd Open" in r.get_data(as_text=True)


def test_permanente_poi_op_kaart(client, app):
    _add_dated_and_permanent()
    r = client.get("/verkennen")
    assert "Speeltuin Altijd Open" in r.get_data(as_text=True)


def test_geldige_events_bevat_permanent(app):
    from app.routes.public import geldige_events
    _add_dated_and_permanent()
    titels = {e.title for e in geldige_events(Event.query).all()}
    assert "Speeltuin Altijd Open" in titels
    assert "Poppentheater Vandaag" in titels


def test_vandaag_feed_blijft_gedateerd(app):
    """Permanente POI's mogen de gedateerde scored-feed niet vervuilen."""
    from app.routes.public import scored_events
    from app.scoring import Profile
    _add_dated_and_permanent()
    prof = Profile(child_ages=[5], lat=51.05, lng=3.72, radius_km=25)
    rows = scored_events(prof, "vandaag", weer=False)
    titels = {r["event"].title for r in rows}
    assert "Poppentheater Vandaag" in titels
    assert "Speeltuin Altijd Open" not in titels          # geen datum => niet in Vandaag
