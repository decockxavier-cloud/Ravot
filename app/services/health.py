"""Statuscontroles voor het health-dashboard in /beheer/status.

Elke check retourneert {"naam", "ok" (True/False/None=n.v.t.), "detail", "ms"}.
Alle externe pings hebben korte timeouts (max 4s) en falen stil — een haperende
dienst mag het dashboard zelf nooit onderuit halen.
"""
import time

import requests
from flask import current_app

from ..extensions import db


def _ping(fn):
    """Voer een check uit en meet de duur; vang alles af."""
    t0 = time.monotonic()
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {str(e)[:120]}"
    ms = int((time.monotonic() - t0) * 1000)
    return ok, detail, ms


def _db():
    db.session.execute(db.text("SELECT 1"))
    return True, "verbonden"


def _redis():
    url = current_app.config.get("RATELIMIT_STORAGE_URI") \
        or current_app.config.get("REDIS_URL") or ""
    if not url.startswith("redis"):
        return None, "niet in gebruik (limiter draait in-memory)"
    import redis as redislib
    redislib.from_url(url, socket_timeout=2).ping()
    return True, "verbonden"


def _smtp():
    host = current_app.config.get("SMTP_HOST") or ""
    if not host:
        return False, "console-modus — mails verschijnen enkel in de log (SMTP_HOST leeg)"
    return True, f"geconfigureerd ({host})"


def _mollie():
    from . import mollie
    if not mollie.actief():
        return False, "geen MOLLIE_API_KEY in .env — uitbaters zien de mail-terugval"
    key = current_app.config["MOLLIE_API_KEY"]
    r = requests.get("https://api.mollie.com/v2/methods",
                     headers={"Authorization": f"Bearer {key}"}, timeout=4)
    if r.status_code == 200:
        modus = "testmodus" if key.startswith("test_") else "live"
        return True, f"API ok ({modus})"
    return False, f"API antwoordt {r.status_code}"


def _odoo():
    from . import odoo
    if not odoo.actief():
        return False, "niet geconfigureerd (ODOO_URL/DB/USER/API_KEY in .env)"
    uid = odoo._login()
    return (True, f"login ok (uid {uid})") if uid else (False, "login geweigerd")


def _ollama():
    url = (current_app.config.get("OLLAMA_URL") or "http://ollama:11434").rstrip("/")
    r = requests.get(f"{url}/api/tags", timeout=3)
    modellen = [m.get("name") for m in (r.json().get("models") or [])][:4]
    if not modellen:
        return False, "bereikbaar maar géén model geladen (ollama pull ...)"
    return True, "modellen: " + ", ".join(modellen)


def _overpass():
    r = requests.get("https://overpass-api.de/api/status", timeout=4)
    return r.status_code == 200, f"hoofdserver antwoordt {r.status_code}"


def _open_meteo():
    r = requests.get("https://api.open-meteo.com/v1/forecast"
                     "?latitude=50.9&longitude=3.1&daily=weather_code"
                     "&forecast_days=1", timeout=4)
    return r.status_code == 200, f"antwoordt {r.status_code}"


def _uit():
    key = current_app.config.get("UIT_API_KEY") or ""
    if not key:
        return None, "geen UIT_API_KEY (bron kan uit staan tot go-live)"
    return True, "key aanwezig — laatste run: zie bronnen hieronder"


def _overture():
    from ..models import HorecaKandidaat
    n = HorecaKandidaat.query.count()
    if not n:
        return False, "voorraad leeg — draai `flask laad-overture`"
    laatst = db.session.query(db.func.max(HorecaKandidaat.created_at)).scalar()
    wanneer = laatst.strftime("%d/%m/%Y") if laatst else "?"
    return True, f"{n} kandidaten in voorraad (geladen {wanneer})"


def alle_checks():
    """Live checks + de laatste runs van alle databronnen."""
    checks = []
    for naam, fn in (
        ("Database (PostgreSQL)", _db),
        ("Redis (rate-limiter)", _redis),
        ("Mail (SMTP)", _smtp),
        ("Mollie (betalingen)", _mollie),
        ("Odoo (facturatie)", _odoo),
        ("Ollama (AI-verrijking)", _ollama),
        ("Overpass (OSM live)", _overpass),
        ("Open-Meteo (weer)", _open_meteo),
        ("UiTdatabank", _uit),
        ("Overture-voorraad", _overture),
    ):
        ok, detail, ms = _ping(fn)
        checks.append({"naam": naam, "ok": ok, "detail": detail, "ms": ms})
    from ..models import SyncStatus
    bronnen = SyncStatus.query.order_by(SyncStatus.source).all()
    return checks, bronnen
