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


def normaliseer(naam):
    """Gemeentenaam vergelijkbaar maken (patch 251).

    Sint-Truiden werd gemeld als 'geen aanbod' terwijl er 152 fiches staan:
    de brondata schrijft gemeenten net anders (koppeltekens, apostroffen,
    hoofdletters, dubbele spaties). Voor de vergelijking strippen we dat weg —
    de echte naam uit Ravot blijft leidend voor wat we tonen.
    """
    import unicodedata
    tekst = unicodedata.normalize("NFKD", (naam or "").strip().lower())
    tekst = "".join(c for c in tekst if not unicodedata.combining(c))
    for teken in ("'", "\u2019", "-", ".", ","):
        tekst = tekst.replace(teken, " ")
    return " ".join(tekst.split())


def naar_contacten(records):
    """Records omzetten naar (gemeente, dienst, email, notitie), ontdubbeld.

    We houden beide plaatsnamen bij: main_city_name is de fusiegemeente,
    city_name de deelgemeente. Ravot kent er soms maar één van, dus proberen
    we ze allebei bij het koppelen.
    """
    uit = []
    gezien = set()
    for rec in records:
        if str(rec.get("deleted", "0")) == "1":
            continue
        naam = (rec.get("name") or "").strip()
        hoofd = (rec.get("main_city_name") or "").strip()
        deel = (rec.get("city_name") or "").strip()
        gemeente = hoofd or deel
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
            "namen": [n for n in (hoofd, deel) if n],
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
    # Zoektabel op genormaliseerde naam, zodat schrijfwijzeverschillen
    # (Sint-Truiden / Sint Truiden / 's Gravenwezel) toch koppelen.
    genormaliseerd = {normaliseer(echt): sleutel
                      for sleutel, echt in bekende_gemeenten.items()}
    genormaliseerd.update({normaliseer(s): s for s in bekende_gemeenten})

    for c in contacten:
        sleutel = None
        for kandidaat in c["namen"] or [c["gemeente"]]:
            direct = kandidaat.strip().lower()
            if direct in bekende_gemeenten:
                sleutel = direct
                break
            gevonden = genormaliseerd.get(normaliseer(kandidaat))
            if gevonden:
                sleutel = gevonden
                break
        if sleutel is None:
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
