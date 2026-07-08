"""Autocomplete stad/postcode: canonieke offline suggesties."""
from app.plaatsen import PLAATSEN


def test_dataset_is_geladen():
    assert len(PLAATSEN) > 2500
    assert ("9000", "Gent") in [(p[0], p[1]) for p in PLAATSEN]


def test_api_postcode_prefix(client, app):
    d = client.get("/api/plaatsen?q=90").get_json()
    labels = [s["label"] for s in d["suggesties"]]
    assert any("Gent (9000)" == l for l in labels)
    assert len(labels) <= 8


def test_api_naam_prefix_accentongevoelig(client, app):
    d = client.get("/api/plaatsen?q=gent").get_json()
    assert any(s["gemeente"] == "Gent" for s in d["suggesties"])
    d2 = client.get("/api/plaatsen?q=heverle").get_json()
    assert any(s["gemeente"] == "Heverlee" for s in d2["suggesties"])


def test_api_korte_query_leeg(client, app):
    assert client.get("/api/plaatsen?q=g").get_json() == {"suggesties": []}


def test_geo_gebruikt_offline_lijst_zonder_netwerk(app):
    """Plaatsnaam-resolutie werkt offline (geen Nominatim nodig)."""
    from app import geo
    with app.app_context():
        coord = geo.zoek_centrum("Roeselare")
        assert coord is not None and abs(coord[0] - 50.94) < 0.1
        # deelgemeente ook
        assert geo.zoek_centrum("Rumbeke") is not None


def test_zoek_centrum_gebruikt_postcode_uit_label(app):
    """'Beveren (8800)' moet naar 8800 (bij Roeselare) zoomen, niet naar een
    andere Beveren op naam."""
    from app import geo
    with app.app_context():
        via_label = geo.zoek_centrum("Beveren (8800)")
        via_postcode = geo.zoek_centrum("8800")
        assert via_label is not None
        assert via_label == via_postcode          # label -> zelfde als de postcode
        # en dat is niet de Antwerpse Beveren (9120)
        anders = geo.zoek_centrum("9120")
        if anders:
            assert abs(via_label[1] - anders[1]) > 0.3   # duidelijk andere lengtegraad
