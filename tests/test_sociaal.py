"""Patch 168: volgknoppen naar de Ravot-pagina's op Facebook/Instagram."""
from app.extensions import db
from app.models import Setting


def test_geen_knoppen_zonder_instelling(client):
    h = client.get("/").get_data(as_text=True)
    assert "sociaal-knop" not in h
    assert "Volg Ravot" not in h


def test_knoppen_in_header_footer_en_mobiel(client, app):
    with app.app_context():
        db.session.add(Setting(key="social_facebook",
                               value="https://facebook.com/ravot.be"))
        db.session.add(Setting(key="social_instagram",
                               value="https://instagram.com/ravot.be"))
        db.session.commit()
    h = client.get("/").get_data(as_text=True)
    assert "facebook.com/ravot.be" in h and "instagram.com/ravot.be" in h
    assert "foot-sociaal" in h          # footer
    assert "mobiel-sociaal" in h        # mobiel menu
    assert 'aria-label="Ravot op Facebook"' in h
    assert 'rel="noopener me"' in h     # veilig extern openen


def test_enkel_ingevulde_kanalen(client, app):
    with app.app_context():
        db.session.add(Setting(key="social_facebook",
                               value="https://facebook.com/ravot.be"))
        db.session.commit()
    h = client.get("/").get_data(as_text=True)
    assert "facebook.com/ravot.be" in h
    assert "instagram.com" not in h
