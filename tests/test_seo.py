"""SEO/GEO: gemeentepagina's, antwoordblok, noindex-drempel, JSON-LD, robots, llms."""
from app.extensions import db
from app.models import Event


def test_gemeente_page_and_answer_block(client, seed):
    html = client.get("/roeselare").get_data(as_text=True)
    assert "Wat te doen met kinderen in Roeselare" in html
    assert "activiteiten voor kinderen" in html        # het antwoordblok


def test_gemeente_unknown_404(client, seed):
    assert client.get("/atlantis").status_code == 404


def test_noindex_below_threshold(client, seed):
    # Roeselare heeft 2 events (< drempel 3) → noindex
    html = client.get("/roeselare").get_data(as_text=True)
    assert 'noindex' in html


def test_event_jsonld(client, seed):
    html = client.get(f"/e/{seed['events'][0].slug}").get_data(as_text=True)
    assert '"@type": "Event"' in html
    assert '"isAccessibleForFree": true' in html


def test_robots_welcomes_ai_crawlers(client, seed):
    txt = client.get("/robots.txt").get_data(as_text=True)
    for bot in ("GPTBot", "ClaudeBot", "PerplexityBot"):
        assert bot in txt
    assert "Sitemap:" in txt


def test_llms_txt(client, seed):
    txt = client.get("/llms.txt").get_data(as_text=True)
    assert "Ravotscore" in txt and "sitemap.xml" in txt


def test_sitemap_lists_gemeente_above_threshold(client, seed):
    # extra event zodat Roeselare de drempel haalt
    e = Event.query.filter_by(uit_id="e1").first()
    db.session.add(Event(uit_id="e9", slug="extra-roeselare", title="Extra",
                         start=e.start, end=e.end, gemeente="Roeselare", postcode="8800",
                         lat=50.94, lng=3.12, age_min=2, age_max=10, is_free=True))
    db.session.commit()
    xml = client.get("/sitemap.xml").get_data(as_text=True)
    assert "/roeselare</loc>" in xml
    assert "/uitstap/kinderboerderij-roeselare" in xml  # permanente reekspagina


def test_past_event_redirects_to_series(client, seed):
    from datetime import datetime, timedelta
    ev = Event.query.filter_by(uit_id="e1").first()
    ev.start = datetime.utcnow() - timedelta(days=10)
    ev.end = datetime.utcnow() - timedelta(days=10)
    db.session.commit()
    resp = client.get(f"/e/{ev.slug}")
    assert resp.status_code == 301                     # SEO §2.3: 301 naar reekspagina
    assert "/uitstap/kinderboerderij-roeselare" in resp.headers["Location"]
