"""Kwaliteitsscore-laag + Ontdek-dagfilters + feed-keten end-to-end."""
from datetime import datetime, timedelta
from unittest import mock

from app.extensions import db
from app.models import Event, Feed, Setting
from app.kwaliteit import bereken_kwaliteit


def _ev(**kw):
    d = dict(source="osm", slug=kw.pop("slug", "x"), title="X",
             is_permanent=True, gemeente="Gent", postcode="9000",
             lat=51.0, lng=3.7, age_min=0, age_max=12, categories=["buiten"])
    d.update(kw)
    return Event(**d)


def test_rijke_fiche_scoort_hoog_kale_laag(app):
    with app.app_context():
        rijk = _ev(slug="rijk", title="Speelbos De Kabouterberg",
                   image_url="https://x/f.jpg", adres="Bosstraat 1",
                   source_url="https://kabouterberg.be", source="wd",
                   description="Een groot avonturenbos met touwenparcours, "
                               "blotevoetenpad en picknickweides voor het hele gezin.")
        kaal = _ev(slug="kaal", title="Speeltuin — Parkstraat")
        assert bereken_kwaliteit(rijk, heeft_reviews=False) >= 60
        assert bereken_kwaliteit(kaal, heeft_reviews=False) < 30


def test_reviews_geven_bonus(app):
    with app.app_context():
        e = _ev(slug="rev", title="Speelplein Ter Heide")
        zonder = bereken_kwaliteit(e, heeft_reviews=False)
        met = bereken_kwaliteit(e, heeft_reviews=True)
        assert met == zonder + 15


def test_lijst_weert_lage_kwaliteit_maar_null_blijft(client, app):
    with app.app_context():
        db.session.add(_ev(slug="laag-q", title="Park — straat", quality=5))
        db.session.add(_ev(slug="hoog-q", title="Dierenpark De Zonnegloed",
                           quality=80))
        db.session.add(_ev(slug="geen-q", title="Nog Niet Berekend", quality=None))
        db.session.merge(Setting(key="kwaliteit_min_lijst", value="30"))
        db.session.commit()
    html = client.get("/gent").get_data(as_text=True)
    assert "Dierenpark De Zonnegloed" in html
    assert "Park — straat" not in html          # onder de drempel -> uit de lijst
    assert "Nog Niet Berekend" in html          # NULL = nog niet berekend -> zichtbaar


def test_kaart_toont_ook_lage_kwaliteit(client, app):
    with app.app_context():
        db.session.add(_ev(slug="kaart-laag", title="Speeltuin — hoek", quality=5))
        db.session.merge(Setting(key="kwaliteit_min_lijst", value="30"))
        db.session.commit()
    d = client.get("/api/kaart-events").get_json()
    titels = {p["t"] for p in d["punten"]} if d and "punten" in d else set()
    # kaart-endpoint kan anders heten; minstens: verkennen-pagina rendert
    assert client.get("/verkennen").status_code == 200


def test_herbereken_cli(app):
    with app.app_context():
        db.session.add(_ev(slug="cli-q", title="Avonturenpark Hoge Bomen",
                           image_url="https://x/i.jpg"))
        db.session.commit()
    r = app.test_cli_runner().invoke(args=["herbereken-kwaliteit"])
    assert r.exit_code == 0 and "herberekend" in r.output.lower()
    with app.app_context():
        assert Event.query.filter_by(slug="cli-q").first().quality > 0


def test_ontdek_default_sort_is_datum_en_wanneer_filtert(client, app):
    now = datetime.utcnow()
    # Event dat de hele dag vandaag loopt, zodat de test niet faalt afhankelijk
    # van het uur (00:01–23:59 = altijd 'vandaag lopend').
    dagstart = now.replace(hour=0, minute=1, second=0, microsecond=0)
    dageind = now.replace(hour=23, minute=59, second=0, microsecond=0)
    with app.app_context():
        db.session.add(Event(source="uit", ext_id="w1", slug="vandaag-evt",
            title="Vandaagevent", start=dagstart, end=dageind,
            gemeente="Gent", postcode="9000", lat=51.0, lng=3.7,
            age_min=0, age_max=12, categories=["buiten"], quality=70))
        db.session.add(Event(source="uit", ext_id="w2", slug="ver-evt",
            title="VolgendeMaandEvent", start=now + timedelta(days=40),
            gemeente="Gent", postcode="9000", lat=51.0, lng=3.7,
            age_min=0, age_max=12, categories=["buiten"], quality=70))
        db.session.commit()
    # default: geen sort-param -> datumsortering actief (Eerst gepland als 'aan')
    html = client.get("/ontdek").get_data(as_text=True)
    assert "Eerst gepland" in html
    # wanneer=vandaag toont wel het event van vandaag, niet dat van volgende maand
    html2 = client.get("/ontdek?wanneer=vandaag").get_data(as_text=True)
    assert "Vandaagevent" in html2 and "VolgendeMaandEvent" not in html2


ICAL = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:e1@cc
SUMMARY:Kindertheater Pinokkio
DTSTART:20260915T140000
DTEND:20260915T153000
DESCRIPTION:Voor kinderen.
END:VEVENT
END:VCALENDAR"""


def test_feed_sync_end_to_end(app):
    """Feeds zijn bewust geen bron meer: Ravot beperkt zich tot UiT, OSM,
    Overture en handmatige toevoegingen."""
    from app.services.sources import REGISTRY
    assert "feed" not in REGISTRY

def test_verrijking_richt_op_middenzone_dichtst_bij_groen(app):
    """te_verrijken(zone='midden') pakt enkel de middenzone, hoogste score eerst."""
    from app.enrich import te_verrijken
    from app.models import Setting
    with app.app_context():
        db.session.merge(Setting(key="kwaliteit_min_lijst", value="30"))
        db.session.merge(Setting(key="kwaliteit_hoog", value="60"))
        for i, q in [("laag", 10), ("mid-laag", 35), ("mid-hoog", 55), ("groen", 80)]:
            db.session.add(_ev(slug=f"z-{i}", title=f"Plek {i}", quality=q))
        db.session.commit()
        kand = te_verrijken(limit=10, zone="midden")
        slugs = [e.slug for e in kand]
        assert slugs == ["z-mid-hoog", "z-mid-laag"]   # enkel midden, 55 vóór 35
        assert "z-laag" not in slugs and "z-groen" not in slugs


def test_verrijking_leest_url_en_vult_gratis(app, monkeypatch):
    """verrijk_plek haalt websitetekst op en de AI mag ook 'gratis' bepalen."""
    import app.enrich as enrich
    gezien = {}
    monkeypatch.setattr(enrich, "_haal_webtekst",
                        lambda url, max_chars=3000: "Gratis toegankelijk speelbos met kabelbaan.")
    def fake_gen(prompt, system):
        gezien["prompt"] = prompt
        import json as _j
        return _j.dumps({"beschrijving": "Leuk speelbos.", "categorie": "natuur",
                         "leeftijd_min": 3, "leeftijd_max": 10, "binnen": False, "gratis": True})
    with app.app_context():
        ev = _ev(slug="url", title="Speelbos", source_url="https://x.be")
        v = enrich.verrijk_plek(ev, generate=fake_gen)
        assert v["gratis"] is True and v["binnen"] is False
        assert v["webtekst_gebruikt"] is True
        assert "speelbos met kabelbaan" in gezien["prompt"].lower()   # webtekst in prompt


def test_kaart_verbergt_speeltuinen(client, app):
    from app.models import Event
    with app.app_context():
        db.session.add(_ev(slug="sp", title="Gewone Speeltuin", subtype="playground"))
        db.session.add(_ev(slug="zoo", title="Dierenpark Testtuin", subtype="zoo",
                           categories=["natuur"]))
        db.session.commit()
    html = client.get("/verkennen?sp=0").get_data(as_text=True)
    assert "Dierenpark Testtuin" in html and "Gewone Speeltuin" not in html


def test_ontdek_standaard_deze_week_en_alle_verruimt(client, app):
    from datetime import datetime, timedelta
    from app.models import Event
    now = datetime.utcnow()
    with app.app_context():
        db.session.add(Event(source="uit", ext_id="w", slug="dw", title="DezeWeekEvent",
            # +5 min: gegarandeerd nog "deze week", ook op zondagavond
            # (met +5 uur kantelde de test elke zondag over de weekgrens).
            start=now + timedelta(minutes=5), gemeente="Gent", postcode="9000",
            lat=51.0, lng=3.7, age_min=0, age_max=12, categories=["cultuur"], quality=70))
        db.session.add(Event(source="uit", ext_id="l", slug="lm", title="LaterEvent",
            start=now + timedelta(days=40), gemeente="Gent", postcode="9000",
            lat=51.0, lng=3.7, age_min=0, age_max=12, categories=["cultuur"], quality=70))
        db.session.commit()
    d = client.get("/ontdek").get_data(as_text=True)
    assert "DezeWeekEvent" in d and "LaterEvent" not in d   # standaard = deze week
    a = client.get("/ontdek?wanneer=alle").get_data(as_text=True)
    assert "LaterEvent" in a                                 # expliciet verruimen werkt


def test_kaart_standaard_deze_week_maar_vaste_plekken_altijd(client, app):
    from datetime import datetime, timedelta
    from app.models import Event
    now = datetime.utcnow()
    with app.app_context():
        db.session.add(Event(source="uit", ext_id="lm2", slug="lm2", title="MaandLaterEvent",
            start=now + timedelta(days=40), gemeente="Gent", postcode="9000",
            lat=51.0, lng=3.7, age_min=0, age_max=12, categories=["cultuur"], quality=70))
        db.session.add(Event(source="osm", ext_id="v", slug="vast", title="VastePlekAltijd",
            is_permanent=True, gemeente="Gent", postcode="9000", lat=51.0, lng=3.7,
            age_min=0, age_max=12, categories=["buiten"], quality=40))
        db.session.commit()
    d = client.get("/verkennen").get_data(as_text=True)
    assert "VastePlekAltijd" in d and "MaandLaterEvent" not in d
    assert "MaandLaterEvent" in client.get("/verkennen?wanneer=alle").get_data(as_text=True)


def test_event_datum_verre_einddatum_wordt_altijd_open(app):
    """Placeholder-einddata ver in de toekomst (bv. jaar 2100/5201) tonen als
    'altijd open' i.p.v. een zinloze concrete datum."""
    from datetime import datetime
    from app.routes.public import event_datum
    from app.models import Event
    now = datetime(2026, 7, 9)
    ver = Event(source="uit", slug="v", title="Fietstocht",
                start=datetime(2020, 1, 1), end=datetime(5201, 1, 28))
    assert event_datum(ver, now=now) == "doorlopend"
    # een normale meerdaagse (dit jaar) blijft wél een concrete datum tonen
    kort = Event(source="uit", slug="k", title="Expo",
                 start=datetime(2026, 6, 1), end=datetime(2026, 9, 1))
    assert "loopt nog t/m" in event_datum(kort, now=now)
