"""Patch 230: SEO/GEO-gaten dichten.

Drie dingen: het breadcrumb-conflict uit patch 228 (twee functies met dezelfde
naam, waardoor kruimelpaden relatieve URL's kregen), deelbeelden voor sociale
media, en organisatie-/websiteschema op de homepage.
"""
import json
import re

from app.extensions import db
from app.models import Event, Setting


def _blokken(html):
    return [json.loads(b) for b in re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]


def test_kruimelpaden_gebruiken_absolute_urls(client, app):
    """Relatieve URL's in gestructureerde data negeert Google stilzwijgend."""
    with app.app_context():
        db.session.add(Event(title="X", slug="seo230-1", source="osm",
                             ext_id="seo230-1", is_permanent=True,
                             pending=False, hidden=False, lat=51.0, lng=3.5,
                             subtype="playground", gemeente="Roeselare"))
        db.session.commit()
    for pad in ("/e/seo230-1", "/roeselare"):
        kruimels = [d for d in _blokken(client.get(pad).get_data(as_text=True))
                    if d.get("@type") == "BreadcrumbList"]
        assert kruimels, pad
        for item in kruimels[0]["itemListElement"]:
            assert item["item"].startswith("http"), (pad, item)


def test_deelbeeld_per_fiche_en_terugval(client, app):
    """Een gedeelde link zonder beeld is op WhatsApp een kale tekstregel."""
    with app.app_context():
        db.session.add(Event(title="Speeltuin X", slug="seo230-2",
                             source="osm", ext_id="seo230-2",
                             is_permanent=True, pending=False, hidden=False,
                             lat=51.0, lng=3.5, subtype="playground",
                             gemeente="Roeselare",
                             image_url="https://ravot.be/foto.jpg"))
        db.session.commit()
    h = client.get("/e/seo230-2").get_data(as_text=True)
    assert 'content="https://ravot.be/foto.jpg"' in h
    assert 'og:type" content="article"' in h
    assert "summary_large_image" in h
    assert 'og:site_name" content="Ravot"' in h
    thuis = client.get("/").get_data(as_text=True)
    assert "og-default.png" in thuis            # nette terugval


def test_homepage_vertelt_wie_ravot_is(client, app):
    with app.app_context():
        db.session.add(Setting(key="social_facebook",
                               value="https://facebook.com/ravot.be"))
        db.session.commit()
    blokken = _blokken(client.get("/").get_data(as_text=True))
    soorten = [d["@type"] for d in blokken]
    assert "Organization" in soorten and "WebSite" in soorten
    org = [d for d in blokken if d["@type"] == "Organization"][0]
    assert "https://facebook.com/ravot.be" in org["sameAs"]
    site = [d for d in blokken if d["@type"] == "WebSite"][0]
    assert "search_term_string" in site["potentialAction"]["target"]["urlTemplate"]
