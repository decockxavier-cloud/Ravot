"""Patch 256: onderweg-hulp op de routekaart.

Feedback van ouders: ze willen tijdens het fietsen zien waar ze op de lus
zitten, hoe ze aan de start geraken en welke kant ze op moeten. Een GPX helpt
alleen wie al met Komoot of Garmin werkt.

De richting en de eigen positie lossen we op in de kaart zelf; navigeren náár
de start gaat naar Google of Apple Kaarten. De volledige route exporteren naar
Google Maps kan bewust niet: dat herberekent het traject via zijn eigen
wegennet en dan klopt de knooppuntenroute niet meer.
"""
from app.extensions import db
from app.models import FietsRoute


def _route(app, **kw):
    with app.app_context():
        db.session.add(FietsRoute(
            titel="Lus", slug="ow256", afstand_km=18, duur_min=110,
            moeilijkheid="vlak", is_lus=True, pending=False, hidden=False,
            geometrie=[[50.94, 3.12], [50.95, 3.13], [50.94, 3.12]],
            routebeschrijving="Knooppunten: 74 – 32", **kw))
        db.session.commit()


def test_waar_ben_ik_knop_staat_bij_de_kaart(client, app):
    _route(app, start_lat=50.94, start_lng=3.12)
    h = client.get("/fietsroutes/ow256").get_data(as_text=True)
    assert "mijn-positie" in h
    # bij de kaart, niet onderaan bij Praktisch
    assert h.index("mijn-positie") < h.index("Praktisch")


def test_navigeren_naar_de_start(client, app):
    _route(app, start_lat=50.94, start_lng=3.12)
    h = client.get("/fietsroutes/ow256").get_data(as_text=True)
    assert "travelmode=bicycling" in h          # fietsmodus, geen auto
    assert "maps.apple.com" in h                # ook voor iPhone-gebruikers
    assert h.count("maps/dir/?api=1") == 1      # niet dubbel op de pagina


def test_zonder_startpunt_geen_navigatieknoppen(client, app):
    _route(app)
    h = client.get("/fietsroutes/ow256").get_data(as_text=True)
    assert "maps.apple.com" not in h
    assert "mijn-positie" in h                  # positie kan wel altijd


def test_privacy_wordt_uitgelegd(client, app):
    """De gps-positie blijft in de browser; dat hoort er expliciet bij."""
    _route(app, start_lat=50.94, start_lng=3.12)
    h = client.get("/fietsroutes/ow256").get_data(as_text=True)
    assert "blijft op je telefoon" in h


def test_kaartscript_kent_richting_en_positie():
    with open("app/static/js/route-kaart.js", encoding="utf-8") as f:
        src = f.read()
    assert "route-pijl" in src                  # rijrichting op het tracé
    assert "watchPosition" in src               # live positie
    assert "clearWatch" in src                  # en netjes weer uitzetten


def test_pijlen_wijzen_de_juiste_kant_op():
    """p261: met de oude formule (90 - hoek) wezen noord en zuid precies
    verkeerd om — erger dan geen pijl, want je stuurt iemand de andere kant
    uit. ➤ wijst standaard naar oost, dus de rotatie is kompas - 90."""
    import math
    with open("app/static/js/route-kaart.js", encoding="utf-8") as f:
        src = f.read()
    assert "kompas - 90" in src
    assert "Math.cos(a[0]" in src               # lengtegraadcorrectie

    def rotatie(a, b):
        k = math.cos(math.radians(a[0]))
        kompas = math.degrees(math.atan2((b[1] - a[1]) * k, b[0] - a[0]))
        return round((kompas - 90) % 360)

    assert rotatie((51, 3), (51, 4)) == 0       # oost
    assert rotatie((51, 3), (52, 3)) == 270     # noord
    assert rotatie((51, 3), (51, 2)) == 180     # west
    assert rotatie((51, 3), (50, 3)) == 90      # zuid


def test_pijl_komt_los_van_de_kaart():
    with open("app/static/css/ravot.css", encoding="utf-8") as f:
        css = f.read()
    blok = css[css.index(".route-pijl {"):css.index(".route-pijl {") + 400]
    assert "background: #fff" in blok
    assert "border-radius: 50%" in blok


def test_pijlen_wijzen_de_juiste_kant_op():
    """p261: met de oude formule (90 - hoek) wezen noord en zuid precies
    verkeerd om — erger dan geen pijl, want je stuurt iemand de andere kant
    uit. ➤ wijst standaard naar oost, dus de rotatie is kompas - 90."""
    import math
    with open("app/static/js/route-kaart.js", encoding="utf-8") as f:
        src = f.read()
    assert "kompas - 90" in src
    assert "Math.cos(a[0]" in src               # lengtegraadcorrectie

    def rotatie(a, b):
        k = math.cos(math.radians(a[0]))
        kompas = math.degrees(math.atan2((b[1] - a[1]) * k, b[0] - a[0]))
        return round((kompas - 90) % 360)

    assert rotatie((51, 3), (51, 4)) == 0       # oost
    assert rotatie((51, 3), (52, 3)) == 270     # noord
    assert rotatie((51, 3), (51, 2)) == 180     # west
    assert rotatie((51, 3), (50, 3)) == 90      # zuid


def test_pijl_komt_los_van_de_kaart():
    with open("app/static/css/ravot.css", encoding="utf-8") as f:
        css = f.read()
    blok = css[css.index(".route-pijl {"):css.index(".route-pijl {") + 400]
    assert "background: #fff" in blok
    assert "border-radius: 50%" in blok
