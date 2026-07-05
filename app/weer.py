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
