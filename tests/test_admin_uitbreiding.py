"""Grote admin-ronde: familiebeheer, content-pagina's, mails, paginering, cookiebanner."""
from tests.conftest import login_as
from app.extensions import db
from app.models import Family, ContentPage, Event
from datetime import datetime, timedelta


def _admin_login(client, app):
    """Maak een bevestigde admin en log in."""
    from app.models import Admin
    from argon2 import PasswordHasher
    with app.app_context():
        a = Admin(email="beheer@test.be", pw_hash=PasswordHasher().hash("Testwachtwoord1"),
                  totp_secret="AAAAAAAAAAAAAAAA", totp_confirmed=True)
        db.session.add(a); db.session.commit()
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_2fa_ok"] = True


def test_content_markdown_veilig():
    """Markdown wordt veilig gerenderd — scripts eruit."""
    from app.content import render_markdown
    kwaad = "# Titel\n\n<script>alert('xss')</script>\n\n**vet**"
    html = render_markdown(kwaad)
    assert "<script>" not in html      # script verwijderd
    assert "<strong>vet</strong>" in html  # opmaak blijft


def test_content_pagina_toont_db_inhoud(client, app):
    """Een bewerkte pagina in de db wordt getoond i.p.v. het vaste template."""
    with app.app_context():
        db.session.add(ContentPage(slug="over", titel="Over ons test",
                                   inhoud_md="# Welkom\n\nDit is **onze** test."))
        db.session.commit()
    html = client.get("/over").get_data(as_text=True)
    assert "Over ons test" in html
    assert "<strong>onze</strong>" in html


def test_admin_families_beheer(client, app):
    _admin_login(client, app)
    with app.app_context():
        db.session.add(Family(email="klant@test.be", postcode="9000"))
        db.session.commit()
    # overzicht
    r = client.get("/beheer/families")
    assert r.status_code == 200
    assert b"klant@test.be" in r.data
    # zoeken
    r = client.get("/beheer/families?q=klant")
    assert b"klant@test.be" in r.data


def test_admin_familie_deactiveren(client, app):
    _admin_login(client, app)
    with app.app_context():
        f = Family(email="deact@test.be", postcode="9000")
        db.session.add(f); db.session.commit()
        fid = f.id
    client.post(f"/beheer/families/{fid}", data={"actie": "deactiveer"})
    with app.app_context():
        assert db.session.get(Family, fid).active is False


def test_admin_familie_verwijderen(client, app):
    _admin_login(client, app)
    with app.app_context():
        f = Family(email="weg@test.be", postcode="9000")
        db.session.add(f); db.session.commit()
        fid = f.id
    client.post(f"/beheer/families/{fid}", data={"actie": "verwijder"})
    with app.app_context():
        assert db.session.get(Family, fid) is None


def test_ontdek_paginering(client, app):
    """Ontdek toont maximaal per_pagina en heeft paginanavigatie."""
    with app.app_context():
        now = datetime.utcnow()
        for i in range(30):
            db.session.add(Event(uit_id=f"p{i}", slug=f"p-{i}", title=f"Event {i}",
                start=now+timedelta(days=1, hours=i), end=now+timedelta(days=1, hours=i+1),
                gemeente="Gent", postcode="9000", lat=51.0, lng=3.7,
                age_min=3, age_max=10, categories=[], is_free=True,
                price_info=[{"name":"basis","price":0}]))
        db.session.commit()
    r = client.get("/ontdek?sort=datum")
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    # 30 events, 24 per pagina → pagina 1/2 + volgende-knop
    assert "Volgende" in html


def test_ontdek_filter_gratis(client, app):
    with app.app_context():
        now = datetime.utcnow()
        db.session.add(Event(uit_id="gr", slug="gr", title="GratisEvent",
            start=now+timedelta(days=1), end=now+timedelta(days=1, hours=1),
            gemeente="Gent", postcode="9000", lat=51.0, lng=3.7,
            age_min=3, age_max=10, categories=[], is_free=True,
            price_info=[{"name":"basis","price":0}]))
        db.session.add(Event(uit_id="be", slug="be", title="BetaalEvent",
            start=now+timedelta(days=1), end=now+timedelta(days=1, hours=1),
            gemeente="Gent", postcode="9000", lat=51.0, lng=3.7,
            age_min=3, age_max=10, categories=[], is_free=False,
            price_info=[{"name":"basis","price":8}]))
        db.session.commit()
    html = client.get("/ontdek?filter=gratis").get_data(as_text=True)
    assert "GratisEvent" in html
    assert "BetaalEvent" not in html


def test_cookiebanner_in_html(client, app):
    """De cookiebanner staat in de pagina."""
    html = client.get("/ontdek").get_data(as_text=True)
    assert "cookiebanner" in html
    assert "Analytisch" in html
