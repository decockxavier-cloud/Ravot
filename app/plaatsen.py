"""Canonieke plaatsen voor autocomplete en geo-resolutie.

Bron: GeoNames (via de pgeocode-mirror op GitHub), eenmalig gegenereerd naar
app/data/plaatsen.json. Dekt heel België en Nederland plus Noord-Frankrijk
(regio Hauts-de-France — de grensstreek). Elke rij: [postcode, naam, lat, lng, land].

PLAATSEN blijft (postcode, naam, lat, lng) voor bestaande oproepers; het land
zit apart in PLAATS_LAND als je het nodig hebt.
"""
import json
import pathlib

_DATA = pathlib.Path(__file__).with_name("data") / "plaatsen.json"

try:
    _RUW = json.loads(_DATA.read_text(encoding="utf-8"))
except Exception:
    _RUW = []

# (postcode, naam, lat, lng) — ongewijzigd formaat voor bestaande code.
PLAATSEN = [(r[0], r[1], r[2], r[3]) for r in _RUW]
# postcode -> landcode (BE/NL/FR), voor wie het onderscheid nodig heeft.
PLAATS_LAND = {r[0]: r[4] for r in _RUW}
