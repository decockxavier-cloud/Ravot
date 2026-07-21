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
        return {d: ["00:00", "23:59"] for d in DAGEN}

    resultaat = {}

    def _zet(dagcodes, blok):
        """blok = (open, sluit) of None (=gesloten)."""
        for dc in dagcodes:
            nl = _OSM_DAGEN.get(dc)
            if not nl:
                continue
            if blok is None:
                resultaat.setdefault(nl, None)
            else:
                o, s = blok
                if nl in resultaat and resultaat[nl]:
                    # ruimste opening bewaren bij meerdere blokken
                    resultaat[nl] = [min(resultaat[nl][0], o), max(resultaat[nl][1], s)]
                else:
                    resultaat[nl] = [o, s]

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

        # tijdsblok: "09:00-17:00" (evt. meerdere met komma -> ruimste)
        vroegste, laatste = None, None
        for tb in tijddeel.split(","):
            if "-" not in tb:
                continue
            o, _, s = tb.partition("-")
            o, s = o.strip(), s.strip()
            if s == "24:00":
                s = "23:59"
            if _parse(o) and _parse(s):
                vroegste = o if vroegste is None or o < vroegste else vroegste
                laatste = s if laatste is None or s > laatste else laatste
        if vroegste and laatste:
            _zet(dagcodes, (vroegste, laatste))

    # enkel teruggeven als er minstens één open dag bij zit
    if any(v for v in resultaat.values()):
        return resultaat
    return {}


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
