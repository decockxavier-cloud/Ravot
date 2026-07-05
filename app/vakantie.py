"""Vakantiemodus (strategienota fase 2).

- Vlaamse schoolvakanties (vaste data waar mogelijk, herfst/krokus/paasregels).
- Detectie of we in of vlak voor een vakantie zitten → app toont dan een
  vakantie-banner en zet kampen/meerdaagse activiteiten naar voren.
"""
from datetime import date, timedelta

# Vlaamse schoolvakanties. Data per schooljaar; uitbreidbaar.
# (start, eind_inclusief, naam)
SCHOOLVAKANTIES = [
    (date(2025, 10, 27), date(2025, 11, 2), "herfstvakantie"),
    (date(2025, 12, 22), date(2026, 1, 4), "kerstvakantie"),
    (date(2026, 2, 16), date(2026, 2, 22), "krokusvakantie"),
    (date(2026, 4, 6), date(2026, 4, 19), "paasvakantie"),
    (date(2026, 7, 1), date(2026, 8, 31), "zomervakantie"),
    (date(2026, 10, 26), date(2026, 11, 1), "herfstvakantie"),
    (date(2026, 12, 21), date(2027, 1, 3), "kerstvakantie"),
    (date(2027, 2, 15), date(2027, 2, 21), "krokusvakantie"),
    (date(2027, 4, 5), date(2027, 4, 18), "paasvakantie"),
    (date(2027, 7, 1), date(2027, 8, 31), "zomervakantie"),
]


def actieve_vakantie(vandaag=None):
    """Naam van de lopende vakantie, of None."""
    vandaag = vandaag or date.today()
    for start, eind, naam in SCHOOLVAKANTIES:
        if start <= vandaag <= eind:
            return naam
    return None


def komende_vakantie(vandaag=None, binnen_dagen=10):
    """(naam, dagen_tot_start) als er binnenkort een vakantie start, anders None."""
    vandaag = vandaag or date.today()
    for start, eind, naam in SCHOOLVAKANTIES:
        d = (start - vandaag).days
        if 0 < d <= binnen_dagen:
            return naam, d
    return None


def vakantiecontext(vandaag=None):
    """Wat de app moet weten: zit-in-vakantie, komt-eraan, en de banner-tekst."""
    vandaag = vandaag or date.today()
    actief = actieve_vakantie(vandaag)
    if actief:
        return {"actief": True, "naam": actief,
                "banner": f"Het is {actief}! 🎉 Extra tijd om te ravotten — "
                          f"ook meerdaagse activiteiten en kampen staan hieronder."}
    komt = komende_vakantie(vandaag)
    if komt:
        naam, dagen = komt
        wanneer = "morgen" if dagen == 1 else f"over {dagen} dagen"
        return {"actief": False, "naam": naam,
                "banner": f"De {naam} start {wanneer}. 🗓️ Plan alvast iets leuks — "
                          f"kampen raken snel vol."}
    return {"actief": False, "naam": None, "banner": None}


# Kamp-/meerdaags detecteren uit titel + duur
KAMP_WOORDEN = ("kamp", "stage", "workshopreeks", "vakantieatelier", "speelweek",
                "sportkamp", "crea", "meerdaags")


def is_kamp(event):
    titel = (event.title or "").lower()
    if any(w in titel for w in KAMP_WOORDEN):
        return True
    # meerdaags: eind minstens een dag na start
    if event.start and event.end and (event.end.date() - event.start.date()).days >= 1:
        return True
    return False
