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


def test_badge_toont_startmoment_bij_running(client, app):
    from datetime import datetime
    from argon2 import PasswordHasher
    import pyotp
    from app.models import Admin
    with app.app_context():
        db.session.add(SyncStatus(source="osm", state="running"))
        admin = Admin(email="badge-test@ravot.be",
                      pw_hash=PasswordHasher().hash("wachtwoord123"),
                      totp_secret=pyotp.random_base32(),
                      totp_confirmed=True)
        db.session.add(admin)
        db.session.commit()
        aid = admin.id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
    r = client.get("/beheer/verbindingen")
    assert r.status_code == 200, r.status_code   # géén redirect naar login
    html = r.data.decode()
    assert "bezig… sinds" in html
    # het startmoment is van vandaag, niet de vorige run
    assert datetime.utcnow().strftime("%d/%m") in html


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
        assert ev.gemeente == "Merelbeke" and ev.postcode == "9820"


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
    from app.models import Event, Family, Review, Child
    runner = app.test_cli_runner()
    with app.app_context():
        db.session.add(Event(uit_id="bi", slug="binnen", title="Gent",
                             source="osm", is_permanent=True,
                             lat=51.05, lng=3.72, age_min=0, age_max=12))
        db.session.add(Event(uit_id="bu", slug="utrecht", title="Utrecht",
                             source="osm", is_permanent=True,
                             lat=52.09, lng=5.12, age_min=0, age_max=12))
        keulen = Event(uit_id="ke", slug="keulen", title="Keulen",
                       source="osm", is_permanent=True,
                       lat=50.94, lng=6.96, age_min=0, age_max=12)
        db.session.add(keulen)
        db.session.flush()
        fam = Family(email="r@t.be", postcode="9000")
        db.session.add(fam)
        db.session.flush()
        db.session.add(Child(family_id=fam.id, birth_year=2018))
        db.session.add(Review(family_id=fam.id, event_id=keulen.id,
                              kid_score=4, parent_score=3, child_ages=[8]))
        db.session.commit()
    # zonder --ja: enkel tellen
    uit = runner.invoke(args=["opruim-buitenland"])
    assert "Buiten Vlaanderen+Brussel: 2" in uit.output
    assert "Niets verwijderd" in uit.output
    with app.app_context():
        assert Event.query.count() == 3
    # met --ja: Utrecht weg, Keulen blijft (review), Gent blijft (binnen)
    uit = runner.invoke(args=["opruim-buitenland", "--ja"])
    assert uit.exception is None, uit.output
    with app.app_context():
        titels = {e.title for e in Event.query.all()}
        assert titels == {"Gent", "Keulen"}


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
