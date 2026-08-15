"""Toeristische infokantoren uit de open data van Toerisme Vlaanderen (patch 249).

Officiële dataset onder Modellicentie Gratis Hergebruik. Dit vervangt het
handmatig verzamelen van adressen: één klik haalt de actuele lijst op.

De koppeling naar Ravot gebeurt op gemeentenaam. Wat de beheerder zelf invulde
wint altijd — een open dataset kan verouderd zijn, en een adres dat jij
persoonlijk kreeg is meer waard dan een generiek infoadres.
"""
import requests

BRON_URL = ("https://opendata.visitflanders.org/tourist/services-facilities/"
            "tourist-info-centers_v2.json")
BRON_NAAM = "Toerisme Vlaanderen (open data infokantoren)"
UA = {"User-Agent": "Ravot.be/1.0 (info@ravot.be)"}


def _diepste_lijst(data):
    """De Datatank geeft soms een lijst, soms een dict met de lijst erin."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for waarde in data.values():
            gevonden = _diepste_lijst(waarde)
            if gevonden is not None:
                return gevonden
    return None


def haal_records(max_paginas=20):
    """Alle actieve records ophalen (gepagineerd)."""
    records = []
    for pagina in range(1, max_paginas + 1):
        antw = requests.get(BRON_URL, headers=UA, timeout=60,
                            params={"page": pagina, "page_size": 500})
        antw.raise_for_status()
        batch = _diepste_lijst(antw.json()) or []
        records.extend(batch)
        if len(batch) < 500:
            break
    return records


def naar_contacten(records):
    """Records omzetten naar (gemeente, dienst, email, notitie), ontdubbeld."""
    uit = []
    gezien = set()
    for rec in records:
        if str(rec.get("deleted", "0")) == "1":
            continue
        naam = (rec.get("name") or "").strip()
        gemeente = (rec.get("main_city_name")
                    or rec.get("city_name") or "").strip()
        email = (rec.get("email") or "").strip()
        if not naam or not gemeente or "@" not in email:
            continue
        sleutel = (naam.lower(), gemeente.lower())
        if sleutel in gezien:
            continue
        gezien.add(sleutel)
        notitie = []
        telefoon = (rec.get("phone1") or "").strip()
        if telefoon:
            notitie.append(telefoon)
        if rec.get("sub_type"):
            notitie.append(str(rec["sub_type"]))
        gewijzigd = (rec.get("changed_time") or "")[:10]
        if gewijzigd:
            notitie.append(f"bron bijgewerkt {gewijzigd}")
        uit.append({
            "gemeente": gemeente,
            "dienst": naam[:160],
            "email": email[:255],
            "notitie": " · ".join(notitie)[:4000] or None,
        })
    return uit


def importeer(bekende_gemeenten, maak_token):
    """Contacten aanvullen. bekende_gemeenten: {kleine_letters: echte naam}.

    Retourneert (nieuw, aangevuld, overgeslagen, onbekend, onbekende_namen).
    Wat de beheerder zelf invulde blijft ongemoeid.
    """
    from ..extensions import db
    from ..models import GemeenteContact
    records = haal_records()
    contacten = naar_contacten(records)
    nieuw = aangevuld = overgeslagen = onbekend = 0
    onbekende_namen = []
    for c in contacten:
        sleutel = c["gemeente"].strip().lower()
        if sleutel not in bekende_gemeenten:
            onbekend += 1
            if len(onbekende_namen) < 25:
                onbekende_namen.append(c["gemeente"])
            continue
        rij = db.session.get(GemeenteContact, sleutel)
        if rij is None:
            rij = GemeenteContact(gemeente=sleutel)
            db.session.add(rij)
            nieuw += 1
        elif rij.email:
            overgeslagen += 1        # eigen invoer wint van open data
            continue
        else:
            aangevuld += 1
        rij.email = c["email"]
        rij.dienst = c["dienst"]
        if c["notitie"] and not rij.notitie:
            rij.notitie = c["notitie"]
        maak_token(rij)
    db.session.commit()
    return nieuw, aangevuld, overgeslagen, onbekend, onbekende_namen
