"""Patch 257: filters op de routelijst + leesbare overzichtskaart.

De kaart toonde 62 losse pins plus alle streeknamen tegelijk, uitgezoomd over
half West-Europa — de pins stapelden en de labels lagen over elkaar. Nu één
bel per streek met een telling, en de routes verschijnen bij inzoomen.

Filters: afstand is voor een gezin de belangrijkste keuze; buggy en heuvels
zijn harde eisen.
"""
import re

from app.extensions import db
from app.models import FietsRoute

DATA = [("Kort vlak", 8, 40, True, "Westhoek"),
        ("Middel", 22, 60, True, "Westhoek"),
        ("Lang heuvel", 45, 320, False, "Vlaamse Ardennen"),
        ("Ongemeten", 18, None, False, "Leiestreek")]


def _routes(app):
    with app.app_context():
        for i, (titel, km, klim, buggy, reg) in enumerate(DATA):
            db.session.add(FietsRoute(
                titel=titel, slug=f"rf257-{i}", afstand_km=km, duur_min=km * 6,
                moeilijkheid="vlak", is_lus=True, pending=False, hidden=False,
                hoogte_m=klim, buggyvriendelijk=buggy, regio=reg,
                start_lat=50.9 + i * 0.1, start_lng=3.1 + i * 0.1,
                geometrie=[[50.9, 3.1], [50.91, 3.11]],
                routebeschrijving="Knooppunten: 1 – 2"))
        db.session.commit()


def _aantal(client, url):
    h = client.get(url).get_data(as_text=True)
    return len(set(re.findall(r"/fietsroutes/(rf257-\d)", h)))


def test_filter_op_afstand(client, app):
    _routes(app)
    assert _aantal(client, "/fietsroutes") == 4
    assert _aantal(client, "/fietsroutes?lengte=kort") == 1        # 8 km
    assert _aantal(client, "/fietsroutes?lengte=middel") == 2      # 18 en 22
    assert _aantal(client, "/fietsroutes?lengte=lang") == 1        # 45 km


def test_filter_op_buggy_en_hoogte(client, app):
    _routes(app)
    assert _aantal(client, "/fietsroutes?buggy=1") == 2
    assert _aantal(client, "/fietsroutes?vlak=vlak") == 2
    assert _aantal(client, "/fietsroutes?vlak=heuvels") == 1


def test_ongemeten_hoogte_belooft_niets(client, app):
    """Leeg = niet gemeten; die route mag niet als 'vlak' verschijnen."""
    _routes(app)
    h = client.get("/fietsroutes?vlak=vlak").get_data(as_text=True)
    assert "Ongemeten" not in h
    h2 = client.get("/fietsroutes?vlak=heuvels").get_data(as_text=True)
    assert "Ongemeten" not in h2


def test_filters_zijn_combineerbaar(client, app):
    _routes(app)
    assert _aantal(client, "/fietsroutes?lengte=kort&buggy=1") == 1
    assert _aantal(client, "/fietsroutes?lengte=lang&buggy=1") == 0
    # en blijven samengaan met de streekfilter
    assert _aantal(client, "/fietsroutes?regio=Westhoek&lengte=kort") == 1


def test_kaart_krijgt_streektellingen(client, app):
    _routes(app)
    h = client.get("/fietsroutes").get_data(as_text=True)
    regios = re.search(r"data-regios='([^']+)'", h).group(1)
    assert '"aantal": 2' in regios or '&#34;aantal&#34;: 2' in regios


def test_kaartscript_blijft_leesbaar(client, app):
    """Op een telefoon passen vijftien streeknamen niet naast elkaar; dan
    tonen we enkel het aantal, en voegen we bellen samen die tóch overlappen."""
    with open("app/static/js/routes_kaart.js", encoding="utf-8") as f:
        src = f.read()
    assert "streek-bel" in src            # bellen i.p.v. losse labels
    assert "streek-bol" in src            # smal scherm: enkel het aantal
    assert "samengevoegd" in src          # overlappende bellen optellen
    assert "zoomend" in src and "moveend" in src
    assert "DREMPEL" in src
