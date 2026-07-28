"""Deelgemeente → fusiegemeente, op basis van de officiële postcode-indeling.

OpenStreetMap levert bij `addr:city` vaak de deelgemeente (Rumbeke, Gits,
Waardamme…) terwijl gezinnen in fusiegemeenten denken en zoeken (Roeselare,
Hooglede, Oostkamp). We tonen daarom "Roeselare (Rumbeke)": de fusiegemeente als
hoofdnaam (voor zoeken/filteren), de deelgemeente als bijschrift.

De koppeling postcode → fusiegemeente komt uit de officiële Statbel/bpost-
nomenclatuur (vanaf 1/1/2025), opgeslagen in
app/data/postcode_fusiegemeente.json. Zo is elke Vlaamse (en Brusselse)
postcode correct gekoppeld, inclusief de fusies van 2025, zonder giswerk.

De deelgemeente kennen we via de naam die de bron (OSM) meegaf. Klopt die naam
met de fusiegemeente, dan is er geen deelgemeente om te tonen.
"""
import json
import os
import unicodedata

_DATA = os.path.join(os.path.dirname(__file__), "data",
                     "postcode_fusiegemeente.json")

with open(_DATA, encoding="utf-8") as _f:
    _PC_FUSIE = json.load(_f)


def _norm(naam):
    if not naam:
        return ""
    n = unicodedata.normalize("NFKD", str(naam))
    n = "".join(c for c in n if not unicodedata.combining(c))
    return n.strip().lower()


def fusiegemeente_van_postcode(postcode):
    """De officiële fusiegemeente voor een postcode, of None als onbekend."""
    if not postcode:
        return None
    return _PC_FUSIE.get(str(postcode).strip())


def normaliseer(postcode, bron_naam):
    """Bepaal (fusiegemeente, deelgemeente) uit een postcode en de naam die de
    bron aanleverde.

    - Kennen we de postcode? Dan is de fusiegemeente die uit de officiële lijst.
      Verschilt de bron-naam ervan, dan is die bron-naam de deelgemeente.
    - Kennen we de postcode niet? Dan houden we de bron-naam als gemeente en
      hebben we geen deelgemeente (beter dan gokken).

    Retourneert (gemeente, deelgemeente_of_None).
    """
    fusie = fusiegemeente_van_postcode(postcode)
    if not fusie:
        return (bron_naam or None), None
    if bron_naam and _norm(bron_naam) != _norm(fusie):
        return fusie, bron_naam
    return fusie, None


def toon_gemeente(gemeente, deelgemeente=None):
    """Weergavenaam: 'Roeselare (Rumbeke)' als er een afwijkende deelgemeente is,
    anders gewoon 'Roeselare'."""
    if deelgemeente and _norm(deelgemeente) != _norm(gemeente):
        return f"{gemeente} ({deelgemeente})"
    return gemeente or ""
