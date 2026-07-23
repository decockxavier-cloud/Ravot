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


def dag_blokken(v):
    """Normaliseer een dagwaarde naar een lijst tijdsblokken of None (gesloten).

    Twee formaten leven naast elkaar in de databank:
      oud:   ["11:30", "22:00"]                       (één blok)
      nieuw: [["11:30","14:30"], ["18:00","22:00"]]   (blokken, met pauze)
    """
    if v is None:
        return None
    if not isinstance(v, list) or not v:
        return []
    if len(v) == 2 and all(isinstance(x, str) for x in v):
        return [list(v)]
    return [list(b) for b in v
            if isinstance(b, list) and len(b) == 2 and _parse(b[0]) and _parse(b[1])]


def _voeg_samen(blokken):
    """Sorteer blokken en voeg overlappende/aansluitende samen."""
    blokken = sorted(blokken, key=lambda b: b[0])
    uit = []
    for b in blokken:
        if uit and b[0] <= uit[-1][1]:
            uit[-1][1] = max(uit[-1][1], b[1])
        else:
            uit.append(list(b))
    return uit


def _parse(hhmm):
    try:
        u, m = hhmm.split(":")
        return time(int(u), int(m))
    except (ValueError, AttributeError):
        return None


# OSM/Overture-dagcodes -> onze dagsleutels. Overture gebruikt exact dezelfde
# opening_hours-syntax als OSM (bv. "Mo-Fr 09:00-17:00; Sa 10:00-14:00").
_OSM_DAGEN = {"mo": "ma", "tu": "di", "we": "wo", "th": "do",
              "fr": "vr", "sa": "za", "su": "zo"}
_OSM_VOLGORDE = ["mo", "tu", "we", "th", "fr", "sa", "su"]


def parse_osm_uren(spec):
    """Zet een OSM/Overture opening_hours-string om naar ons dict-formaat
    {"ma": ["09:00","17:00"], ...}. Bewust CONSERVATIEF: begrijpt de courante
    gevallen (dagbereiken, losse dagen, één tijdsblok per dag, 'closed'/'off',
    en 24/7). Bij iets wat we niet zeker kunnen lezen -> die dag overslaan,
    zodat we nooit foute uren tonen. Retourneert {} als er niets bruikbaars is.

    Beperkingen (bewust): meerdere tijdsblokken op één dag pakken we als de
    ruimste opening (vroegste open, laatste sluit); feestdagen (PH), 'sunset',
    weeknummers e.d. negeren we."""
    if not spec or not isinstance(spec, str):
        return {}
    spec = spec.strip().lower()
    if not spec:
        return {}
    if spec in ("24/7", "mo-su 00:00-24:00", "24x7"):
        return {d: [["00:00", "23:59"]] for d in DAGEN}

    resultaat = {}

    def _zet(dagcodes, blok):
        """blok = (open, sluit) of None (=gesloten). Meerdere blokken per dag
        (middagpauze!) blijven bewaard als aparte blokken."""
        for dc in dagcodes:
            nl = _OSM_DAGEN.get(dc)
            if not nl:
                continue
            if blok is None:
                resultaat.setdefault(nl, None)
            else:
                if resultaat.get(nl):
                    resultaat[nl].append([blok[0], blok[1]])
                else:
                    resultaat[nl] = [[blok[0], blok[1]]]

    for regel in spec.split(";"):
        regel = regel.strip()
        if not regel:
            continue
        # feestdagen/weeknummers/maanden e.d. negeren we volledig
        if regel.startswith("ph") or regel.startswith("week") or "sunset" in regel \
                or "sunrise" in regel or "jan" in regel or "dec" in regel:
            continue
        deel = regel.split()
        if not deel:
            continue
        dagdeel = deel[0]
        tijddeel = deel[1] if len(deel) > 1 else ""
        # "Mo-Su,PH 08:00-18:00": feestdag-token in de daglijst mag weg
        dagdeel = dagdeel.replace(",ph", "").replace("ph,", "")
        # "08:00-18:00+" (open einde): het plusje strippen, uren blijven juist
        tijddeel = tijddeel.rstrip("+")
        # Regel zonder dagcodes maar mét geldig tijdsblok = elke dag
        # (bv. "08:00-20:00" of "09:00-13:00,15:00-20:00")
        if dagdeel[:2] not in _OSM_VOLGORDE and "-" in dagdeel \
                and _parse(dagdeel.split(",")[0].partition("-")[0]):
            tijddeel = regel.replace(" ", "").rstrip("+")
            dagdeel = "mo-su"
        gesloten = "off" in regel or "closed" in regel

        # dagcodes uitlezen: "mo-fr", "sa", "mo,we,fr"
        dagcodes = []
        for stuk in dagdeel.split(","):
            stuk = stuk.strip()
            if "-" in stuk:
                a, _, b = stuk.partition("-")
                a, b = a[:2], b[:2]
                if a in _OSM_VOLGORDE and b in _OSM_VOLGORDE:
                    i, j = _OSM_VOLGORDE.index(a), _OSM_VOLGORDE.index(b)
                    rng = _OSM_VOLGORDE[i:j + 1] if i <= j \
                        else _OSM_VOLGORDE[i:] + _OSM_VOLGORDE[:j + 1]
                    dagcodes.extend(rng)
            elif stuk[:2] in _OSM_VOLGORDE:
                dagcodes.append(stuk[:2])
        if not dagcodes:
            continue

        if gesloten:
            _zet(dagcodes, None)
            continue

        # tijdsblokken: "09:00-17:00" of "11:30-14:30,18:00-22:00" (pauze)
        for tb in tijddeel.split(","):
            if "-" not in tb:
                continue
            o, _, s = tb.partition("-")
            o, s = o.strip(), s.strip()
            if s == "24:00":
                s = "23:59"
            if _parse(o) and _parse(s):
                _zet(dagcodes, (o, s))

    for d, v in resultaat.items():
        if v:
            resultaat[d] = _voeg_samen(v)
    # enkel teruggeven als er minstens één open dag bij zit
    if any(v for v in resultaat.values()):
        return resultaat
    return {}


def heeft_uren(ev):
    ou = getattr(ev, "openingsuren", None)
    return bool(ou) and any(dag_blokken(ou.get(d)) for d in DAGEN)


def status(ev, nu=None):
    """('open'|'bijna'|'dicht'|None, sluit_om|None) op moment nu."""
    ou = getattr(ev, "openingsuren", None)
    if not ou:
        return None, None
    if nu is None:
        from ..tijd import nu_lokaal
        nu = nu_lokaal()          # Belgische wandklok: de container draait op UTC
    dag = DAGEN[nu.weekday()]
    blokken = dag_blokken(ou.get(dag))
    if not blokken:
        return "dicht", None
    nu_t = nu.time()
    for o, s in blokken:
        open_t, sluit_t = _parse(o), _parse(s)
        if not open_t or not sluit_t:
            continue
        if open_t <= nu_t < sluit_t:
            rest = (sluit_t.hour * 60 + sluit_t.minute) - (nu_t.hour * 60 + nu_t.minute)
            if rest <= BIJNA_MINUTEN:
                return "bijna", s
            return "open", s
    return "dicht", None   # buiten alle blokken (bv. tijdens de middagpauze)


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
        blokken = dag_blokken(ou.get(d)) if d in ou else None
        if blokken:
            rijen.append((DAG_LABELS[d],
                          " en ".join(f"{o}–{s}" for o, s in blokken)))
        elif d in ou:   # expliciet gesloten
            rijen.append((DAG_LABELS[d], "gesloten"))
        else:
            rijen.append((DAG_LABELS[d], "—"))
    return rijen
