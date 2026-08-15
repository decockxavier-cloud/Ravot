"""Patch 235: sitemap met wijzigingsdata en een bijgewerkte llms.txt."""
import re
from datetime import datetime, timedelta

from app.extensions import db
from app.models import Event, FietsRoute


def test_sitemap_heeft_lastmod_per_url(client, app):
    """Zonder wijzigingsdatum moet Google gokken welke pagina's het opnieuw
    moet bekijken."""
    with app.app_context():
        db.session.add(Event(title="P", slug="sm235", source="osm",
                             ext_id="sm235", is_permanent=True, pending=False,
                             hidden=False, lat=50.94, lng=3.12,
                             gemeente="Roeselare", subtype="playground"))
        db.session.commit()
    h = client.get("/sitemap.xml").get_data(as_text=True)
    assert h.count("<lastmod>") == h.count("<loc>")
    assert re.search(r"<lastmod>\d{4}-\d{2}-\d{2}</lastmod>", h)


def test_fiche_krijgt_zijn_eigen_wijzigingsdatum(client, app):
    with app.app_context():
        oud = datetime.utcnow() - timedelta(days=40)
        ev = Event(title="Oud", slug="sm235-oud", source="osm",
                   ext_id="sm235-oud", is_permanent=True, pending=False,
                   hidden=False, lat=50.94, lng=3.12, gemeente="Roeselare",
                   subtype="playground")
        db.session.add(ev)
        db.session.commit()
        ev.updated_at = oud
        db.session.commit()
        verwacht = oud.strftime("%Y-%m-%d")
    h = client.get("/sitemap.xml").get_data(as_text=True)
    blok = re.search(r"<url><loc>[^<]*/e/sm235-oud</loc><lastmod>([^<]+)", h)
    assert blok and blok.group(1) == verwacht


def test_fietsroutes_staan_in_de_sitemap(client, app):
    with app.app_context():
        db.session.add(FietsRoute(titel="Lus", slug="sm235-route",
                                  afstand_km=18, duur_min=110,
                                  moeilijkheid="vlak", is_lus=True,
                                  pending=False, hidden=False,
                                  gemeente="Roeselare", regio="Leiestreek",
                                  start_lat=50.94, start_lng=3.12))
        db.session.commit()
    h = client.get("/sitemap.xml").get_data(as_text=True)
    assert "/fietsroutes<" in h and "sm235-route" in h


def test_llms_txt_kent_de_fietsroutes(client, app):
    """llms.txt beschreef alleen nog activiteiten — AI-assistenten wisten
    niets van de routes, de klimmeting of de veldgegevens."""
    h = client.get("/llms.txt").get_data(as_text=True)
    assert "/fietsroutes" in h
    assert "klimmeters" in h.lower()
    assert "Ravotscore" in h
    assert "{{" not in h and "{%" not in h        # geen jinja-resten
