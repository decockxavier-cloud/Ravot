"""Weerkoppeling (strategienota fase 3, veilig deel).

Gebruikt Open-Meteo (gratis, geen API-key). Bij regen krijgen binnen-activiteiten
een scorebonus zodat ze naar boven komen. Alles met korte timeout + in-memory
cache zodat een trage of onbereikbare weer-API de app nooit vertraagt.
"""
import time
from datetime import date

import requests
from flask import current_app

_cache = {}  # (afgeronde lat, lng, dag) -> (regenkans, tijdstip)
_TTL = 3600  # 1 uur


def regenkans(lat, lng, dag=None):
    """Regenkans (0-100) voor een locatie op een dag. None bij fout/onbekend."""
    if lat is None or lng is None:
        return None
    dag = dag or date.today()
    key = (round(lat, 1), round(lng, 1), dag.isoformat())
    hit = _cache.get(key)
    if hit and time.time() - hit[1] < _TTL:
        return hit[0]
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": round(lat, 2), "longitude": round(lng, 2),
                    "daily": "precipitation_probability_max",
                    "start_date": dag.isoformat(), "end_date": dag.isoformat(),
                    "timezone": "Europe/Brussels"},
            timeout=4,
        )
        r.raise_for_status()
        vals = (r.json().get("daily") or {}).get("precipitation_probability_max") or []
        kans = vals[0] if vals else None
    except Exception as exc:  # nooit de app breken op weer
        current_app.logger.info("weer: geen data (%s)", str(exc)[:80])
        kans = None
    _cache[key] = (kans, time.time())
    return kans


def weerbonus(event, lat, lng, dag=None):
    """Scorebonus-factor: bij >50% regenkans krijgen binnen-events +30%,
    buiten-events een lichte penalty. Anders neutraal (1.0)."""
    kans = regenkans(lat, lng, dag)
    if kans is None:
        return 1.0
    if kans >= 50:
        return 1.3 if event.indoor else 0.85
    if kans <= 20:  # mooi weer → buiten licht bevoordelen
        return 1.1 if not event.indoor else 1.0
    return 1.0


# WMO-weathercode -> (emoji, korte omschrijving). Bewust grofmazig — ouders
# willen "regent het en hoe warm?" weten, geen meteorologie.
_WMO = [
    ((0,), "☀️", "zonnig"),
    ((1, 2), "🌤️", "licht bewolkt"),
    ((3,), "☁️", "bewolkt"),
    ((45, 48), "🌫️", "mistig"),
    ((51, 53, 55, 56, 57), "🌦️", "motregen"),
    ((61, 63, 65, 66, 67, 80, 81, 82), "🌧️", "regen"),
    ((71, 73, 75, 77, 85, 86), "🌨️", "sneeuw"),
    ((95, 96, 99), "⛈️", "onweer"),
]


def _wmo(code):
    for codes, emoji, label in _WMO:
        if code in codes:
            return emoji, label
    return "🌤️", ""


_vcache = {}  # (lat, lng, dag) -> (dict, tijdstip)


def voorspelling(lat, lng, dag=None):
    """Volledig dagbericht: {emoji, label, tmax, tmin, regenkans}. None bij
    fout of ontbrekende locatie. Zelfde cache-aanpak als regenkans."""
    if lat is None or lng is None:
        return None
    dag = dag or date.today()
    # Open-Meteo voorspelt tot ~16 dagen vooruit; daarbuiten niets tonen.
    if (dag - date.today()).days > 14 or dag < date.today():
        return None
    key = (round(lat, 1), round(lng, 1), dag.isoformat())
    hit = _vcache.get(key)
    if hit and time.time() - hit[1] < _TTL:
        return hit[0]
    uit = None
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": round(lat, 2), "longitude": round(lng, 2),
                    "daily": "weathercode,temperature_2m_max,temperature_2m_min,"
                             "precipitation_probability_max",
                    "start_date": dag.isoformat(), "end_date": dag.isoformat(),
                    "timezone": "Europe/Brussels"},
            timeout=4,
        )
        r.raise_for_status()
        d = r.json().get("daily") or {}

        def eerste(naam):
            vals = d.get(naam) or []
            return vals[0] if vals else None

        code = eerste("weathercode")
        if code is not None:
            emoji, label = _wmo(int(code))
            uit = {"emoji": emoji, "label": label,
                   "tmax": eerste("temperature_2m_max"),
                   "tmin": eerste("temperature_2m_min"),
                   "regenkans": eerste("precipitation_probability_max"),
                   "datum": dag}
    except Exception as exc:  # nooit de app breken op weer
        current_app.logger.info("weer: geen voorspelling (%s)", str(exc)[:80])
    _vcache[key] = (uit, time.time())
    return uit
