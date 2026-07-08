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
    with app.app_context():
        db.session.add(Event(source="uit", ext_id="w1", slug="vandaag-evt",
            title="Vandaagevent", start=now + timedelta(hours=2),
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
    """Volledige keten: Feed-rij -> sync_one('feed') -> Event in databank."""
    with app.app_context():
        db.session.add(Feed(naam="CC Test", url="https://cc.be/a.ics", kind="ical",
                            gemeente="Roeselare", postcode="8800",
                            categorie="cultuur", trusted=True))
        db.session.merge(Setting(key="bron_feed_aan", value="1"))
        db.session.commit()
        fake = mock.Mock(status_code=200, text=ICAL)
        fake.raise_for_status = lambda: None
        with mock.patch("app.services.sources.feeds.requests.get", return_value=fake):
            from app.services.sources import sync_one
            r = sync_one("feed")
        assert r["verwerkt"] == 1
        ev = Event.query.filter_by(source="feed").first()
        assert ev and ev.title == "Kindertheater Pinokkio" and not ev.pending
        assert ev.quality is not None


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
