"""Patch 103: zelfherstel van vastgelopen syncstatussen.

Een run die urenlang op "running" staat, hoort bij een gesneuveld proces
(herstart/deploy tijdens de sync). Die blokkeerde voorheen de
Synchroniseer-knop voor eeuwig.
"""
from datetime import datetime, timedelta

from app.extensions import db
from app.models import SyncStatus
from app.services.sources import is_sync_running, get_statuses


def _run(source, state, uren_oud):
    row = SyncStatus(source=source, state=state)
    db.session.add(row)
    db.session.commit()
    # updated_at expliciet terugzetten (onupdate zou hem anders verversen)
    db.session.execute(db.text(
        "UPDATE sync_status SET updated_at = :t WHERE source = :s"),
        {"t": datetime.utcnow() - timedelta(hours=uren_oud), "s": source})
    db.session.commit()
    return row


def test_oude_running_wordt_hersteld(app):
    with app.app_context():
        _run("osm", "running", uren_oud=120)   # 5 dagen "bezig"
        assert is_sync_running() is False       # geneest én blokkeert niet meer
        st = get_statuses()["osm"]
        assert st.state == "error"
        assert "automatisch hersteld" in st.last_error


def test_verse_running_blijft_gewoon_bezig(app):
    with app.app_context():
        _run("uit", "running", uren_oud=1)      # legitiem bezig
        assert is_sync_running() is True
        assert get_statuses()["uit"].state == "running"


def test_badge_toont_startmoment_bij_running(app):
    """De badge toont bij een lopende sync het startmoment (updated_at), niet
    de datum van de vorige afgeronde run — dat leidde tot 'bezig… 18/07' terwijl
    de sync die dag was gestart. Getest op de template + de statusrij zelf,
    zonder HTTP: de adminsessie namaken bleek in CI te wisselvallig."""
    from datetime import datetime, timedelta
    tpl = open("app/templates/admin/verbindingen.html").read()
    assert "bezig… sinds" in tpl
    assert "st.updated_at.strftime" in tpl          # startmoment, niet last_run
    with app.app_context():
        rij = SyncStatus(source="osm", state="running")
        rij.last_run = datetime.utcnow() - timedelta(days=5)   # oude afgeronde run
        db.session.add(rij)
        db.session.commit()
        st = get_statuses()["osm"]
        assert st.state == "running"
        # updated_at is van nu, last_run nog de oude datum: de badge toont dus
        # het startmoment en niet 18/07-achtige verwarring
        assert (datetime.utcnow() - st.updated_at).total_seconds() < 120
        assert (datetime.utcnow() - st.last_run).days >= 4


# --------------------------------------------------------------- patch 104 --

def test_gemeente_fallback_bij_upsert(app):
    from app.models import Event, PostcodeCentroid
    from app.services.sources.base import upsert_event
    with app.app_context():
        db.session.add(PostcodeCentroid(postcode="9820", gemeente="Merelbeke",
                                        lat=50.99, lng=3.75))
        db.session.commit()
        upsert_event(dict(source="osm", ext_id="g1", title="Naamloos Plein",
                          is_permanent=True, gemeente=None, postcode=None,
                          lat=50.991, lng=3.752, age_min=0, age_max=12,
                          categories=[], indoor=False, is_free=True,
                          price_info=[], image_url=None, description="x",
                          start=None, end=None))
        db.session.commit()
        ev = Event.query.filter_by(ext_id="g1").first()
        # 9820 is sinds 2025 officieel "Merelbeke-Melle"; de dichtste_gemeente-
        # fallback vult de fusiegemeente in. Sinds patch 137 levert zo'n
        # coördinaten-GOK bewust géén deelgemeente-bijschrift meer op: het
        # punt kan net zo goed in Melle liggen (zie het Rumbeke-probleem).
        assert ev.postcode == "9820"
        assert ev.gemeente == "Merelbeke-Melle"
        assert ev.deelgemeente is None


def test_backfill_gemeenten_cli(app):
    from app.models import Event, PostcodeCentroid
    runner = app.test_cli_runner()
    with app.app_context():
        db.session.add(PostcodeCentroid(postcode="9000", gemeente="Gent",
                                        lat=51.05, lng=3.72))
        db.session.add(Event(uit_id="g2", slug="anoniem", title="Speeltuin",
                             is_permanent=True, lat=51.051, lng=3.721,
                             age_min=0, age_max=12))
        db.session.commit()
    uit = runner.invoke(args=["backfill-gemeenten"])
    assert "Gemeente aangevuld: 1" in uit.output
    with app.app_context():
        assert Event.query.filter_by(slug="anoniem").first().gemeente == "Gent"


def test_heal_spaart_bron_met_hartslag(app):
    from datetime import datetime as dt
    from app.models import Event
    with app.app_context():
        _run("osm", "running", uren_oud=10)   # ouder dan de drempel...
        db.session.add(Event(uit_id="h1", slug="hartslag", title="Vers",
                             source="osm", is_permanent=True,
                             lat=51.0, lng=3.7, age_min=0, age_max=12))
        db.session.commit()                    # ...maar mét recente activiteit
        assert is_sync_running() is True       # niet geheeld: hij leeft nog
        assert get_statuses()["osm"].state == "running"


# --------------------------------------------------------------- patch 105 --

def test_terugknop_csp_proof(client, app):
    """De terugknop mag geen inline onclick meer hebben (CSP blokkeert die
    stilletjes, waardoor hij altijd naar de lijst viel i.p.v. terug)."""
    from app.models import Event
    with app.app_context():
        db.session.add(Event(uit_id="t105", slug="terug-plek", title="Plek",
                             is_permanent=True, gemeente="Gent", postcode="9000",
                             lat=51.0, lng=3.7, age_min=0, age_max=12))
        db.session.commit()
    html = client.get("/e/terug-plek").data.decode()
    assert "data-terug" in html
    assert "onclick" not in html.split("terug-link")[1][:200]
    js = open("app/static/js/app.js").read()
    assert "data-terug" in js and "history.back" in js


def test_kaartstand_wordt_bewaard(client, app):
    js = open("app/static/js/verkennen.js").read()
    assert "ravot-kaartstand" in js and "moveend" in js
    with app.app_context():
        pass
    html = client.get("/verkennen").data.decode()
    assert "js/verkennen.js" in html



def test_geen_inline_terug_onclicks_meer(app):
    """De CSP blokkeert inline onclick geruisloos — dit patroon mag nergens
    meer opduiken. Generieke ← Terug-knoppen gebruiken data-terug."""
    import glob
    for pad in glob.glob("app/templates/**/*.html", recursive=True):
        inhoud = open(pad).read()
        assert "history.back" not in inhoud, pad
    assert "data-terug" in open("app/templates/public/score_uitleg.html").read()
    assert "data-terug" in open("app/templates/uitbater/zaak_nieuw.html").read()


# --------------------------------------------------------------- patch 107 --

def test_backfill_gemeenten_zonder_cursorcrash(app):
    """Regressie: yield_per + tussentijdse commits brak de servercursor
    ('named cursor isn't valid anymore'). Nu: eerst lezen, dan schrijven."""
    from app.models import Event, PostcodeCentroid
    runner = app.test_cli_runner()
    with app.app_context():
        db.session.add(PostcodeCentroid(postcode="8800", gemeente="Roeselare",
                                        lat=50.95, lng=3.12))
        for i in range(2500):   # ruim over de commitgrens van 2000
            db.session.add(Event(uit_id=f"c{i}", slug=f"c{i}", title=f"P{i}",
                                 is_permanent=True, lat=50.951, lng=3.121,
                                 age_min=0, age_max=12))
        db.session.commit()
    uit = runner.invoke(args=["backfill-gemeenten"])
    assert uit.exception is None, uit.output
    assert "Gemeente aangevuld: 2500" in uit.output
    with app.app_context():
        assert Event.query.filter(Event.gemeente == "Roeselare").count() == 2500


def test_opruim_buitenland(app):
    """Naam+afstand i.p.v. rechthoek: Eindhoven en Maastricht liggen bínnen
    elke rechthoek rond Vlaanderen en bleven vroeger staan."""
    from app.models import Event, Family, Review, Child
    runner = app.test_cli_runner()
    with app.app_context():
        def plek(slug, titel, gemeente, lat, lng, bron="osm"):
            ev = Event(uit_id=slug, slug=slug, title=titel, source=bron,
                       gemeente=gemeente, is_permanent=True, lat=lat, lng=lng,
                       age_min=0, age_max=12)
            db.session.add(ev)
            return ev
        plek("gent", "Gent", "Gent", 51.05, 3.72)
        plek("voeren", "Voeren", "Voeren", 50.75, 5.80)
        plek("ehv", "Eindhoven", "Eindhoven", 51.44, 5.47)
        plek("maas", "Maastricht", "Maastricht", 50.85, 5.69)
        plek("adam", "Amsterdam", "Amsterdam", 52.37, 4.90, bron="overture")
        plek("rijsel", "Marcq", "Marcq-en-Barœul", 50.68, 3.09, bron="wd")
        efteling = plek("eft", "Efteling", "Kaatsheuvel", 51.65, 5.04, bron="uit")
        keulen = plek("keulen", "Keulen", "Köln", 50.94, 6.96)
        db.session.flush()
        fam = Family(email="r@t.be", postcode="9000")
        db.session.add(fam)
        db.session.flush()
        db.session.add(Child(family_id=fam.id, birth_year=2018))
        db.session.add(Review(family_id=fam.id, event_id=keulen.id,
                              kid_score=4, parent_score=3, child_ages=[8]))
        db.session.commit()

    uit = runner.invoke(args=["opruim-buitenland"])
    assert uit.exception is None, uit.output
    assert "Buiten Vlaanderen/Brussel: 5" in uit.output   # ehv, maas, adam, rijsel, keulen
    assert "Efteling" in uit.output                        # UiT enkel gemeld
    assert "Niets verwijderd" in uit.output
    with app.app_context():
        assert Event.query.count() == 8

    uit = runner.invoke(args=["opruim-buitenland", "--ja"])
    assert uit.exception is None, uit.output
    with app.app_context():
        titels = {e.title for e in Event.query.all()}
        # Vlaams blijft, buitenland weg, Keulen blijft (review), Efteling blijft (UiT)
        assert titels == {"Gent", "Voeren", "Efteling", "Keulen"}


def test_vlaanderen_toets_grensgevallen(app):
    from app.vlaanderen import is_vlaams
    assert is_vlaams("Roeselare", 50.94, 3.12)
    assert is_vlaams("Voeren", 50.75, 5.80)          # naam ontbreekt in lijst, pal op kern
    assert is_vlaams("Middelburg", 51.25, 3.45)      # Belgisch Middelburg
    assert not is_vlaams("Middelburg", 51.50, 3.61)  # Zeeuws Middelburg
    assert not is_vlaams("Eindhoven", 51.44, 5.47)
    assert not is_vlaams("Maastricht", 50.85, 5.69)
    assert not is_vlaams(None, 52.37, 4.90)          # zonder naam, ver weg
    assert is_vlaams(None, 51.05, 3.72)              # zonder naam, in Vlaanderen
    assert is_vlaams("Onbekend", None, None)         # geen coördinaten: sparen


# --------------------------------------------------------------- patch 108 --

def test_laad_postcodes_vult_enkel_gaten(app):
    from app.models import PostcodeCentroid
    runner = app.test_cli_runner()
    with app.app_context():
        # bestaand event-afgeleid zwaartepunt met bewust afwijkende naam
        db.session.add(PostcodeCentroid(postcode="8800", gemeente="Roeselare-eigen",
                                        lat=50.95, lng=3.12))
        db.session.commit()
    uit = runner.invoke(args=["laad-postcodes"])
    assert uit.exception is None, uit.output
    assert "1 bestaand behouden" in uit.output
    with app.app_context():
        # bestaande niet overschreven
        assert PostcodeCentroid.query.get("8800").gemeente == "Roeselare-eigen"
        # gaten gevuld (bv. Ieper en Peer uit de ingebakken lijst)
        assert PostcodeCentroid.query.get("8900").gemeente == "Ieper"
        assert PostcodeCentroid.query.get("3990").gemeente == "Peer"
        assert PostcodeCentroid.query.count() > 500
    # herhaald draaien voegt niets meer toe
    uit = runner.invoke(args=["laad-postcodes"])
    assert "0 toegevoegd" in uit.output


def test_herstart_ververst_startmoment(app):
    """Herstart je een sync die al op 'running' staat, dan moet het startmoment
    mee opschuiven — anders toont de badge 'bezig sinds' een oude tijd."""
    from datetime import datetime, timedelta
    from app.services.sources import _set_status
    with app.app_context():
        rij = SyncStatus(source="osm", state="running")
        db.session.add(rij)
        db.session.commit()
        db.session.execute(db.text(
            "UPDATE sync_status SET updated_at = :t WHERE source = 'osm'"),
            {"t": datetime.utcnow() - timedelta(hours=5)})
        db.session.commit()
        db.session.expire_all()
        _set_status("osm", "running")          # herstart
        db.session.expire_all()
        vers = db.session.get(SyncStatus, "osm")
        assert (datetime.utcnow() - vers.updated_at).total_seconds() < 120


# --------------------------------------------------------------- patch 112 --

def test_gemeentezoek_toont_de_hele_gemeente(client, app):
    """Bij een herkende plaats werd de buurt pas NA de cap van 1000 toegepast:
    1000 willekeurige fiches uit heel Vlaanderen, waarvan er toevallig een
    handvol in de gezochte gemeente lag (3 eetplekken in Oostende i.p.v. 196).
    De buurt hoort al in de databank geknepen te worden."""
    import random
    import re
    from app.models import Event, PostcodeCentroid
    with app.app_context():
        db.session.add(PostcodeCentroid(postcode="8400", gemeente="Oostende",
                                        lat=51.2155, lng=2.927))
        random.seed(7)
        for i in range(1500):          # elders in Vlaanderen
            db.session.add(Event(uit_id=f"v{i}", slug=f"v{i}", title=f"Zaak {i}",
                                 source="overture", subtype="horeca",
                                 is_permanent=True, gemeente="Elders",
                                 postcode="9000", curated=True, quality=58,
                                 lat=50.9 + random.random() * 0.5,
                                 lng=3.5 + random.random() * 1.5,
                                 age_min=0, age_max=12))
        for i in range(120):           # in Oostende
            db.session.add(Event(uit_id=f"h{i}", slug=f"h{i}", title=f"Eetzaak {i}",
                                 source="overture", subtype="horeca",
                                 is_permanent=True, gemeente="Oostende",
                                 postcode="8400", curated=True, quality=58,
                                 lat=51.23, lng=2.92, age_min=0, age_max=12))
        db.session.commit()
    html = client.get("/ontdek?wanneer=alle&q=oostende&groep=smullen").data.decode()
    m = re.search(r"(\d+)\s+activiteiten", html)
    assert m, "resultaatteller niet gevonden"
    assert int(m.group(1)) >= 100, f"slechts {m.group(1)} resultaten in Oostende"
    # en geen fiches van ver buiten de gezochte gemeente
    assert "Zaak 1" not in html


def test_kaart_toont_de_gezochte_gemeente(client, app):
    """Zelfde euvel als de lijst, maar dan op de kaart: de contingenten (300
    beste eetplekken van héél Vlaanderen) werden gevuld vóór het buurtfilter,
    waardoor een zoekopdracht op Oostende een bijna lege kaart gaf."""
    import json
    import random
    import re
    from app.models import Event, PostcodeCentroid
    with app.app_context():
        db.session.add(PostcodeCentroid(postcode="8400", gemeente="Oostende",
                                        lat=51.2155, lng=2.927))
        random.seed(11)
        for i in range(1200):          # elders, met hógere kwaliteit
            db.session.add(Event(uit_id=f"v{i}", slug=f"v{i}", title=f"Zaak {i}",
                                 source="overture", subtype="horeca",
                                 is_permanent=True, gemeente="Elders",
                                 postcode="9000", curated=True, quality=70,
                                 lat=50.9 + random.random() * 0.5,
                                 lng=3.5 + random.random() * 1.5,
                                 age_min=0, age_max=12))
        for i in range(150):           # in Oostende
            db.session.add(Event(uit_id=f"h{i}", slug=f"h{i}", title=f"Eetzaak {i}",
                                 source="overture", subtype="horeca",
                                 is_permanent=True, gemeente="Oostende",
                                 postcode="8400", curated=True, quality=58,
                                 lat=51.23, lng=2.92, age_min=0, age_max=12))
        db.session.commit()
    html = client.get("/verkennen?wanneer=alle&q=oostende&groep=smullen").data.decode()
    blok = re.search(r'id="map-data">(.*?)</script>', html, re.S)
    assert blok, "map-data ontbreekt"
    markers = json.loads(blok.group(1))["markers"]
    oostendse = [m for m in markers if m["title"].startswith("Eetzaak")]
    assert len(oostendse) >= 100, f"slechts {len(oostendse)} Oostendse pins"


# --------------------------------------------------------------- patch 113 --

def _kaartplekken(app, n_kust=150, n_gent=250):
    from app.models import Event
    import random
    random.seed(13)
    with app.app_context():
        for i in range(n_kust):
            db.session.add(Event(uit_id=f"k{i}", slug=f"k{i}", title=f"Kust {i}",
                                 source="osm", subtype="playground", is_permanent=True,
                                 gemeente="Oostende", lat=51.23 + random.random() * .01,
                                 lng=2.92, age_min=0, age_max=12, quality=50))
        for i in range(n_gent):
            db.session.add(Event(uit_id=f"g{i}", slug=f"g{i}", title=f"Gent {i}",
                                 source="osm", subtype="playground", is_permanent=True,
                                 gemeente="Gent", lat=51.05 + random.random() * .01,
                                 lng=3.72, age_min=0, age_max=12, quality=80))
        db.session.commit()


def test_kaart_api_geeft_pins_van_het_zichtbare_gebied(client, app):
    _kaartplekken(app)
    d = client.get("/api/kaart?z=51.15&n=51.30&w=2.80&o=3.05&zoom=12&wanneer=alle").get_json()
    assert d["modus"] == "pins"
    assert d["totaal"] == 150                      # enkel de kust, niet Gent
    assert all(m["title"].startswith("Kust") for m in d["markers"])


def test_kaart_api_groepeert_ver_uitgezoomd(client, app):
    _kaartplekken(app)
    d = client.get("/api/kaart?z=50.6&n=51.6&w=2.4&o=6.0&zoom=8&wanneer=alle").get_json()
    assert d["modus"] == "gemeenten"
    per_gem = {g["gemeente"]: g["aantal"] for g in d["groepen"]}
    assert per_gem == {"Oostende": 150, "Gent": 250}


def test_kaart_api_respecteert_filters_en_fouten(client, app):
    _kaartplekken(app)
    d = client.get("/api/kaart?z=50.6&n=51.6&w=2.4&o=6.0&zoom=12"
                   "&wanneer=alle&groep=smullen").get_json()
    assert d["totaal"] == 0                        # speeltuinen zijn geen eetplekken
    assert client.get("/api/kaart?zoom=9").status_code == 400
    js = open("app/static/js/verkennen.js").read()
    assert "/api/kaart?" in js and "moveend zoomend" in js


# --------------------------------------------------------------- patch 114 --

def test_openingsuren_gebruiken_belgische_klok(app):
    """Server draait op UTC: om 22u55 Belgische tijd is het 20u55 UTC, waardoor
    een zaak die om 22:00 sluit ten onrechte 'open' bleef tonen."""
    from datetime import datetime, timezone
    from unittest.mock import patch
    from app.services.openingsuren import status
    from app import tijd

    class Zaak:
        openingsuren = {"do": [["11:30", "22:00"]]}

    # donderdag 23/07/2026, 20:55 UTC = 22:55 in Brussel (zomertijd)
    utc = datetime(2026, 7, 23, 20, 55, tzinfo=timezone.utc)
    with patch("app.tijd.datetime") as dt:
        dt.now.side_effect = lambda tz=None: (utc.astimezone(tz).replace(tzinfo=None)
                                              if tz else utc.replace(tzinfo=None))
        assert status(Zaak())[0] == "dicht"      # was 'open' vóór deze fix

    # en om 19:00 UTC = 21:00 lokaal hoort hij nog open te zijn
    utc = datetime(2026, 7, 23, 19, 0, tzinfo=timezone.utc)
    with patch("app.tijd.datetime") as dt:
        dt.now.side_effect = lambda tz=None: (utc.astimezone(tz).replace(tzinfo=None)
                                              if tz else utc.replace(tzinfo=None))
        st, sluit = status(Zaak())
        assert st == "bijna" and sluit == "22:00"


def test_nu_lokaal_loopt_voor_op_utc(app):
    from datetime import datetime, timezone
    from app.tijd import nu_lokaal
    verschil = (nu_lokaal() - datetime.now(timezone.utc).replace(tzinfo=None))
    uren = round(verschil.total_seconds() / 3600)
    assert uren in (1, 2), f"onverwacht tijdverschil: {uren} uur"


# --------------------------------------------------------------- patch 115 --

def test_zoeken_op_gemeente_toont_die_gemeente(client, app):
    """Torhout gaf vooral Brugge en Roeselare: die liggen binnen 20 km van het
    middelpunt. De gemeente zelf hoort voorrang te krijgen."""
    import re
    from app.models import Event, PostcodeCentroid
    with app.app_context():
        db.session.add(PostcodeCentroid(postcode="8820", gemeente="Torhout",
                                        lat=51.066, lng=3.100))
        for i in range(12):
            db.session.add(Event(uit_id=f"t{i}", slug=f"t{i}", title=f"Torhout {i}",
                                 source="osm", subtype="playground", is_permanent=True,
                                 gemeente="Torhout", lat=51.066, lng=3.100,
                                 age_min=0, age_max=12, quality=50))
        for i in range(200):       # buurgemeenten binnen 20 km
            db.session.add(Event(uit_id=f"b{i}", slug=f"b{i}", title=f"Brugge {i}",
                                 source="osm", subtype="playground", is_permanent=True,
                                 gemeente="Brugge", lat=51.209, lng=3.224,
                                 age_min=0, age_max=12, quality=90))
        db.session.commit()
    html = client.get("/ontdek?wanneer=alle&q=torhout").data.decode()
    assert "Torhout 1" in html
    assert "Brugge" not in html.split("<main")[-1][:4000]


def test_vriendencode_blijft_gelijk_bij_refresh(client, app):
    """Elke refresh gaf een nieuwe code, terwijl je die net wil doorgeven."""
    from app.models import Family, Child
    with app.app_context():
        fam = Family(email="v@t.be", postcode="9000")
        db.session.add(fam); db.session.flush()
        db.session.add(Child(family_id=fam.id, birth_year=2018))
        db.session.commit()
        fid = fam.id
    with client.session_transaction() as s:
        s["family_id"] = fid
    import re
    codes = []
    for _ in range(3):
        html = client.get("/mijn/vrienden").data.decode()
        m = re.search(r'font-family:monospace">([A-Z2-9]{6})<', html)
        codes.append(m.group(1) if m else None)
    assert codes[0] is not None, "geen code gevonden op de pagina"
    assert len(set(codes)) == 1, f"code wisselde bij refresh: {codes}"


def test_kindveld_neemt_bestaand_veld_over(app):
    js = open("app/static/js/app.js").read()
    assert "cloneNode(true)" in js and "wrap.querySelector(\"input\")" in js
    # beide formulieren vragen nu het geboortejaar (patch 117)
    assert "geboortejaar" in open("app/templates/public/proberen.html").read()
    # p233: het welkomstscherm vraagt alleen nog de postcode; de kindvelden
    # (met dezelfde kloon-logica) staan nu in de gezinsinstellingen.
    assert "geboortejaar" in open(
        "app/templates/account/instellingen.html").read()


def test_zoekvak_op_vandaag_zoekt_echt(app):
    """Het zoekvak op de scope-pagina's (vandaag/deze week/weekend) filterde
    enkel de kaartjes op het scherm; nu stuurt het een echte zoekopdracht naar
    /ontdek, met het juiste tijdvenster mee."""
    tpl = open("app/templates/public/lijst.html").read()
    assert "url_for('public.ontdek')" in tpl
    assert 'name="q"' in tpl and 'name="wanneer"' in tpl
    assert "in heel Vlaanderen" in tpl


# --------------------------------------------------------------- patch 116 --

def test_zoekterm_met_postcode_tussen_haakjes(client, app):
    """De autocomplete vult "torhout (8820)" in als zoekterm. Die haakjesvorm
    werd niet als gemeente herkend, waardoor de lijst (en de wissel kaart->lijst)
    leeg bleef. De (postcode) hoort gestript te worden."""
    from app.models import Event, PostcodeCentroid
    with app.app_context():
        db.session.add(PostcodeCentroid(postcode="8820", gemeente="Torhout",
                                        lat=51.066, lng=3.10))
        for i in range(12):
            db.session.add(Event(uit_id=f"t{i}", slug=f"t{i}", title=f"Torhout {i}",
                                 source="osm", subtype="playground", is_permanent=True,
                                 gemeente="Torhout", lat=51.066, lng=3.10,
                                 age_min=0, age_max=12, quality=50))
        db.session.commit()
    # exact zoals de autocomplete het invult
    for pad in ("/ontdek?q=torhout+(8820)&wanneer=alle",
                "/ontdek?q=torhout&wanneer=alle",
                "/verkennen?q=torhout+(8820)&wanneer=alle"):
        html = client.get(pad).data.decode()
        assert "Torhout 1" in html, f"geen Torhout-resultaten op {pad}"


def test_zoekvak_autosuggest_houdt_kale_naam(app):
    """Het zoekvak in de filterkop moet na een suggestiekeuze de kále naam
    overhouden (data-zelf=zoek), niet 'Naam (postcode)'. Anders belandt de
    haakjesvorm alsnog in de URL."""
    kop = open("app/templates/public/_filters_kop.html").read()
    assert 'data-zelf="zoek"' in kop
    js = open("app/static/js/plaatsen.js").read()
    assert 'veld.dataset.zelf === "zoek"' in js


# --------------------------------------------------------------- patch 117 --

def test_personaliseer_gebruikt_geboortejaar(client, app):
    """Personaliseer vroeg de leeftijd, het account het geboortejaar — nu beide
    het geboortejaar, zodat de leeftijd elk jaar vanzelf meegroeit."""
    from datetime import datetime
    jaar = datetime.utcnow().year
    html = client.get("/proberen").data.decode()
    assert 'name="birth_year"' in html and "geboortejaar" in html
    assert 'name="age"' not in html          # geen losse leeftijd meer
    client.post("/proberen", data={"postcode": "9000",
                                   "birth_year": [str(jaar - 6), str(jaar - 3)],
                                   "radius": "25", "budget": "all"})
    with client.session_transaction() as s:
        g = s["guest"]
        assert g["birth_years"] == [jaar - 6, jaar - 3]
        assert g["ages"] == [6, 3]            # afgeleid voor de scoring


def test_personaliseer_aanpassen_en_wissen(client, app):
    """Zodra een gastprofiel bestaat, hoort de banner Aanpassen + Wissen te
    tonen (voorheen verdween de banner en zat je eraan vast)."""
    from app.models import Event
    from datetime import datetime
    with app.app_context():
        db.session.add(Event(uit_id="p117", slug="p117", title="Plek",
                             source="osm", subtype="playground", is_permanent=True,
                             gemeente="Gent", lat=51.05, lng=3.72,
                             age_min=0, age_max=12, quality=50))
        db.session.commit()
    jaar = datetime.utcnow().year
    client.post("/proberen", data={"postcode": "9000", "birth_year": str(jaar - 5),
                                   "radius": "25", "budget": "all"})
    html = client.get("/vandaag").data.decode()
    assert "Aanpassen" in html and "Wissen" in html
    # wissen leegt het profiel
    client.get("/opnieuw")
    with client.session_transaction() as s:
        assert not s.get("guest")


def test_oud_gastprofiel_blijft_werken(client, app):
    """Backwards-compat: een bestaand gastprofiel met enkel 'ages' (van vóór
    deze patch) mag niet breken."""
    with client.session_transaction() as s:
        s["guest"] = {"postcode": "9000", "ages": [4, 7], "radius": 25, "budget": "all"}
    assert client.get("/vandaag").status_code == 200
    assert client.get("/proberen").status_code == 200


# ============================================================ crowdsourcing ==
# Fase 0 — het fundament: stemmenteller per veld. Verandert (nog) niets aan
# wat de gebruiker ziet; de uitkomst per veld is gelijk aan de bestaande boolean.

def test_stem_fundament_uitkomst_gelijk_aan_boolean(app):
    from app.models import Event
    from app import stemmen
    with app.app_context():
        e = Event(uit_id="cs1", slug="cs1", title="Plek", source="osm",
                  subtype="playground", is_permanent=True, gemeente="Gent",
                  lat=51.05, lng=3.72, toilet=True, drinkwater=False, picknick=None)
        db.session.add(e); db.session.flush()
        stemmen.zaai_bronstemmen(e); db.session.commit()
        assert stemmen.veld_status(e.id, "toilet")["waarde"] is True
        assert stemmen.veld_status(e.id, "drinkwater")["waarde"] is False
        # onbekend (None) levert geen stem en dus geen waarde
        assert stemmen.veld_status(e.id, "picknick")["waarde"] is None


def test_stem_een_stem_per_stemmer_geen_stapeling(app):
    from app.models import Event, Family, VeldStem
    from app import stemmen
    with app.app_context():
        e = Event(uit_id="cs2", slug="cs2", title="Plek", source="osm",
                  subtype="playground", is_permanent=True, gemeente="Gent",
                  lat=51.05, lng=3.72, toilet=True)
        db.session.add(e)
        fam = Family(email="cs@t.be", postcode="9000")
        db.session.add(fam); db.session.flush()
        stemmen.zaai_bronstemmen(e)
        stemmen.leg_stem_vast(e.id, "toilet", False, family=fam)
        db.session.commit()
        # bron (ja) + gebruiker (nee) = 2 rijen
        assert VeldStem.query.filter_by(event_id=e.id, veld="toilet").count() == 2
        # gebruiker verandert van gedacht → nog steeds 2 rijen (geen stapeling)
        stemmen.leg_stem_vast(e.id, "toilet", True, family=fam)
        db.session.commit()
        assert VeldStem.query.filter_by(event_id=e.id, veld="toilet").count() == 2


def test_stem_herkomst_bezoekers_zodra_gebruiker_stemt(app):
    from app.models import Event, Family
    from app import stemmen
    with app.app_context():
        e = Event(uit_id="cs3", slug="cs3", title="Plek", source="osm",
                  subtype="playground", is_permanent=True, gemeente="Gent",
                  lat=51.05, lng=3.72, toilet=True)
        db.session.add(e)
        fam = Family(email="cs3@t.be", postcode="9000")
        db.session.add(fam); db.session.flush()
        stemmen.zaai_bronstemmen(e); db.session.commit()
        assert stemmen.veld_status(e.id, "toilet")["herkomst"] == "bron"
        stemmen.leg_stem_vast(e.id, "toilet", True, family=fam)
        db.session.commit()
        assert stemmen.veld_status(e.id, "toilet")["herkomst"] == "bezoekers"


def test_stem_zaaien_is_idempotent(app):
    from app.models import Event, VeldStem
    from app import stemmen
    with app.app_context():
        e = Event(uit_id="cs4", slug="cs4", title="Plek", source="osm",
                  subtype="playground", is_permanent=True, gemeente="Gent",
                  lat=51.05, lng=3.72, toilet=True, drinkwater=True)
        db.session.add(e); db.session.flush()
        stemmen.zaai_bronstemmen(e); db.session.commit()
        n1 = VeldStem.query.filter_by(event_id=e.id).count()
        stemmen.zaai_bronstemmen(e); db.session.commit()
        n2 = VeldStem.query.filter_by(event_id=e.id).count()
        assert n1 == n2 == 2


# Fase 1 — micro-vragen op de fiche: één tik vult een ontbrekend zacht veld.

def _fiche_met_gezin(app, **velden):
    from app.models import Event, Family
    with app.app_context():
        e = Event(uit_id="fa1", slug="fa1", title="Speeltuin", source="osm",
                  subtype="playground", is_permanent=True, gemeente="Gent",
                  lat=51.05, lng=3.72, age_min=0, age_max=12, **velden)
        db.session.add(e)
        fam = Family(email="fa1@t.be", postcode="9000")
        db.session.add(fam); db.session.flush()
        db.session.commit()
        return e.id, fam.id


def test_fiche_microvragen_zonder_account_uit(client, app):
    """Met anoniem stemmen UIT: uitnodiging om aan te melden, geen knoppen."""
    from app.models import Setting
    eid, fid = _fiche_met_gezin(app, toilet=None, picknick=None)
    with app.app_context():
        db.session.add(Setting(key="anoniem_stemmen_aan", value="0"))
        db.session.commit()
    html = client.get("/e/fa1").data.decode()
    assert "Meld je aan" in html and "data-stem-url" not in html


def test_fiche_toont_microvragen_enkel_ingelogd(client, app):
    eid, fid = _fiche_met_gezin(app, toilet=None, picknick=None)
    # Sinds patch 182 mag ook zónder account bevestigd worden (half gewicht,
    # geen punten): de knoppen wijzen dan naar de anonieme route.
    html = client.get("/e/fa1").data.decode()
    assert "/bevestig/" in html
    assert "geen account nodig" in html

def test_een_stem_vult_veld_en_boolean_loopt_mee(client, app):
    from app.models import Event
    eid, fid = _fiche_met_gezin(app, toilet=None)
    with client.session_transaction() as s:
        s["family_id"] = fid
    r = client.post(f"/mijn/veld-stem/{eid}/toilet/ja",
                    headers={"X-Requested-With": "XMLHttpRequest"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    with app.app_context():
        assert db.session.get(Event, eid).toilet is True
    # Fase 2: na één stem is het veld 'voorlopig' — de open ja/nee-vraag maakt
    # plaats voor een 'klopt dit?'-vraag (versterken/weerleggen), dus de knop
    # blijft bestaan maar de OPEN vraag onder "Nog onbekend" is weg.
    html = client.get("/e/fa1").data.decode()
    assert "klopt het?" in html


def test_stem_toggle_trekt_in(client, app):
    from app.models import VeldStem
    eid, fid = _fiche_met_gezin(app, picknick=None)
    with client.session_transaction() as s:
        s["family_id"] = fid
    client.post(f"/mijn/veld-stem/{eid}/picknick/ja")
    client.post(f"/mijn/veld-stem/{eid}/picknick/ja")   # tweede keer = intrekken
    with app.app_context():
        assert VeldStem.query.filter_by(event_id=eid, veld="picknick",
                                        stemmer=str(fid)).count() == 0


def test_stem_weigert_onbekend_veld(client, app):
    eid, fid = _fiche_met_gezin(app)
    with client.session_transaction() as s:
        s["family_id"] = fid
    assert client.post(f"/mijn/veld-stem/{eid}/onzin/ja").status_code == 400
    assert client.post(f"/mijn/veld-stem/{eid}/toilet/misschien").status_code == 400


def test_stem_meta_csrf_token_aanwezig(client, app):
    """De fiche moet een csrf-token-meta bevatten, zodat het fetch-verzoek van
    de stemknoppen een geldig token kan meesturen. Zonder dit faalde elke stem
    (behalve de eerste leek te 'lukken' door de bron-aria-pressed)."""
    eid, fid = _fiche_met_gezin(app, toilet=None)
    with client.session_transaction() as s:
        s["family_id"] = fid
    html = client.get("/e/fa1").data.decode()
    assert 'name="csrf-token"' in html
    js = open("app/static/js/helpaanvullen.js").read()
    assert "X-CSRFToken" in js and "csrf-token" in js


def test_stem_werkt_met_csrf_actief(app):
    """Met CSRF ingeschakeld: zonder token faalt de stem, mét token lukt hij.
    Reproduceert exact de productiesituatie (CSRFProtect staat aan)."""
    import re
    from app.config import TestConfig
    from app.models import Event, Family

    class CsrfConfig(TestConfig):
        WTF_CSRF_ENABLED = True
    from app import create_app
    csrf_app = create_app(CsrfConfig)
    with csrf_app.app_context():
        db.create_all()
        e = Event(uit_id="csrf1", slug="csrf1", title="Speeltuin", source="osm",
                  subtype="playground", is_permanent=True, gemeente="Gent",
                  lat=51.05, lng=3.72, age_min=0, age_max=12, toilet=None)
        db.session.add(e)
        fam = Family(email="csrf@t.be", postcode="9000")
        db.session.add(fam); db.session.flush()
        db.session.commit()
        eid, fid = e.id, fam.id
    c = csrf_app.test_client()
    with c.session_transaction() as s:
        s["family_id"] = fid
    html = c.get("/e/csrf1").data.decode()
    token = re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)
    zonder = c.post(f"/mijn/veld-stem/{eid}/toilet/ja",
                    headers={"X-Requested-With": "XMLHttpRequest"})
    assert zonder.status_code in (400, 403)
    met = c.post(f"/mijn/veld-stem/{eid}/toilet/ja",
                 headers={"X-Requested-With": "XMLHttpRequest", "X-CSRFToken": token})
    assert met.status_code == 200 and met.get_json()["ok"] is True


def test_help_blok_is_ingeklapt_en_onderaan(client, app):
    """Het aanvul-blok mag de fiche niet domineren: inklapbaar (<details>),
    standaard dicht, hooguit enkele vragen, en ná de kern-info."""
    eid, fid = _fiche_met_gezin(app, toilet=None, picknick=None, parking=None)
    with client.session_transaction() as s:
        s["family_id"] = fid
    html = client.get("/e/fa1").data.decode()
    assert '<details class="help-aanvullen"' in html      # inklapbaar
    # p209: bewust wél standaard open zolang er vragen openstaan — ingeklapt
    # werd het blok nauwelijks aangetikt en bleven fiches half ingevuld.
    assert 'help-aanvullen" open' in html
    assert html.count("help-vraag") <= 4                   # niet alle tegelijk


# Fase 2 — drie toestanden, versterken/weerleggen, twijfel, veroudering.

def test_veld_drie_toestanden(app):
    from app.models import Event, Family
    from app import stemmen
    with app.app_context():
        e = Event(uit_id="t2a", slug="t2a", title="Plek", source="osm",
                  subtype="playground", is_permanent=True, gemeente="Gent",
                  lat=51.05, lng=3.72, toilet=None)
        db.session.add(e); db.session.flush()
        fams = [Family(email=f"t2a{i}@t.be", postcode="9000") for i in range(3)]
        for f in fams: db.session.add(f)
        db.session.flush()
        # onbekend
        assert stemmen.veld_status(e.id, "toilet")["toestand"] == "onbekend"
        # 1 stem → voorlopig
        stemmen.leg_stem_vast(e.id, "toilet", True, family=fams[0]); db.session.commit()
        assert stemmen.veld_status(e.id, "toilet")["toestand"] == "voorlopig"
        # 3 overeenstemmende stemmen → bevestigd
        stemmen.leg_stem_vast(e.id, "toilet", True, family=fams[1])
        stemmen.leg_stem_vast(e.id, "toilet", True, family=fams[2]); db.session.commit()
        assert stemmen.veld_status(e.id, "toilet")["toestand"] == "bevestigd"


def test_tegenstem_toont_twijfel(app):
    from app.models import Event, Family
    from app import stemmen
    with app.app_context():
        e = Event(uit_id="t2b", slug="t2b", title="Plek", source="osm",
                  subtype="playground", is_permanent=True, gemeente="Gent",
                  lat=51.05, lng=3.72, toilet=None)
        db.session.add(e); db.session.flush()
        fams = [Family(email=f"t2b{i}@t.be", postcode="9000") for i in range(4)]
        for f in fams: db.session.add(f)
        db.session.flush()
        for f in fams[:3]:
            stemmen.leg_stem_vast(e.id, "toilet", True, family=f)
        stemmen.leg_stem_vast(e.id, "toilet", False, family=fams[3])
        db.session.commit()
        # neutraal vertrouwen (fase 2 isoleren van fase 3): elk gezin telt gelijk
        cache = {str(f.id): 1.0 for f in fams}
        st = stemmen.veld_status(e.id, "toilet", vertrouwen_cache=cache)
        assert st["waarde"] is True                 # meerderheid nog ja
        assert st["meerderheid_pct"] == 75          # maar eerlijk: 75%


def test_oude_stemmen_verouderen(app):
    from datetime import datetime, timedelta
    from app.models import Event, Family, VeldStem
    from app import stemmen
    with app.app_context():
        e = Event(uit_id="t2c", slug="t2c", title="Plek", source="osm",
                  subtype="playground", is_permanent=True, gemeente="Gent",
                  lat=51.05, lng=3.72, toilet=None)
        db.session.add(e); db.session.flush()
        fams = [Family(email=f"t2c{i}@t.be", postcode="9000") for i in range(4)]
        for f in fams: db.session.add(f)
        db.session.flush()
        for f in fams[:3]:
            stemmen.leg_stem_vast(e.id, "toilet", True, family=f)
        stemmen.leg_stem_vast(e.id, "toilet", False, family=fams[3])
        db.session.commit()
        # maak de 3 ja-stemmen ruim ouder dan de halfwaardetijd
        oud = datetime.utcnow() - timedelta(days=1300)
        for s in VeldStem.query.filter_by(event_id=e.id, veld="toilet", waarde=True).all():
            s.updated_at = oud
        db.session.commit()
        cache = {str(f.id): 1.0 for f in fams}      # neutraal vertrouwen
        st = stemmen.veld_status(e.id, "toilet", vertrouwen_cache=cache)
        ja, nee = st["ja"], st["nee"]
        # de 3 oude ja's zijn zo vervaagd dat ze de verse nee amper nog overstemmen
        assert ja < 1.5 and abs(ja - nee) < 0.5


# Fase 3 — onzichtbaar vertrouwen, gevoelige velden, plausibiliteit.

def test_vertrouwen_betrouwbaar_weegt_zwaarder(app):
    from app.models import Event, Family
    from app import stemmen
    with app.app_context():
        fams = {n: Family(email=f"v3{n}@t.be", postcode="9000") for n in "ABCD"}
        for f in fams.values(): db.session.add(f)
        db.session.flush()
        for i in range(8):
            e = Event(uit_id=f"v3e{i}", slug=f"v3e{i}", title=f"P{i}", source="osm",
                      subtype="playground", is_permanent=True, gemeente="Gent",
                      lat=51.05, lng=3.72, toilet=None)
            db.session.add(e); db.session.flush()
            stemmen.leg_stem_vast(e.id, "toilet", True, family=fams["C"])
            stemmen.leg_stem_vast(e.id, "toilet", True, family=fams["D"])
            stemmen.leg_stem_vast(e.id, "toilet", True, family=fams["A"])
            stemmen.leg_stem_vast(e.id, "toilet", False, family=fams["B"])
        db.session.commit()
        va = stemmen.stemmer_vertrouwen(str(fams["A"].id))
        vb = stemmen.stemmer_vertrouwen(str(fams["B"].id))
        assert va > 1.0 > vb            # A boven basis, B eronder
        assert va > vb


def test_veranderingsmelder_niet_gestraft(app):
    """Kernprincipe: wie een verandering meldt en wiens tijdgenoten dat beamen,
    wordt niet gestraft — ook al gaat hij tegen oudere stemmen in."""
    from app.models import Event, Family
    from app import stemmen
    with app.app_context():
        fams = {n: Family(email=f"chg{n}@t.be", postcode="9000") for n in "CXY"}
        for f in fams.values(): db.session.add(f)
        db.session.flush()
        e = Event(uit_id="v3chg", slug="v3chg", title="P", source="osm",
                  subtype="playground", is_permanent=True, gemeente="Gent",
                  lat=51.05, lng=3.72, toilet=None)
        db.session.add(e); db.session.flush()
        for n in "CXY":
            stemmen.leg_stem_vast(e.id, "toilet", False, family=fams[n])
        db.session.commit()
        assert stemmen.stemmer_vertrouwen(str(fams["C"].id)) >= 1.0


def test_gevoelig_veld_hogere_drempel(app):
    from app.models import Event, Family
    from app import stemmen
    with app.app_context():
        e = Event(uit_id="v3g", slug="v3g", title="P", source="osm",
                  subtype="playground", is_permanent=True, gemeente="Gent",
                  lat=51.05, lng=3.72)
        db.session.add(e); db.session.flush()
        fams = [Family(email=f"v3g{i}@t.be", postcode="9000") for i in range(3)]
        for f in fams: db.session.add(f)
        db.session.flush()
        # 3 stemmen op een gevoelig veld → nog NIET bevestigd (hogere drempel)
        for f in fams:
            stemmen.leg_stem_vast(e.id, "gesloten", True, family=f)
        db.session.commit()
        st = stemmen.veld_status(e.id, "gesloten")
        assert st["toestand"] == "voorlopig"     # zou 'bevestigd' zijn bij zacht veld


def test_plausibiliteit_markeert_onmogelijk(app):
    from app.models import Event
    from app import stemmen
    with app.app_context():
        e = Event(uit_id="v3p", slug="v3p", title="P", source="osm",
                  subtype="playground", is_permanent=True, gemeente="Gent",
                  lat=51.05, lng=3.72, age_min=8, age_max=2)
        db.session.add(e); db.session.flush()
        w = stemmen.plausibiliteitswaarschuwingen(e)
        assert any("omgekeerd" in x for x in w)


# Fase 4 — motivatie: bijdrage-punten en badges.

def test_bijdrage_geeft_punten_niet_farmbaar(app):
    from app.models import Event, Family
    from app import punten
    with app.app_context():
        e = Event(uit_id="p4a", slug="p4a", title="P", source="osm",
                  subtype="playground", is_permanent=True, gemeente="Gent",
                  lat=51.05, lng=3.72)
        db.session.add(e)
        fam = Family(email="p4a@t.be", postcode="9000")
        db.session.add(fam); db.session.flush()
        db.session.commit()
        assert punten.ken_toe(fam.id, "veld_stem", ref_id=e.id * 100 + 1) == 3
        # tweede keer zelfde ref → geen extra
        assert punten.ken_toe(fam.id, "veld_stem", ref_id=e.id * 100 + 1) == 0


def test_pionier_badge_voor_eerste_bijdragers(app):
    from app.models import Event, Family
    from app import stemmen, punten
    with app.app_context():
        e = Event(uit_id="p4b", slug="p4b", title="P", source="osm",
                  subtype="playground", is_permanent=True, gemeente="Gent",
                  lat=51.05, lng=3.72)
        db.session.add(e)
        fam = Family(email="p4b@t.be", postcode="9000")
        db.session.add(fam); db.session.flush()
        stemmen.leg_stem_vast(e.id, "toilet", True, family=fam)
        db.session.commit()
        punten._pionier_cache = None
        bs = {b["naam"]: b for b in punten.badges(fam.id)}
        assert bs["Pionier"]["behaald"] is True
        assert bs["Fiche-aanvuller"]["teller"] == 1


def test_eerste_score_bonus_bestaat(app):
    from app.models import PUNT_REDENEN
    assert PUNT_REDENEN["eerste_score"] > PUNT_REDENEN["review"]
    assert "veld_stem" in PUNT_REDENEN


# Selectieve vragen: enkel velden die bij het type plek passen.

def test_relevante_velden_per_type(app):
    from app.models import Event
    from app import stemmen
    with app.app_context():
        sp = Event(uit_id="rv_sp", slug="rv_sp", title="S", source="osm",
                   subtype="playground", is_permanent=True, gemeente="Gent",
                   lat=51.05, lng=3.72)
        ho = Event(uit_id="rv_ho", slug="rv_ho", title="H", source="osm",
                   subtype="horeca", is_permanent=True, gemeente="Gent",
                   lat=51.05, lng=3.72)
        db.session.add_all([sp, ho]); db.session.flush()
        # speeltuin: geen kindermenu, wel drinkwater
        assert "kindermenu" not in stemmen.relevante_velden(sp)
        assert "drinkwater" in stemmen.relevante_velden(sp)
        # horeca: wel kindermenu, geen omheining
        assert "kindermenu" in stemmen.relevante_velden(ho)
        assert "omheind" not in stemmen.relevante_velden(ho)
        # toilet is overal relevant
        assert "toilet" in stemmen.relevante_velden(sp)
        assert "toilet" in stemmen.relevante_velden(ho)


def test_stem_weigert_irrelevant_veld_voor_type(client, app):
    from app.models import Event, Family
    with app.app_context():
        e = Event(uit_id="rv_w", slug="rv_w", title="S", source="osm",
                  subtype="playground", is_permanent=True, gemeente="Gent",
                  lat=51.05, lng=3.72)
        db.session.add(e)
        fam = Family(email="rvw@t.be", postcode="9000")
        db.session.add(fam); db.session.flush()
        db.session.commit()
        eid, fid = e.id, fam.id
    with client.session_transaction() as s:
        s["family_id"] = fid
    # kindermenu hoort niet bij een speeltuin → geweigerd
    assert client.post(f"/mijn/veld-stem/{eid}/kindermenu/ja").status_code == 400
    # drinkwater hoort er wel bij → toegestaan
    assert client.post(f"/mijn/veld-stem/{eid}/drinkwater/ja").status_code in (200, 302)


# Fase 5 — eerlijke inkadering.

def test_zo_help_je_mee_pagina(client, app):
    r = client.get("/zo-help-je-mee")
    assert r.status_code == 200
    html = r.data.decode()
    assert "jij bouwt mee" in html.lower()
    assert "Ravotscore" in html


def test_homepage_toont_bouw_mee_blok(client, app):
    html = client.get("/").data.decode()
    assert "bouw-mee" in html or "bouwt mee" in html.lower()
    assert "zo-help-je-mee" in html


# Gemeentenormalisatie: deelgemeente → officiële fusiegemeente (postcode-based).

def test_gemeente_normalisatie_deelgemeente(app):
    from app import gemeenten
    with app.app_context():
        # bekende probleemgevallen
        assert gemeenten.normaliseer("8800", "Rumbeke") == ("Roeselare", "Rumbeke")
        assert gemeenten.normaliseer("8800", "Roeselare") == ("Roeselare", None)
        assert gemeenten.normaliseer("8830", "Gits") == ("Hooglede", "Gits")
        assert gemeenten.normaliseer("9850", "Nevele") == ("Deinze", "Nevele")
        assert gemeenten.normaliseer("8020", "Waardamme") == ("Oostkamp", "Waardamme")


def test_gemeente_dubbelnaam_via_postcode(app):
    """'Beveren' is deelgemeente van Roeselare (8800) én een aparte fusie in O-Vl
    (9120). De postcode beslist, niet de naam."""
    from app import gemeenten
    with app.app_context():
        assert gemeenten.normaliseer("8800", "Beveren")[0] == "Roeselare"
        # 9120 hoort bij de fusie Beveren-Kruibeke-Zwijndrecht
        assert gemeenten.normaliseer("9120", "Beveren")[0] == "Beveren-Kruibeke-Zwijndrecht"


def test_gemeente_onbekende_postcode_behoudt_naam(app):
    from app import gemeenten
    with app.app_context():
        # postcode niet in de lijst → naam blijft, geen deelgemeente
        assert gemeenten.normaliseer("0000", "Ergens") == ("Ergens", None)


def test_toon_gemeente_weergave(app):
    from app import gemeenten
    with app.app_context():
        assert gemeenten.toon_gemeente("Roeselare", "Rumbeke") == "Roeselare (Rumbeke)"
        assert gemeenten.toon_gemeente("Roeselare", None) == "Roeselare"
        # deelgemeente gelijk aan gemeente → geen dubbeling
        assert gemeenten.toon_gemeente("Torhout", "Torhout") == "Torhout"


# Kop van de Vandaag-pagina opgeschoond (punten 3-7).

def test_vandaag_kop_opgeschoond(app):
    """De opgeschoonde kop: weer-chip in de titelrij, compacte personaliseer-
    strook, subtiele vakantie-lijn, en zoekknop ingebouwd in het zoekveld."""
    from flask import render_template
    from types import SimpleNamespace
    with app.app_context(), app.test_request_context("/vandaag"):
        ev = SimpleNamespace(id=1, slug="x", title="Test", is_free=True,
                             indoor=False, gemeente="Roeselare", deelgemeente=None,
                             postcode="8800", venue=None, start=None, age_min=0,
                             age_max=12, uit_id=None, image_url=None,
                             subtype="playground", is_permanent=True,
                             speeltoestellen=None, categories=[])
        html = render_template("public/lijst.html", rows=[{"event": ev, "agg": None,
            "regen": None}], scope="vandaag", title="Wat gaan we vandaag doen?",
            answer="Vandaag 6 activiteiten.",
            weer={"emoji": "⛅", "label": "bewolkt", "tmax": 24, "regenkans": 10,
                  "dag_label": "Vandaag", "plaats": "Roeselare"},
            vakantie={"banner": "x", "banner_kort": "Zomervakantie — ook kampen."},
            has_profile=True, gast_actief=True, regen=None)
        assert "vandaag-kop" in html          # titel + weer samen
        assert "weer-chip" in html            # weer als compacte chip
        assert "personaliseer-banner compact" in html
        assert "zoekveld-wrap" in html and "zoek-knop" in html  # knop ingebouwd
        assert "vakantie-lijn" in html        # subtiele vakantie-melding
        # geen losse "—" meer in het weerbericht
        assert "regenkans —" not in html


# Leeftijdsbadge: enkel tonen als de leeftijd echt iets zegt (punt 2).

def test_leeftijd_badge_verbergt_defaults(app):
    from types import SimpleNamespace
    with app.app_context():
        lb = app.jinja_env.globals["leeftijd_badge"]
        # standaard-vulwaarden → geen badge
        assert lb(SimpleNamespace(age_min=0, age_max=12)) is None   # restaurant
        assert lb(SimpleNamespace(age_min=0, age_max=99)) is None   # UiT leeg
        # echte leeftijden → wel een badge
        assert lb(SimpleNamespace(age_min=0, age_max=3)) == "tot 3 jaar"
        assert lb(SimpleNamespace(age_min=3, age_max=6)) == "3\u20136 jaar"
        assert lb(SimpleNamespace(age_min=6, age_max=99)) == "vanaf 6 jaar"
        assert lb(SimpleNamespace(age_min=1, age_max=12)) == "1\u201312 jaar"


def test_leeftijd_badge_niet_op_fiche_bij_default(client, app):
    from app.models import Event
    with app.app_context():
        e = Event(uit_id="lft1", slug="lft1", title="Restaurant", source="osm",
                  subtype="horeca", is_permanent=True, gemeente="Gent",
                  postcode="9000", lat=51.05, lng=3.72, age_min=0, age_max=12)
        db.session.add(e); db.session.commit()
    html = client.get("/e/lft1").data.decode()
    # geen "0–12 jaar"-badge meer op een gewone fiche
    assert "0–12 jaar" not in html
    assert "0-12 jaar" not in html


def test_zoekknop_ingebouwd_op_ontdek_en_kaart(client, app):
    """De zoekknop hoort in het zoekveld te zitten (zoekveld-wrap + zoek-knop),
    net als op de Vandaag-pagina — niet los eronder."""
    for url in ("/ontdek", "/ontdek?weergave=kaart"):
        html = client.get(url).data.decode()
        assert "zoekveld-wrap" in html
        assert "zoek-knop" in html
        # de oude losse knop mag er niet meer staan
        assert 'class="btn btn-klein" type="submit">Zoek' not in html
