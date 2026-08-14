"""Patch 228: gemeentepagina's die meedoen voor overzichtszoektermen.

Het probleem: Ravot scoorde op losse fiches (één zaak, één zoekopdracht) maar
niet op "activiteiten kinderen Roeselare". De pagina bestond wel, maar was een
platte lijst met alleen namen — zonder structuur, foto's of interne links.
"""
import json
import re

from app.extensions import db
from app.models import Event


def _plekken(app):
    with app.app_context():
        for n in range(12):
            db.session.add(Event(
                title=f"Speeltuin {n}", slug=f"seo-sp{n}", source="osm",
                ext_id=f"seo-sp{n}", is_permanent=True, pending=False,
                hidden=False, gemeente="Roeselare", lat=50.94, lng=3.12,
                subtype="playground", quality=60, categories=["buiten"],
                description="Een fijne speeltuin met schommels."))
        for n in range(6):
            db.session.add(Event(
                title=f"Frituur {n}", slug=f"seo-fr{n}", source="osm",
                ext_id=f"seo-fr{n}", is_permanent=True, pending=False,
                hidden=False, gemeente="Roeselare", lat=50.94, lng=3.12,
                subtype="horeca", indoor=True, quality=50))
        for n in range(3):
            db.session.add(Event(
                title=f"Museum {n}", slug=f"seo-mu{n}", source="osm",
                ext_id=f"seo-mu{n}", is_permanent=True, pending=False,
                hidden=False, gemeente="Roeselare", lat=50.94, lng=3.12,
                subtype="museum", indoor=True, quality=70))
        db.session.commit()


def test_pagina_is_gestructureerd_per_soort(client, app):
    """Eigen koppen per soort vangen ook de specifiekere zoekvragen op."""
    _plekken(app)
    h = client.get("/roeselare").get_data(as_text=True)
    assert "Activiteiten met kinderen in Roeselare" in h        # H1
    assert "Spelen en ravotten in Roeselare" in h
    assert "Eten en drinken met kinderen in Roeselare" in h
    assert "Beleven en ontdekken in Roeselare" in h
    assert "(12)" in h and "(6)" in h and "(3)" in h            # aantallen


def test_items_hebben_beeld_en_context(client, app):
    _plekken(app)
    h = client.get("/roeselare").get_data(as_text=True)
    assert "gem-beeld" in h                                     # foto's
    assert "schommels" in h                                     # beschrijving
    assert 'class="kruimels' in h                               # kruimelpad


def test_gestructureerde_data_voor_zoekmachines(client, app):
    _plekken(app)
    h = client.get("/roeselare").get_data(as_text=True)
    blokken = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', h, re.S)
    soorten = [json.loads(b).get("@type") for b in blokken]
    assert "FAQPage" in soorten
    assert "ItemList" in soorten          # dit is een lijst van plekken
    assert "BreadcrumbList" in soorten    # en waar ze in de site zitten
    lijst = json.loads([b for b in blokken if "ItemList" in b][0])
    assert lijst["numberOfItems"] == 21


def test_interne_links_wijzen_naar_de_overzichtspagina(client, app):
    """Zonder interne links vindt Google een pagina alleen via de sitemap —
    het zwakste signaal dat er is."""
    _plekken(app)
    h = client.get("/e/seo-sp0").get_data(as_text=True)
    assert "Alle activiteiten met kinderen in Roeselare" in h
    h2 = client.get("/ontdek").get_data(as_text=True)
    assert "Activiteiten per gemeente" in h2
    assert 'href="/roeselare"' in h2
