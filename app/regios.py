"""Toeristische streken van Vlaanderen (patch 206).

Eén vaste, door de redactie gecureerde indeling: gemeente -> streek en
streek -> provincie. Dit is bewust een tabel in code en geen slimme
afleiding — de regio op een routefiche moet gewoon kloppen, niet geraden
worden. Gemeenten die hier niet in staan vallen terug op overerving van
nabijgelegen routes (zie regio_suggestie).
"""

STREEK_PROVINCIE = {
    # West-Vlaanderen
    "Leiestreek": "West-Vlaanderen",
    "Brugse Ommeland": "West-Vlaanderen",
    "Westhoek": "West-Vlaanderen",
    "De Kust": "West-Vlaanderen",
    # Oost-Vlaanderen
    "Vlaamse Ardennen": "Oost-Vlaanderen",
    "Meetjesland / Gentse rand": "Oost-Vlaanderen",
    "Scheldeland": "Oost-Vlaanderen",
    "Waasland": "Oost-Vlaanderen",
    # Antwerpen
    "Antwerpse Kempen": "Antwerpen",
    "Mechelen & Rivierenland": "Antwerpen",
    "Antwerpen & groene rand": "Antwerpen",
    # Vlaams-Brabant
    "Hageland": "Vlaams-Brabant",
    "Leuven & Dijleland": "Vlaams-Brabant",
    "Groene Gordel": "Vlaams-Brabant",
    # Limburg
    "Haspengouw": "Limburg",
    "Hoge Kempen / Midden-Limburg": "Limburg",
    "Noord-Limburg / Bosland": "Limburg",
    "Maasland": "Limburg",
}

GEMEENTE_STREEK = {
    # ── West-Vlaanderen ──
    "Roeselare": "Leiestreek", "Izegem": "Leiestreek", "Tielt": "Leiestreek",
    "Kortrijk": "Leiestreek", "Waregem": "Leiestreek",
    "Hooglede": "Leiestreek", "Ingelmunster": "Leiestreek",
    "Wevelgem": "Leiestreek", "Menen": "Leiestreek", "Deerlijk": "Leiestreek",
    "Harelbeke": "Leiestreek", "Kuurne": "Leiestreek",
    "Brugge": "Brugse Ommeland", "Torhout": "Brugse Ommeland",
    "Oostkamp": "Brugse Ommeland", "Zedelgem": "Brugse Ommeland",
    "Beernem": "Brugse Ommeland", "Jabbeke": "Brugse Ommeland",
    "Ieper": "Westhoek", "Diksmuide": "Westhoek", "Poperinge": "Westhoek",
    "Veurne": "Westhoek", "Heuvelland": "Westhoek", "Zonnebeke": "Westhoek",
    "Kortemark": "Westhoek", "Houthulst": "Westhoek", "Langemark-Poelkapelle": "Westhoek",
    "Oostende": "De Kust", "Nieuwpoort": "De Kust", "Blankenberge": "De Kust",
    "Knokke-Heist": "De Kust", "De Panne": "De Kust", "Koksijde": "De Kust",
    "Middelkerke": "De Kust", "De Haan": "De Kust", "Bredene": "De Kust",
    # ── Oost-Vlaanderen ──
    "Oudenaarde": "Vlaamse Ardennen", "Geraardsbergen": "Vlaamse Ardennen",
    "Ronse": "Vlaamse Ardennen", "Zottegem": "Vlaamse Ardennen",
    "Kluisbergen": "Vlaamse Ardennen", "Brakel": "Vlaamse Ardennen",
    "Gent": "Meetjesland / Gentse rand", "Deinze": "Meetjesland / Gentse rand",
    "Eeklo": "Meetjesland / Gentse rand", "Maldegem": "Meetjesland / Gentse rand",
    "Aalter": "Meetjesland / Gentse rand", "Evergem": "Meetjesland / Gentse rand",
    "Dendermonde": "Scheldeland", "Berlare": "Scheldeland",
    "Wetteren": "Scheldeland", "Temse": "Scheldeland", "Hamme": "Scheldeland",
    "Sint-Niklaas": "Waasland", "Lokeren": "Waasland", "Stekene": "Waasland",
    "Beveren": "Waasland", "Sint-Gillis-Waas": "Waasland",
    # ── Antwerpen ──
    "Kasterlee": "Antwerpse Kempen", "Mol": "Antwerpse Kempen",
    "Herentals": "Antwerpse Kempen", "Geel": "Antwerpse Kempen",
    "Turnhout": "Antwerpse Kempen", "Retie": "Antwerpse Kempen",
    "Dessel": "Antwerpse Kempen", "Balen": "Antwerpse Kempen",
    "Mechelen": "Mechelen & Rivierenland", "Lier": "Mechelen & Rivierenland",
    "Bornem": "Mechelen & Rivierenland", "Puurs-Sint-Amands": "Mechelen & Rivierenland",
    "Willebroek": "Mechelen & Rivierenland", "Duffel": "Mechelen & Rivierenland",
    "Antwerpen": "Antwerpen & groene rand", "Brasschaat": "Antwerpen & groene rand",
    "Schoten": "Antwerpen & groene rand", "Schilde": "Antwerpen & groene rand",
    "Kapellen": "Antwerpen & groene rand", "Zoersel": "Antwerpen & groene rand",
    # ── Vlaams-Brabant ──
    "Diest": "Hageland", "Aarschot": "Hageland",
    "Scherpenheuvel-Zichem": "Hageland", "Tienen": "Hageland",
    "Landen": "Hageland", "Zoutleeuw": "Hageland",
    "Leuven": "Leuven & Dijleland", "Oud-Heverlee": "Leuven & Dijleland",
    "Haacht": "Leuven & Dijleland", "Rotselaar": "Leuven & Dijleland",
    "Bertem": "Leuven & Dijleland", "Herent": "Leuven & Dijleland",
    "Tervuren": "Groene Gordel", "Halle": "Groene Gordel",
    "Beersel": "Groene Gordel", "Overijse": "Groene Gordel",
    "Hoeilaart": "Groene Gordel", "Dilbeek": "Groene Gordel",
    "Grimbergen": "Groene Gordel", "Meise": "Groene Gordel",
    # ── Limburg ──
    "Sint-Truiden": "Haspengouw", "Tongeren-Borgloon": "Haspengouw",
    "Tongeren": "Haspengouw", "Borgloon": "Haspengouw",
    "Bilzen-Hoeselt": "Haspengouw", "Bilzen": "Haspengouw",
    "Riemst": "Haspengouw", "Heers": "Haspengouw",
    "Genk": "Hoge Kempen / Midden-Limburg", "Hasselt": "Hoge Kempen / Midden-Limburg",
    "Maasmechelen": "Hoge Kempen / Midden-Limburg",
    "Zutendaal": "Hoge Kempen / Midden-Limburg", "As": "Hoge Kempen / Midden-Limburg",
    "Lommel": "Noord-Limburg / Bosland", "Pelt": "Noord-Limburg / Bosland",
    "Peer": "Noord-Limburg / Bosland", "Hechtel-Eksel": "Noord-Limburg / Bosland",
    "Hamont-Achel": "Noord-Limburg / Bosland", "Bocholt": "Noord-Limburg / Bosland",
    "Maaseik": "Maasland", "Dilsen-Stokkem": "Maasland", "Lanaken": "Maasland",
    "Kinrooi": "Maasland",
}

_LOWER = {g.lower(): s for g, s in GEMEENTE_STREEK.items()}


def streek_van_gemeente(gemeente):
    """Streek voor een gemeente, of None als ze niet in de tabel staat."""
    if not gemeente:
        return None
    return _LOWER.get(gemeente.strip().lower())


def provincie_van_streek(streek):
    return STREEK_PROVINCIE.get((streek or "").strip())
