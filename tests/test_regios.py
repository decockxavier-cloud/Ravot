"""Patch 206: vaste streekindeling (fiche klopt vanzelf), provinciefilter op
de routelijst en de Fietsroutes-tab op mobiel."""
from unittest.mock import patch

from app.extensions import db
from app.models import Event, FietsRoute, Setting


def test_streek_uit_vaste_tabel():
    from app.regios import provincie_van_streek, streek_van_gemeente
    assert streek_van_gemeente("Genk") == "Hoge Kempen / Midden-Limburg"
    assert streek_van_gemeente("roeselare") == "Leiestreek"     # hoofdletterong.
    assert streek_van_gemeente("Onbekendegem") is None          # nette terugval
    assert provincie_van_streek("Waasland") == "Oost-Vlaanderen"


def test_promotie_gebruikt_vaste_streek(app):
    """Geen gegokte regio meer: de tabel wint van overerving."""
    from app.models import RouteVoorstel
    with app.app_context():
        from app.services.route_generator import regio_suggestie
        r = FietsRoute(titel="T", slug="rg-1", afstand_km=15, duur_min=90,
                       moeilijkheid="vlak", is_lus=True, pending=True,
                       hidden=False, gemeente="Kasterlee",
                       start_lat=51.24, start_lng=4.96)
        db.session.add(r)
        db.session.commit()
        assert regio_suggestie(r) == "Antwerpse Kempen"


def test_provinciefilter_en_gsm_tab(client, app):
    with app.app_context():
        db.session.add(Setting(key="routes_in_menu", value="1"))
        db.session.add(FietsRoute(titel="Leielus", slug="rg-wvl",
                                  afstand_km=15, duur_min=90,
                                  moeilijkheid="vlak", is_lus=True,
                                  pending=False, hidden=False,
                                  gemeente="Roeselare", regio="Leiestreek",
                                  start_lat=50.95, start_lng=3.12))
        db.session.add(FietsRoute(titel="Kempenlus", slug="rg-ant",
                                  afstand_km=18, duur_min=110,
                                  moeilijkheid="vlak", is_lus=True,
                                  pending=False, hidden=False,
                                  gemeente="Kasterlee",
                                  regio="Antwerpse Kempen",
                                  start_lat=51.24, start_lng=4.96))
        db.session.commit()
    h = client.get("/fietsroutes").get_data(as_text=True)
    assert ">West-Vlaanderen</a>" in h and ">Antwerpen</a>" in h
    assert 'tab-tekst">Fietsroutes' in h                 # bereikbaar op gsm
    h2 = client.get("/fietsroutes?provincie=Antwerpen").get_data(as_text=True)
    assert "Kempenlus" in h2 and "Leielus" not in h2
    h3 = client.get("/fietsroutes?provincie=West-Vlaanderen").get_data(as_text=True)
    assert "Leielus" in h3 and "Kempenlus" not in h3
