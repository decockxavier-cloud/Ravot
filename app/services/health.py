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
    from .. import mollie
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
    from .. import odoo
    if not odoo.actief():
        return False, "niet geconfigureerd (ODOO_URL/DB/USER/API_KEY in .env)"
    uid = odoo._login()
    return (True, f"login ok (uid {uid})") if uid else (False, "login geweigerd")


def _ai():
    """Status van de gekozen AI-backend: cloud (Anthropic) of lokaal (Ollama).
    Toont dus wat er ECHT gebruikt wordt bij verrijking en horeca-triage."""
    from ..models import get_setting
    backend = (get_setting("verrijk_backend") or "ollama").lower()
    if backend == "cloud":
        key = current_app.config.get("ENRICH_CLOUD_KEY")
        if not key:
            return False, ("backend 'cloud' gekozen maar geen key in .env — zet "
                           "ENRICH_CLOUD_KEY of ANTHROPIC_API_KEY, en herlaad de "
                           "container met: docker compose up -d --force-recreate web")
        r = requests.get("https://api.anthropic.com/v1/models", timeout=6,
                         headers={"x-api-key": key,
                                  "anthropic-version": "2023-06-01"})
        model = get_setting("cloud_model") or "claude-haiku-4-5-20251001"
        if r.status_code == 200:
            return True, f"cloud actief ({model}) — API-key geldig"
        if r.status_code in (401, 403):
            return False, "cloud: API-key ongeldig of verlopen"
        return False, f"cloud: Anthropic antwoordt {r.status_code}"
    ok, detail = _ollama()
    hint = ""
    if current_app.config.get("ENRICH_CLOUD_KEY"):
        hint = " · tip: er staat een cloud-key klaar — wissel bij Instellingen"
    return ok, f"lokaal (ollama) — {detail}{hint}"


def _ollama():
    url = (current_app.config.get("OLLAMA_URL") or "http://ollama:11434").rstrip("/")
    r = requests.get(f"{url}/api/tags", timeout=3)
    modellen = [m.get("name") for m in (r.json().get("models") or [])][:4]
    if not modellen:
        return False, "bereikbaar maar géén model geladen (ollama pull ...)"
    return True, "modellen: " + ", ".join(modellen)


def _overpass():
    servers = ("https://overpass-api.de/api/status",
               "https://overpass.kumi.systems/api/status",
               "https://overpass.private.coffee/api/status")
    laatste = None
    for url in servers:
        try:
            r = requests.get(url, timeout=4, headers={
                "User-Agent": "Ravot/1.0 (+https://ravot.be)",
                "Accept": "*/*"})
            if r.status_code == 200:
                return True, f"{url.split('/')[2]} antwoordt 200"
            laatste = f"{url.split('/')[2]} antwoordt {r.status_code}"
        except Exception as exc:
            laatste = f"{url.split('/')[2]}: {type(exc).__name__}"
    return False, laatste or "geen server bereikbaar"


def _open_meteo():
    r = requests.get("https://api.open-meteo.com/v1/forecast"
                     "?latitude=50.9&longitude=3.1&daily=weather_code"
                     "&forecast_days=1", timeout=4)
    return r.status_code == 200, f"antwoordt {r.status_code}"


def _uit():
    key = current_app.config.get("UIT_API_KEY") or ""
    url = current_app.config.get("UIT_SEARCH_URL") or ""
    is_test = "test" in url
    if not key:
        return None, "geen UIT_API_KEY (bron kan uit staan tot go-live)"
    if is_test:
        return None, ("TEST-omgeving (demodata!) — vraag een productiekey aan "
                      "bij publiq en zet UIT_SEARCH_URL op "
                      "https://search.uitdatabank.be")
    return True, "PRODUCTIE — echte events, key aanwezig"


def _overture():
    from ..models import HorecaKandidaat
    n = HorecaKandidaat.query.count()
    if not n:
        return False, "voorraad leeg — draai `flask laad-overture`"
    laatst = db.session.query(db.func.max(HorecaKandidaat.created_at)).scalar()
    wanneer = laatst.strftime("%d/%m/%Y") if laatst else "?"
    return True, f"{n} kandidaten in voorraad (geladen {wanneer})"


def _backup():
    """Backup-versheid: er moet een recente (< 26u) en niet-lege backup zijn.
    Een stil gefaalde backup is een van de gevaarlijkste dingen die er zijn."""
    import os
    import glob
    from datetime import datetime
    mappen = (current_app.config.get("BACKUP_DIR")
              or "/backups", "/srv/ravot-backups", "/var/backups/ravot")
    bestanden = []
    for m in mappen:
        bestanden += glob.glob(os.path.join(m, "*"))
    bestanden = [f for f in bestanden if os.path.isfile(f)]
    if not bestanden:
        return False, "geen backupbestanden gevonden — controleer scripts/backup-db.sh + crontab"
    nieuwste = max(bestanden, key=os.path.getmtime)
    leeftijd_u = (datetime.now().timestamp() - os.path.getmtime(nieuwste)) / 3600
    grootte_mb = os.path.getsize(nieuwste) / (1024 * 1024)
    if grootte_mb < 0.5:
        return False, f"nieuwste backup is te klein ({grootte_mb:.1f} MB) — mogelijk mislukt"
    if leeftijd_u > 26:
        return False, f"nieuwste backup is {leeftijd_u:.0f}u oud — draaide de nachtelijke backup?"
    return True, f"vers ({leeftijd_u:.0f}u oud, {grootte_mb:.1f} MB)"


def _werkvoorraad():
    """Opstopping in de menselijke wachtrijen: als er te veel blijft liggen,
    is dat een signaal (niet kritiek, wel aandacht)."""
    from ..models import Event, OperatorClaim, Photo, EditProposal
    wacht = Event.query.filter_by(pending=True).count()
    claims = OperatorClaim.query.filter_by(status="pending").count()
    fotos = Photo.query.filter_by(status="pending").count()
    edits = EditProposal.query.filter_by(status="pending").count()
    totaal = wacht + claims + fotos + edits
    detail = f"{totaal} open ({wacht} wachtrij, {claims} claims, {fotos} foto's, {edits} edits)"
    if totaal > 75:
        return False, detail + " — loopt op, werk de wachtrij bij"
    return True, detail


def _schijf():
    """Schijfruimte: een vollopende schijf legt stilletjes alles plat
    (uploads, backups, logs stapelen op)."""
    import shutil
    total, used, free = shutil.disk_usage("/")
    pct = used / total * 100
    vrij_gb = free / (1024 ** 3)
    detail = f"{pct:.0f}% gebruikt, {vrij_gb:.1f} GB vrij"
    if pct > 90:
        return False, detail + " — kritiek, ruim op"
    if pct > 85:
        return None, detail + " — hou in het oog"
    return True, detail


def alle_checks():
    """Live checks + de laatste runs van alle databronnen."""
    checks = []
    for naam, fn in (
        ("Database (PostgreSQL)", _db),
        ("Redis (rate-limiter)", _redis),
        ("Mail (SMTP)", _smtp),
        ("Mollie (betalingen)", _mollie),
        ("Odoo (facturatie)", _odoo),
        ("AI-verrijking (cloud/Ollama)", _ai),
        ("Overpass (OSM live)", _overpass),
        ("Open-Meteo (weer)", _open_meteo),
        ("UiTdatabank", _uit),
        ("Overture-voorraad", _overture),
        ("Database-backup", _backup),
        ("Werkvoorraad", _werkvoorraad),
        ("Schijfruimte", _schijf),
    ):
        ok, detail, ms = _ping(fn)
        checks.append({"naam": naam, "ok": ok, "detail": detail, "ms": ms})
    from ..models import SyncStatus
    from .sources import REGISTRY
    from datetime import datetime, timedelta
    # Verwachte maximale ouderdom per bron (uren). Wordt de laatste run ouder
    # dan dit, dan is de sync "te laat" volgens schema.
    SCHEMA_UREN = {"uit": 26, "osm": 24 * 8, "tm": 26, "tv": 26}
    ruw = [b for b in SyncStatus.query.order_by(SyncStatus.source).all()
           if b.source in REGISTRY or b.source == "overture"]
    grens = datetime.utcnow() - timedelta(hours=2)
    bronnen = []
    for b in ruw:
        vastgelopen = (b.state == "running"
                       and (b.updated_at or b.last_run or grens) < grens)
        # Te laat volgens schema?
        te_laat = False
        max_u = SCHEMA_UREN.get(b.source)
        if max_u and b.last_run:
            leeftijd_u = (datetime.utcnow() - b.last_run).total_seconds() / 3600
            te_laat = leeftijd_u > max_u
        bronnen.append({"source": b.source, "state": b.state,
                        "last_run": b.last_run, "last_result": b.last_result,
                        "last_error": b.last_error,
                        "vastgelopen": vastgelopen, "te_laat": te_laat,
                        "schema_uren": max_u})
    return checks, bronnen


def dashboard_samenvatting():
    """Compacte samenvatting voor het dashboard-statusblok: telt problemen en
    waarschuwingen over de kritieke checks + sync-versheid, zonder de trage
    externe pings (die blijven op de volledige /status-pagina)."""
    from ..models import SyncStatus
    from datetime import datetime
    problemen, waarschuwingen, items = 0, 0, []
    # Snelle, lokale checks (geen trage externe API-pings op het dashboard).
    for naam, fn in (("Database-backup", _backup), ("Werkvoorraad", _werkvoorraad),
                     ("Schijfruimte", _schijf), ("Mail (SMTP)", _smtp)):
        ok, detail, _ = _ping(fn)
        if ok is False:
            problemen += 1
            items.append({"naam": naam, "niveau": "fout", "detail": detail})
        elif ok is None:
            waarschuwingen += 1
            items.append({"naam": naam, "niveau": "let op", "detail": detail})
    # Sync-versheid volgens schema (dagelijkse/wekelijkse bronnen).
    SCHEMA_UREN = {"uit": 26, "osm": 24 * 8}
    for b in SyncStatus.query.all():
        max_u = SCHEMA_UREN.get(b.source)
        if not max_u:
            continue
        if b.last_error and b.state == "error":
            problemen += 1
            items.append({"naam": f"Sync {b.source}", "niveau": "fout",
                          "detail": f"laatste run faalde: {b.last_error[:80]}"})
        elif b.last_run:
            leeftijd_u = (datetime.utcnow() - b.last_run).total_seconds() / 3600
            if leeftijd_u > max_u:
                waarschuwingen += 1
                items.append({"naam": f"Sync {b.source}", "niveau": "let op",
                              "detail": f"{leeftijd_u:.0f}u geleden (verwacht < {max_u}u)"})
    return {"problemen": problemen, "waarschuwingen": waarschuwingen,
            "meldingen": items, "gezond": problemen == 0 and waarschuwingen == 0}
