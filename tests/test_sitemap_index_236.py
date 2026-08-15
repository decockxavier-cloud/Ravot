"""Patch 236: sitemap opgesplitst zodat het crawlbudget naar de juiste
pagina's gaat.

Aanleiding: /roeselare stond in de sitemap maar was nog nooit gecrawld
("Laatste crawl: N.v.t."). Met bijna 40.000 URL's, grotendeels kale
OSM-punten, kwam Google er simpelweg niet aan toe.
"""
from app.extensions import db
from app.models import Event, FietsRoute


def _data(app):
    with app.app_context():
        for n in range(5):
            db.session.add(Event(
                title=f"Rijk {n}", slug=f"si-rk{n}", source="osm",
                ext_id=f"si-rk{n}", is_permanent=True, pending=False,
                hidden=False, lat=50.9, lng=3.1, gemeente="Roeselare",
                subtype="playground", quality=70,
                description="Een mooie speeltuin met glijbaan."))
        for n in range(20):
            db.session.add(Event(
                title=f"Speeltuin — Straat {n}", slug=f"si-kl{n}",
                source="osm", ext_id=f"si-kl{n}", is_permanent=True,
                pending=False, hidden=False, lat=50.9, lng=3.1,
                gemeente="Roeselare", subtype="playground", quality=20))
        db.session.add(Event(title="Eenzaam", slug="si-ee", source="osm",
                             ext_id="si-ee", is_permanent=True, pending=False,
                             hidden=False, lat=51.2, lng=3.2,
                             gemeente="Dorpje", subtype="playground",
                             quality=80))
        db.session.add(FietsRoute(titel="Lus", slug="si-route", afstand_km=18,
                                  duur_min=110, moeilijkheid="vlak",
                                  is_lus=True, pending=False, hidden=False,
                                  gemeente="Roeselare", start_lat=50.9,
                                  start_lng=3.1))
        db.session.commit()


def test_index_wijst_naar_vier_deelsitemaps(client, app):
    h = client.get("/sitemap.xml").get_data(as_text=True)
    assert "<sitemapindex" in h
    for deel in ("kern", "gemeenten", "routes", "fiches"):
        assert f"sitemap-{deel}.xml" in h


def test_gemeentepaginas_krijgen_een_eigen_bestand(client, app):
    """Zo sneeuwen ze niet onder tussen tienduizenden fiches."""
    _data(app)
    h = client.get("/sitemap-gemeenten.xml").get_data(as_text=True)
    assert "/roeselare<" in h
    assert "/roeselare/gratis" in h          # ook de facetten
    assert "/dorpje<" not in h               # te dun om te ranken


def test_kale_fiches_kosten_geen_crawlbudget_meer(client, app):
    _data(app)
    h = client.get("/sitemap-fiches.xml").get_data(as_text=True)
    assert h.count("/e/si-rk") == 5          # met beschrijving: wel
    assert h.count("/e/si-kl") == 0          # kaal OSM-punt: niet


def test_uitgesloten_fiche_blijft_gewoon_bereikbaar(client, app):
    """Niet in de sitemap betekent niet weg: de pagina werkt gewoon.

    Deze fiches vielen al onder de bestaande lijstdrempel (kwaliteit_min_lijst)
    en waren dus op de site zelf nergens gelinkt — ze stonden alleen nog in de
    sitemap. Dat leverde Google verweesde URL's op."""
    _data(app)
    assert client.get("/e/si-kl0").status_code == 200
    gem = client.get("/roeselare").get_data(as_text=True)
    assert "si-kl0" not in gem          # ook op de site niet gelinkt


def test_sitemap_volgt_de_lijstdrempel(client, app):
    """Wat nergens gelinkt is, hoort niet in de sitemap — en omgekeerd."""
    with app.app_context():
        db.session.add(Event(title="Net genoeg", slug="si-net", source="osm",
                             ext_id="si-net", is_permanent=True, pending=False,
                             hidden=False, lat=50.9, lng=3.1,
                             gemeente="Roeselare", subtype="playground",
                             quality=50,
                             description="Met een korte beschrijving."))
        db.session.add(Event(title="Te dun", slug="si-dun", source="osm",
                             ext_id="si-dun", is_permanent=True, pending=False,
                             hidden=False, lat=50.9, lng=3.1,
                             gemeente="Roeselare", subtype="playground",
                             quality=15,
                             description="Zelfs mét tekst te laag gescoord."))
        db.session.commit()
    h = client.get("/sitemap-fiches.xml").get_data(as_text=True)
    assert "si-net" in h
    assert "si-dun" not in h


def test_partner_staat_altijd_in_de_sitemap(client, app):
    """Wie betaalt, moet vindbaar zijn — ook zonder foto of beschrijving."""
    from datetime import timedelta
    from app.models import utcnow
    with app.app_context():
        db.session.add(Event(title="Partnerzaak", slug="si-partner",
                             source="user", ext_id="si-partner",
                             is_permanent=True, pending=False, hidden=False,
                             lat=50.9, lng=3.1, gemeente="Roeselare",
                             subtype="horeca", indoor=True, quality=10,
                             partner_until=utcnow().replace(tzinfo=None)
                             + timedelta(days=90)))
        db.session.commit()
    h = client.get("/sitemap-fiches.xml").get_data(as_text=True)
    assert "si-partner" in h
