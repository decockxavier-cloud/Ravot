"""Patch 234: de aanmeldpagina legt eerst uit waaróm, en belooft niets dat niet
klopt of uitstaat."""
from app.extensions import db
from app.models import Setting


def test_waarom_staat_boven_de_mechaniek(client, app):
    h = client.get("/login").get_data(as_text=True)
    i_waarom = h.find("Wat krijg je met een gezinsprofiel")
    i_hoe = h.find("Geen wachtwoord nodig")
    assert 0 < i_waarom < i_hoe
    assert h.count("Wat krijg je met een gezinsprofiel") == 1   # niet dubbel


def test_beloftes_kloppen_met_de_werkelijkheid(client, app):
    h = client.get("/login").get_data(as_text=True)
    # sinds p229/233 vragen we alleen nog een e-mailadres
    assert "postcode en de geboortejaren" not in h
    assert "Routes meenemen" in h                  # wat er sinds p226 bij kwam
    assert "verjaardagsfeestje regelen" not in h   # functie staat uit


def test_feestje_verschijnt_zodra_vrijgegeven(client, app):
    with app.app_context():
        db.session.add(Setting(key="feestjes_aan", value="1"))
        db.session.commit()
    h = client.get("/login").get_data(as_text=True)
    assert "verjaardagsfeestje regelen" in h


def test_aanvinkregel_breekt_niet(client, app):
    """De weekmail-zin is te lang voor een .chip-pil; die brak over twee
    regels met de hint als losse bubbel ernaast."""
    with client.session_transaction() as s:
        s["pending_email"] = "ux234@test.be"
    h = client.get("/mijn/start").get_data(as_text=True)
    assert "keuze-regel" in h
    assert 'class="chip"' not in h
