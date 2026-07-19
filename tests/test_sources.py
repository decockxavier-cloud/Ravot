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


def test_permanente_poi_ook_op_ontdek(client, app):
    """Lijst = kaart: vaste plekken staan óók in Ontdek (na de gedateerde
    events, dankzij de sortering) — anders kon je nooit op restaurants of
    speeltuinen filteren in de lijst."""
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


# ------------------------------------ publiq-vermeldingen zichtbaarheid --

def _zet(app, key, waarde):
    from app.models import Setting
    from app.extensions import db as _db
    row = _db.session.get(Setting, key) or Setting(key=key)
    row.value = waarde
    _db.session.add(row); _db.session.commit()


def test_geen_uit_vermelding_als_publiq_uit(client, app):
    """Standaard staat publiq uit -> nergens UiT/UiTinVlaanderen op de site."""
    for pad in ("/", "/over", "/voorwaarden"):
        html = client.get(pad).get_data(as_text=True).lower()
        assert "uitinvlaanderen" not in html, pad
        assert "uitdatabank" not in html, pad


def test_uit_vermelding_verschijnt_bij_go_live(client, app):
    """Zodra publiq aanstaat, komen de verplichte verwijzingen terug."""
    _zet(app, "bron_uit_aan", "1")
    html = client.get("/").get_data(as_text=True).lower()
    assert "uitinvlaanderen" in html          # referral-blok terug
    assert "uitdatabank" in html              # bronvermelding terug


def test_niet_uit_bronnen_wel_vermeld(client, app):
    """Toegelaten bronnen (OSM, Overture) worden wél vermeld,
    ook als publiq uit staat."""
    _zet(app, "bron_osm_aan", "1")
    html = client.get("/").get_data(as_text=True)
    assert "OpenStreetMap" in html and "Overture" in html
    assert "uitinvlaanderen" not in html.lower()   # publiq blijft weg


# ------------------------------------------------------------ purge-bron --

def test_purge_bron_verwijdert_events(app):
    """purge-bron haalt de testdata van een bron eruit, andere bronnen blijven."""
    from app.models import Event
    _add_dated_and_permanent()                 # 1 uit-event ('d1') + 1 osm-POI
    assert Event.query.filter_by(source="uit").count() == 1
    runner = app.test_cli_runner()
    res = runner.invoke(args=["purge-bron", "uit", "--ja"])
    assert "opgeruimd" in res.output
    assert Event.query.filter_by(source="uit").count() == 0
    assert Event.query.filter_by(source="osm").count() == 1   # osm ongemoeid


def test_purge_ruimt_verweesde_zwaartepunten_op(app):
    """Een postcode die enkel UiT-events had, verliest na purge ook zijn centroid."""
    from app.models import Event, PostcodeCentroid
    from app.extensions import db as _db
    ev = Event(source="uit", uit_id="only-uit", slug="only-uit",
               title="Enkel hier", gemeente="Testdorp", postcode="9999",
               lat=51.0, lng=3.5, age_min=0, age_max=12, categories=["buiten"])
    _db.session.add(ev)
    _db.session.add(PostcodeCentroid(postcode="9999", gemeente="Testdorp",
                                     lat=51.0, lng=3.5, n_events=1))
    _db.session.commit()
    app.test_cli_runner().invoke(args=["purge-bron", "uit", "--ja"])
    assert _db.session.get(PostcodeCentroid, "9999") is None   # verweesde centroid weg
    assert Event.query.filter_by(source="uit").count() == 0


def test_naamloze_osm_pois_krijgen_unieke_slug(app):
    """Twee naamloze speeltuinen met verschillende id's mogen niet botsen
    (regressie: slug werd afgekapt op 8 tekens van de id)."""
    from app.models import Event
    from app.services.sources.base import upsert_event
    for nid in (1234567, 1234568, 1234569):   # zelfde eerste 3 cijfers
        upsert_event({"source": "osm", "ext_id": f"node/{nid}", "title": "Speeltuin",
                      "is_permanent": True, "lat": 51.0, "lng": 3.7, "age_min": 1,
                      "age_max": 12, "categories": ["buiten"], "is_free": True,
                      "venue_name": "Speeltuin", "venue_ext_id": f"node/{nid}"})
    from app.extensions import db as _db
    _db.session.commit()
    evs = Event.query.filter_by(source="osm").all()
    assert len(evs) == 3                                  # alle drie bewaard
    assert len({e.slug for e in evs}) == 3                # elk een unieke slug


def test_tv_lodging_wordt_geweigerd(app):
    """Logies (lodgings) mogen nooit als attractie binnenkomen."""
    from app.services.sources import toerisme
    row = {"id": "abc", "type": "lodgings",
           "attributes": {"name": [{"content": "Kinderboerderij Vakantiehuis", "language": "nl"}]}}
    assert toerisme.normalise(row) is None                # type-guard grijpt in


def test_osm_geen_website_geen_link(app):
    """Zonder echte website: geen source_url (dus geen lelijke osm.org-link)."""
    from app.services.sources import osm
    d = osm.normalise({"type": "node", "id": 5, "lat": 51, "lon": 3.7,
                       "tags": {"leisure": "playground"}})
    assert d["source_url"] is None


def test_osm_website_en_foto(app):
    """Echte website + image-tag worden overgenomen."""
    from app.services.sources import osm
    d = osm.normalise({"type": "node", "id": 6, "lat": 51, "lon": 3.7,
                       "tags": {"leisure": "playground", "name": "Speelburcht",
                                "website": "https://speelburcht.be",
                                "image": "https://speelburcht.be/foto.jpg"}})
    assert d["source_url"] == "https://speelburcht.be"
    assert d["image_url"] == "https://speelburcht.be/foto.jpg"


def test_poi_image_fallback(app):
    """Zonder foto valt een POI terug op een categorie-illustratie; met foto
    krijg je de echte foto."""
    from app.media import poi_image
    from app.models import Event
    with app.test_request_context():
        buiten = Event(source="osm", categories=["buiten"], indoor=False)
        assert poi_image(buiten).endswith("cat-buiten.svg")
        binnen = Event(source="osm", categories=["buiten"], indoor=True)
        assert poi_image(binnen).endswith("cat-binnen.svg")
        metfoto = Event(source="tm", image_url="https://x/foto.jpg", categories=["cultuur"])
        assert poi_image(metfoto) == "https://x/foto.jpg"


def test_osm_query_builder(app):
    """De per-cel/per-tag query is geldige Overpass QL."""
    from app.services.sources import osm
    with app.app_context():
        cel = next(osm._grid(osm.REGIOS["vlaanderen"]))
        q = osm._query("playground", cel)
        assert q.startswith("[out:json]") and '"leisure"="playground"' in q
        qm = osm._query("museum", cel)
        assert '"tourism"="museum"' in qm and "out center tags" in qm


def test_osm_museum(app):
    """Een museum komt binnen als binnen-cultuur POI."""
    from app.services.sources import osm
    d = osm.normalise({"type": "way", "id": 20, "center": {"lat": 51.05, "lon": 3.72},
                       "tags": {"tourism": "museum", "name": "Kindermuseum",
                                "opening_hours": "Tu-Su 10:00-17:00",
                                "website": "https://kindermuseum.be"}})
    assert d["categories"] == ["cultuur"] and d["indoor"] is True
    assert d["source_url"] == "https://kindermuseum.be"
    assert "Openingsuren" in d["description"]


def test_osm_erotisch_museum_geweerd(app):
    """Duidelijk niet-kindvriendelijke musea worden geweigerd."""
    from app.services.sources import osm
    d = osm.normalise({"type": "node", "id": 21, "lat": 51.0, "lon": 3.7,
                       "tags": {"tourism": "museum", "name": "Erotisch Museum"}})
    assert d is None


# --------------------------------------------------------------- Wikidata --

def _wd_row(soort="museum", label="Museum M", coord="Point(3.72 51.05)",
            website="https://m.be", image="http://commons.wikimedia.org/wiki/Special:FilePath/M.jpg"):
    row = {"_soort": soort,
           "item": {"value": "http://www.wikidata.org/entity/Q42"},
           "itemLabel": {"value": label},
           "coord": {"value": coord}}
    if website:
        row["website"] = {"value": website}
    if image:
        row["image"] = {"value": image}
    return row


def test_wikidata_museum_met_foto_en_site(app):
    from app.services.sources import wikidata
    d = wikidata.normalise(_wd_row())
    assert d is not None
    assert d["source"] == "wd" and d["ext_id"] == "Q42"
    assert d["is_permanent"] is True and d["categories"] == ["cultuur"]
    assert abs(d["lat"] - 51.05) < 1e-6 and abs(d["lng"] - 3.72) < 1e-6   # lat/lng juist
    assert d["source_url"] == "https://m.be"
    assert d["image_url"].endswith("?width=800")                         # Commons-thumbnail
    assert d["attribution"] == "via Wikidata"


def test_wikidata_zonder_naam_of_coord_verworpen(app):
    from app.services.sources import wikidata
    assert wikidata.normalise(_wd_row(label="Q42")) is None              # label = QID = geen naam
    assert wikidata.normalise(_wd_row(coord="")) is None                 # geen coördinaat


def test_wikidata_erotisch_museum_geweerd(app):
    from app.services.sources import wikidata
    assert wikidata.normalise(_wd_row(label="Erotisch Museum")) is None


def test_wikidata_regio_scopes_ontdubbeld(app):
    """Vlaanderen+Brussel+Wallonië => één keer België."""
    from app.services.sources import wikidata
    scopes = wikidata._regio_scopes(["vlaanderen", "brussel", "wallonie", "nederland"])
    assert len(scopes) == 2                                              # België (1x) + NL


def test_vandaag_toont_geen_permanente_pois(client, app):
    """Permanente plekken (speeltuinen e.d.) horen op de kaart, niet in de
    tijdgebonden feeds Vandaag/Weekend."""
    from app.models import Event
    from app.extensions import db as _db
    _db.session.add(Event(source="osm", ext_id="node/9", slug="speeltuin-fallback",
        title="Speeltuin Fallback", is_permanent=True, gemeente="Gent", postcode="9000",
        lat=51.05, lng=3.72, age_min=1, age_max=12, categories=["buiten"], is_free=True))
    _db.session.commit()
    for pad in ("/vandaag", "/weekend"):
        html = client.get(pad).get_data(as_text=True)
        assert "Speeltuin Fallback" not in html, pad


# --------------------------------------------------------------- dedup --

def test_dedup_verbergt_dubbels_rijkste_wint(app):
    """Zelfde plek uit OSM (kaal) + Wikidata (foto+site) op <150m: OSM wordt
    verborgen, Wikidata blijft en houdt de foto."""
    from app.models import Event
    from app.services.sources import dedup_pois
    from app.extensions import db as _db
    osm = Event(source="osm", ext_id="node/1", slug="zoo-osm", title="ZOO Antwerpen",
                is_permanent=True, lat=51.2170, lng=4.4210, age_min=1, age_max=12,
                categories=["natuur"])
    wd = Event(source="wd", ext_id="Q1", slug="zoo-wd", title="ZOO Antwerpen",
               is_permanent=True, lat=51.2171, lng=4.4212, age_min=1, age_max=12,
               categories=["natuur"], image_url="https://commons/zoo.jpg",
               source_url="https://zooantwerpen.be")
    _db.session.add_all([osm, wd]); _db.session.commit()
    n = dedup_pois()
    assert n == 1
    osm_r = Event.query.filter_by(source="osm").first()
    wd_r = Event.query.filter_by(source="wd").first()
    assert osm_r.hidden is True and osm_r.dupe_of == wd_r.id   # kale OSM verborgen
    assert wd_r.hidden is False                                 # Wikidata (met foto) blijft


def test_dedup_spaart_verschillende_plekken(app):
    """Twee verschillende speeltuinen dicht bij elkaar blijven allebei zichtbaar."""
    from app.models import Event
    from app.services.sources import dedup_pois
    from app.extensions import db as _db
    a = Event(source="osm", ext_id="node/1", slug="sp-a", title="Speeltuin Astrid",
              is_permanent=True, lat=51.20, lng=4.40, age_min=1, age_max=12, categories=["buiten"])
    b = Event(source="osm", ext_id="node/2", slug="sp-b", title="Speeltuin Rivierenhof",
              is_permanent=True, lat=51.2001, lng=4.4001, age_min=1, age_max=12, categories=["buiten"])
    _db.session.add_all([a, b]); _db.session.commit()
    assert dedup_pois() == 0                                    # andere naam => geen merge
    assert Event.query.filter_by(hidden=True).count() == 0


def test_verborgen_dubbel_niet_in_ontdek(client, app):
    from app.models import Event
    from app.extensions import db as _db
    _db.session.add(Event(source="osm", ext_id="node/9", slug="verborgen-poi",
        title="Verborgen Dubbel", is_permanent=True, hidden=True, gemeente="Gent",
        postcode="9000", lat=51.05, lng=3.72, age_min=1, age_max=12, categories=["buiten"]))
    _db.session.commit()
    assert "Verborgen Dubbel" not in client.get("/ontdek").get_data(as_text=True)


def test_osm_volledig_adres(app):
    """OSM addr:street + housenumber worden tot een volledig adres samengevoegd."""
    from app.services.sources import osm
    d = osm.normalise({"type": "node", "id": 30, "lat": 51.05, "lon": 3.72,
                       "tags": {"tourism": "museum", "name": "Design Museum",
                                "addr:street": "Jan Breydelstraat", "addr:housenumber": "5",
                                "addr:postcode": "9000", "addr:city": "Gent"}})
    assert d["adres"] == "Jan Breydelstraat 5"
    assert d["postcode"] == "9000" and d["gemeente"] == "Gent"


def test_osm_adres_getoond_op_fiche(client, app):
    from app.models import Event
    from app.extensions import db as _db
    _db.session.add(Event(source="osm", ext_id="node/31", slug="museum-adres",
        title="Design Museum", is_permanent=True, gemeente="Gent", postcode="9000",
        adres="Jan Breydelstraat 5", lat=51.05, lng=3.72, age_min=4, age_max=12,
        categories=["cultuur"], indoor=True))
    _db.session.commit()
    html = client.get("/e/museum-adres").get_data(as_text=True)
    assert "Jan Breydelstraat 5" in html


def test_osm_gesloten_plek_geweerd(app):
    """Verlaten/gesloten/voormalige OSM-plekken komen niet binnen."""
    from app.services.sources import osm
    base = {"type": "node", "id": 40, "lat": 51.0, "lon": 3.7}
    assert osm.normalise({**base, "tags": {"disused:tourism": "museum", "name": "Oud Museum"}}) is None
    assert osm.normalise({**base, "tags": {"tourism": "museum", "disused": "yes", "name": "X"}}) is None
    assert osm.normalise({**base, "tags": {"tourism": "museum", "end_date": "2005", "name": "Y"}}) is None
    # gewoon open museum blijft wel
    assert osm.normalise({**base, "tags": {"tourism": "museum", "name": "Levend Museum"}}) is not None


def test_wikidata_query_filtert_ontbonden(app):
    """De SPARQL-query sluit ontbonden/gesloten plekken uit."""
    from app.services.sources import wikidata
    q = wikidata._query("Q33506", "?item wdt:P17 wd:Q31 .")
    assert "P576" in q and "P3999" in q and "FILTER NOT EXISTS" in q


# ---------------------------------------------- OSM verbreding --

def test_osm_park_vereist_naam(app):
    from app.services.sources import osm
    base = {"type": "way", "id": 50, "center": {"lat": 51.0, "lon": 3.7}}
    # naamloos park -> geweigerd (anti-vervuiling)
    assert osm.normalise({**base, "tags": {"leisure": "park"}}) is None
    d = osm.normalise({**base, "tags": {"leisure": "park", "name": "Stadspark"}})
    assert d is not None and d["categories"] == ["buiten"]


def test_osm_kasteel_en_natuurgebied(app):
    from app.services.sources import osm
    kast = osm.normalise({"type": "way", "id": 51, "center": {"lat": 50.9, "lon": 3.1},
                          "tags": {"historic": "castle", "name": "Kasteel Ooidonk"}})
    assert kast["categories"] == ["cultuur"]
    nat = osm.normalise({"type": "way", "id": 52, "center": {"lat": 51.1, "lon": 4.4},
                         "tags": {"leisure": "nature_reserve", "name": "Het Zwin"}})
    assert nat["categories"] == ["natuur"]


def test_detailpagina_toont_kaartje_en_adres(client, app):
    """Een permanente plek toont nu een kaartje + routebeschrijving op de fiche."""
    from app.models import Event
    from app.extensions import db as _db
    _db.session.add(Event(source="osm", ext_id="node/50", slug="speeltuin-detail",
        title="Speeltuin Park", is_permanent=True, gemeente="Gent", postcode="9000",
        adres="Parklaan 1", lat=51.05, lng=3.72, age_min=1, age_max=12,
        categories=["buiten"], is_free=True))
    _db.session.commit()
    html = client.get("/e/speeltuin-detail").get_data(as_text=True)
    assert 'id="minimap"' in html and 'data-lat="51.05"' in html   # kaartje
    assert "Routebeschrijving" in html                              # route-link
    assert "Parklaan 1" in html                                     # adres


def test_event_datum_lopend_toont_geen_voorbije_startdatum(app):
    """Een lopende tentoonstelling (gestart 14/02, eindigt later dit jaar) mag
    niet als voorbije 14/02 tonen, maar als 'loopt nog t/m ...'."""
    from datetime import datetime, timedelta
    from app.routes.public import event_datum
    from app.models import Event
    now = datetime(2026, 7, 8, 12, 0)
    ev = Event(source="uit", slug="expo", title="Expo",
               start=datetime(2026, 2, 14), end=datetime(2026, 12, 31))
    tekst = event_datum(ev, now=now)
    assert "loopt nog" in tekst and "14" not in tekst.split("t/m")[0]


def test_event_datum_toekomst_toont_jaartal_bij_ander_jaar(app):
    from datetime import datetime
    from app.routes.public import event_datum
    from app.models import Event
    now = datetime(2026, 7, 8)
    ev = Event(source="uit", slug="next", title="Next", start=datetime(2027, 2, 14, 10, 0))
    tekst = event_datum(ev, now=now)
    assert "2027" in tekst           # jaartal zichtbaar -> niet verwarrend met verleden


def test_zoeken_op_postcode_in_lijst(client, app):
    """De live-filter op Vandaag/Weekend moet ook op postcode kunnen matchen
    (postcode zit nu in data-zoek)."""
    from datetime import datetime
    from app.models import Event
    from app.extensions import db as _db
    _db.session.add(Event(source="uit", ext_id="pc1", slug="pc-event", title="Iets Leuks",
        start=datetime.utcnow(), gemeente="Gent", postcode="9000", lat=51.05, lng=3.72,
        age_min=0, age_max=12, categories=["buiten"]))
    _db.session.commit()
    html = client.get("/vandaag").get_data(as_text=True)
    assert 'data-zoek="' in html and "9000" in html   # postcode in doorzoekbare tekst
