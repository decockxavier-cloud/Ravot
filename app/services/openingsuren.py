"""Openingsuren — eenvoudige weergave en open/dicht-status met kleurcode.

Model: Event.openingsuren = {"ma": ["09:00","17:00"], ..., "zo": None}
  - [open, sluit] = open die dag tussen die uren
  - None            = gesloten die dag
  - ontbrekende dag = onbekend
Leeg/None dict = geen openingsuren bekend (we tonen dan niks).

Status voor de kleurcode:
  "open"      groen   — nu open
  "bijna"     oranje  — open, maar sluit binnen het waarschuwingsvenster
  "dicht"     rood    — nu gesloten
  None                — onbekend (geen badge)
"""
from datetime import datetime, time

DAGEN = ["ma", "di", "wo", "do", "vr", "za", "zo"]
DAG_LABELS = {"ma": "Maandag", "di": "Dinsdag", "wo": "Woensdag",
              "do": "Donderdag", "vr": "Vrijdag", "za": "Zaterdag",
              "zo": "Zondag"}

BIJNA_MINUTEN = 60   # "sluit binnenkort" als er < 60 min rest


def _parse(hhmm):
    try:
        u, m = hhmm.split(":")
        return time(int(u), int(m))
    except (ValueError, AttributeError):
        return None


def heeft_uren(ev):
    ou = getattr(ev, "openingsuren", None)
    return bool(ou) and any(ou.get(d) for d in DAGEN)


def status(ev, nu=None):
    """('open'|'bijna'|'dicht'|None, sluit_om|None) op moment nu."""
    ou = getattr(ev, "openingsuren", None)
    if not ou:
        return None, None
    nu = nu or datetime.now()
    dag = DAGEN[nu.weekday()]
    vandaag = ou.get(dag)
    if not vandaag or len(vandaag) != 2:
        return "dicht", None
    open_t, sluit_t = _parse(vandaag[0]), _parse(vandaag[1])
    if not open_t or not sluit_t:
        return None, None
    nu_t = nu.time()
    if open_t <= nu_t < sluit_t:
        # resterende minuten tot sluiten
        rest = (sluit_t.hour * 60 + sluit_t.minute) - (nu_t.hour * 60 + nu_t.minute)
        if rest <= BIJNA_MINUTEN:
            return "bijna", vandaag[1]
        return "open", vandaag[1]
    return "dicht", None


def status_badge(ev, nu=None):
    """Dict voor weergave, of None. {klasse, tekst, kleur}."""
    st, sluit = status(ev, nu=nu)
    if st is None:
        return None
    if st == "open":
        return {"klasse": "open", "tekst": f"Nu open tot {sluit}"}
    if st == "bijna":
        return {"klasse": "bijna", "tekst": f"Sluit binnenkort ({sluit})"}
    return {"klasse": "dicht", "tekst": "Nu gesloten"}


def uren_overzicht(ev):
    """Lijst [(daglabel, 'HH:MM–HH:MM'|'gesloten')] voor de fiche."""
    ou = getattr(ev, "openingsuren", None) or {}
    rijen = []
    for d in DAGEN:
        v = ou.get(d)
        if v and len(v) == 2:
            rijen.append((DAG_LABELS[d], f"{v[0]}–{v[1]}"))
        elif d in ou:   # expliciet gesloten
            rijen.append((DAG_LABELS[d], "gesloten"))
        else:
            rijen.append((DAG_LABELS[d], "—"))
    return rijen
