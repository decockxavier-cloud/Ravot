"""Verfijnronde: ontdek zonder profiel, dashboard, instellingen-splitsing."""
from app.extensions import db
from app.models import Family, Child, Event, SavedEvent
from datetime import datetime, timedelta


def test_ontdek_werkt_zonder_profiel(client, app):
    """Ontdek toont events zonder dat een postcode nodig is."""
    with app.app_context():
        now = datetime.utcnow()
        db.session.add(Event(uit_id="o1", slug="o-1", title="Testfeest", start=now+timedelta(hours=2),
            end=now+timedelta(hours=4), gemeente="Gent", postcode="9000",
            lat=51.05, lng=3.72, age_min=3, age_max=10, categories=["creatief"], is_free=True,
            price_info=[{"name":"basis","price":0}]))
        db.session.commit()
    r = client.get("/ontdek")
    assert r.status_code == 200
    assert b"Testfeest" in r.data
    assert b"Personaliseer" in r.data  # banner voor bezoekers zonder profiel


def test_ontdek_sorteren(client, app):
    r = client.get("/ontdek?sort=gratis")
    assert r.status_code == 200
    r = client.get("/ontdek?sort=datum")
    assert r.status_code == 200


def test_mijn_ravot_dashboard(client, app):
    with app.app_context():
        now = datetime.utcnow()
        e = Event(uit_id="d1", slug="d-1", title="Bewaard event", start=now+timedelta(days=2),
            end=now+timedelta(days=2, hours=2), gemeente="Gent", postcode="9000",
            lat=51.05, lng=3.72, age_min=3, age_max=10, categories=["creatief"], is_free=True,
            price_info=[{"name":"basis","price":0}])
        db.session.add(e)
        fam = Family(email="dash@test.be", postcode="9000", display_name="Dashfam")
        db.session.add(fam); db.session.flush()
        db.session.add(Child(family_id=fam.id, birth_year=now.year-5))
        db.session.add(SavedEvent(family_id=fam.id, event_id=e.id))
        db.session.commit()
        fid = fam.id
    with client.session_transaction() as s:
        s["family_id"] = fid
    r = client.get("/mijn/profiel")
    assert r.status_code == 200
    assert b"Bewaard event" in r.data  # bewaarde activiteit staat in dashboard
    assert b"Mijn Ravot" in r.data


def test_instellingen_apart(client, app):
    with app.app_context():
        fam = Family(email="inst@test.be", postcode="9000")
        db.session.add(fam); db.session.commit()
        fid = fam.id
    with client.session_transaction() as s:
        s["family_id"] = fid
    r = client.get("/mijn/instellingen")
    assert r.status_code == 200
    assert b"Gezinsinstellingen" in r.data


def test_bewaren_geeft_bevestiging(client, app):
    with app.app_context():
        now = datetime.utcnow()
        e = Event(uit_id="b1", slug="b-1", title="X", start=now+timedelta(hours=2),
            end=now+timedelta(hours=4), gemeente="Gent", postcode="9000",
            lat=51.05, lng=3.72, age_min=3, age_max=10, categories=[], is_free=True,
            price_info=[{"name":"basis","price":0}])
        db.session.add(e)
        fam = Family(email="bew@test.be", postcode="9000")
        db.session.add(fam); db.session.flush()
        eid, fid = e.id, fam.id
        db.session.commit()
    with client.session_transaction() as s:
        s["family_id"] = fid
    r = client.post(f"/mijn/bewaar/{eid}", follow_redirects=True)
    assert r.status_code == 200
    assert b"Bewaard" in r.data  # flash-bevestiging


def test_leuk_is_toggle(client, app):
    """Leuk aanklikken en nog eens klikken zet het weer uit."""
    from app.models import Event, Family, Interaction
    with app.app_context():
        now = datetime.utcnow()
        e = Event(uit_id="lk1", slug="lk-1", title="Toggle", start=now+timedelta(hours=2),
            end=now+timedelta(hours=4), gemeente="Gent", postcode="9000",
            lat=51.05, lng=3.72, age_min=3, age_max=10, categories=["natuur"], is_free=True,
            price_info=[{"name":"basis","price":0}])
        db.session.add(e)
        fam = Family(email="tog@test.be", postcode="9000")
        db.session.add(fam); db.session.flush()
        eid, fid = e.id, fam.id
        db.session.commit()
    with client.session_transaction() as s:
        s["family_id"] = fid
    # eerste klik: leuk aan
    client.post(f"/mijn/feedback/{eid}/like", follow_redirects=True)
    with app.app_context():
        assert Interaction.query.filter_by(family_id=fid, event_id=eid, type="like").count() == 1
    # tweede klik: leuk uit
    client.post(f"/mijn/feedback/{eid}/like", follow_redirects=True)
    with app.app_context():
        assert Interaction.query.filter_by(family_id=fid, event_id=eid, type="like").count() == 0


def test_hoe_werkt_het_pagina(client, app):
    r = client.get("/hoe-werkt-het")
    assert r.status_code == 200
    assert b"Zo werkt Ravot" in r.data


def test_kaart_markers_bevatten_ficheinfo(client, app):
    """De kaart stuurt genoeg info mee voor een informatieve fiche."""
    from app.models import Event
    with app.app_context():
        now = datetime.utcnow()
        db.session.add(Event(uit_id="k1", slug="k-1", title="Kaartfeest",
            start=now+timedelta(hours=2), end=now+timedelta(hours=4),
            gemeente="Brugge", postcode="8000", lat=51.2, lng=3.2,
            age_min=4, age_max=10, categories=["natuur"], is_free=True, indoor=False,
            price_info=[{"name":"basis","price":0}], image_url="https://x/y.jpg"))
        db.session.commit()
    html = client.get("/verkennen").get_data(as_text=True)
    # de marker-JSON moet de rijke velden bevatten
    assert "Kaartfeest" in html
    assert "Brugge" in html
    assert "gemeente" in html and "leeftijd" in html and "datum" in html


def test_geen_voorbije_of_verre_events(client, app):
    """Ontdek en Kaart tonen geen voorbije events en geen absurd-verre (2999)."""
    from app.models import Event
    with app.app_context():
        now = datetime.utcnow()
        # een geldig event (volgende week)
        db.session.add(Event(uit_id="geldig", slug="geldig", title="GeldigEvent",
            start=now+timedelta(days=7), end=now+timedelta(days=7, hours=2),
            gemeente="Gent", postcode="9000", lat=51.05, lng=3.72,
            age_min=3, age_max=10, categories=[], is_free=True,
            price_info=[{"name":"basis","price":0}]))
        # een voorbij event (vorig jaar)
        db.session.add(Event(uit_id="oud", slug="oud", title="VoorbijEvent",
            start=now-timedelta(days=400), end=now-timedelta(days=400),
            gemeente="Gent", postcode="9000", lat=51.05, lng=3.72,
            age_min=3, age_max=10, categories=[], is_free=True,
            price_info=[{"name":"basis","price":0}]))
        # een absurd ver event (jaar 2999)
        from datetime import datetime as dt
        db.session.add(Event(uit_id="ver", slug="ver", title="VerEvent",
            start=dt(2999,1,1), end=dt(2999,1,1),
            gemeente="Gent", postcode="9000", lat=51.05, lng=3.72,
            age_min=3, age_max=10, categories=[], is_free=True,
            price_info=[{"name":"basis","price":0}]))
        db.session.commit()
    # 'alle' zodat we het geldig-venster testen, niet de nieuwe week-standaard
    html = client.get("/ontdek?wanneer=alle").get_data(as_text=True)
    assert "GeldigEvent" in html       # geldig event wél
    assert "VoorbijEvent" not in html  # voorbij event niet
    assert "VerEvent" not in html      # 2999-event niet
    # zelfde op de kaart
    kaart = client.get("/verkennen?wanneer=alle").get_data(as_text=True)
    assert "GeldigEvent" in kaart
    assert "VoorbijEvent" not in kaart
    assert "VerEvent" not in kaart


def test_lopend_event_blijft_zichtbaar(client, app):
    """Regressie: een event dat >6u geleden startte maar nog bezig is,
    moet zichtbaar blijven (hele-dag-activiteiten)."""
    from app.models import Event
    with app.app_context():
        now = datetime.utcnow()
        db.session.add(Event(uit_id="lop2", slug="lop-2", title="NogBezigEvent",
            start=now-timedelta(hours=8), end=now+timedelta(hours=2),
            gemeente="Gent", postcode="9000", lat=51.05, lng=3.72,
            age_min=3, age_max=10, categories=[], is_free=True,
            price_info=[{"name":"b","price":0}]))
        db.session.commit()
    html = client.get("/ontdek").get_data(as_text=True)
    assert "NogBezigEvent" in html


def test_vandaag_toont_lijst_voor_gasten(client, app):
    """Regressie: /vandaag toont voor bezoekers zonder profiel de dag-lijst,
    niet de landingspagina (die staat op /)."""
    from app.models import Event
    with app.app_context():
        now = datetime.utcnow()
        db.session.add(Event(uit_id="gst", slug="gst", title="GastEvent",
            start=now+timedelta(hours=1), end=now+timedelta(hours=3),
            gemeente="Gent", postcode="9000", lat=51.05, lng=3.72,
            age_min=3, age_max=10, categories=[], is_free=True,
            price_info=[{"name":"b","price":0}]))
        db.session.commit()
    html = client.get("/vandaag").get_data(as_text=True)
    assert "GastEvent" in html                 # de lijst
    assert "Maak gratis profiel" not in html   # niet de landing


def test_mijn_redirect_naar_profiel(client, app):
    """'/mijn' zelf moet naar het dashboard leiden, niet 404."""
    r = client.get("/mijn/")
    assert r.status_code in (302, 308)
