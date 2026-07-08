"""AI-verrijking: stelt op basis van FEITEN een originele beschrijving,
categorie, leeftijd en binnen/buiten voor. Nooit rechtstreeks opgeslagen —
de output gaat naar een goedkeuringswachtrij (menselijke validatie).

Twee omwisselbare backends via de setting 'verrijk_backend':
  - 'ollama'  : lokaal open-source model in de ollama-container (standaard, gratis)
  - 'cloud'   : externe API (enkel als er een key is ingesteld)

Belangrijk: AI mag hier enkel FEITEN herformuleren en een EIGEN originele tekst
schrijven. Het mag geen beschermde foto's of teksten witwassen — dat verandert
niet of het model lokaal of in de cloud draait.
"""
import json
import re

import requests
from flask import current_app

from .models import CATEGORIES

SYSTEM = (
    "Je bent een behulpzame assistent voor Ravot, een Vlaamse website met "
    "gezinsuitstappen voor kinderen tot 12 jaar. Je schrijft korte, warme, "
    "originele beschrijvingen in het Nederlands. Je verzint NOOIT feiten die "
    "niet gegeven zijn (geen openingsuren, prijzen of details bijverzinnen). "
    "Je antwoordt uitsluitend met geldige JSON, zonder extra tekst."
)


def _feiten(event):
    return {
        "naam": event.title,
        "categorieen": event.categories or [],
        "gemeente": event.gemeente,
        "adres": event.adres,
        "website": event.source_url,
        "bestaande_beschrijving": (event.description or "")[:500] or None,
        "binnen_bekend": event.indoor,
    }


def _prompt(event):
    return (
        "Feiten over een plek (gebruik ENKEL deze, verzin niets bij):\n"
        f"{json.dumps(_feiten(event), ensure_ascii=False, indent=2)}\n\n"
        "Geef een JSON-object met exact deze sleutels:\n"
        '- "beschrijving": 2 à 3 zinnen, warm en uitnodigend, gericht op gezinnen '
        "met jonge kinderen, in het Nederlands. Baseer je enkel op de feiten.\n"
        f'- "categorie": één waarde uit deze lijst: {list(CATEGORIES)}.\n'
        '- "leeftijd_min": geschatte minimumleeftijd (geheel getal 0-18).\n'
        '- "leeftijd_max": geschatte maximumleeftijd (geheel getal 0-18).\n'
        '- "binnen": true als het een binnenlocatie is, anders false.\n'
        "Antwoord met ENKEL het JSON-object."
    )


# ------------------------------------------------------------ backends --

def _ollama_generate(prompt, system, timeout=180):
    from .models import get_setting
    url = (current_app.config.get("OLLAMA_URL")
           or "http://ollama:11434").rstrip("/")
    model = get_setting("ollama_model") or "qwen2.5:7b"
    r = requests.post(f"{url}/api/chat", timeout=timeout, json={
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.3},
    })
    r.raise_for_status()
    return (r.json().get("message") or {}).get("content", "")


def _cloud_generate(prompt, system, timeout=60):
    """Optionele externe backend. Alleen actief als er een key is ingesteld."""
    key = current_app.config.get("ENRICH_CLOUD_KEY")
    if not key:
        raise RuntimeError("Cloud-backend niet geconfigureerd (geen ENRICH_CLOUD_KEY).")
    from .models import get_setting
    url = current_app.config.get("ENRICH_CLOUD_URL", "https://api.anthropic.com/v1/messages")
    model = get_setting("cloud_model") or "claude-haiku-4-5-20251001"
    r = requests.post(url, timeout=timeout,
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                               "content-type": "application/json"},
                      json={"model": model, "max_tokens": 500, "system": system,
                            "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    blokken = r.json().get("content") or []
    return "".join(b.get("text", "") for b in blokken if b.get("type") == "text")


def _generate(prompt, system):
    from .models import get_setting
    backend = (get_setting("verrijk_backend") or "ollama").lower()
    return (_cloud_generate if backend == "cloud" else _ollama_generate)(prompt, system)


# ------------------------------------------------------------- parsing --

def _parse_json(ruw):
    if not ruw:
        return {}
    ruw = re.sub(r"^```(?:json)?|```$", "", ruw.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(ruw)
    except Exception:
        m = re.search(r"\{.*\}", ruw, re.DOTALL)   # eerste {...} eruit vissen
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return {}
    return {}


def _clamp_leeftijd(v, standaard):
    try:
        return max(0, min(18, int(v)))
    except (TypeError, ValueError):
        return standaard


def verrijk_plek(event, generate=None):
    """Vraag een AI-voorstel voor een plek. Geeft een dict met voorstel-velden.
    'generate' is injecteerbaar voor tests (zodat we geen live model nodig hebben)."""
    gen = generate or _generate
    ruw = gen(_prompt(event), SYSTEM)
    data = _parse_json(ruw)
    cat = data.get("categorie")
    lo = _clamp_leeftijd(data.get("leeftijd_min"), event.age_min or 0)
    hi = _clamp_leeftijd(data.get("leeftijd_max"), event.age_max or 12)
    if hi < lo:
        lo, hi = hi, lo
    return {
        "beschrijving": (data.get("beschrijving") or "").strip()[:2000],
        "categorie": cat if cat in CATEGORIES else None,
        "leeftijd_min": lo,
        "leeftijd_max": hi,
        "binnen": bool(data.get("binnen")),
        "ruw": ruw,   # voor debugging in de testknop
    }


# ------------------------------------------------ wachtrij (batch) --

def te_verrijken(limit=20):
    """Permanente, zichtbare plekken die nog geen voorstel hebben."""
    from .models import Event, EnrichProposal
    from .extensions import db
    heeft_voorstel = db.session.query(EnrichProposal.event_id)
    return (Event.query
            .filter(Event.is_permanent.is_(True),
                    Event.pending.is_(False),
                    Event.hidden.is_(False),
                    ~Event.id.in_(heeft_voorstel))
            .order_by(Event.id.desc())
            .limit(limit).all())


def verrijk_batch(limit=20, generate=None):
    """Genereer AI-voorstellen voor tot 'limit' plekken en zet ze in de wachtrij.
    Geeft (aantal_gelukt, aantal_mislukt) terug. Traag op CPU: bedoeld als
    achtergrond- of nachtelijke batch."""
    from .models import EnrichProposal
    from .extensions import db
    gelukt = mislukt = 0
    for ev in te_verrijken(limit):
        try:
            v = verrijk_plek(ev, generate=generate)
        except Exception:
            mislukt += 1
            continue
        db.session.add(EnrichProposal(
            event_id=ev.id, beschrijving=v["beschrijving"], categorie=v["categorie"],
            leeftijd_min=v["leeftijd_min"], leeftijd_max=v["leeftijd_max"],
            binnen=v["binnen"], status="pending"))
        db.session.commit()
        gelukt += 1
    return gelukt, mislukt


def pas_voorstel_toe(proposal, beschrijving=None):
    """Keur een voorstel goed: pas de velden toe op het event.
    'beschrijving' laat de admin de tekst nog aanpassen vóór opslaan."""
    from .extensions import db
    ev = proposal.event
    if ev:
        tekst = (beschrijving if beschrijving is not None else proposal.beschrijving) or ""
        if tekst.strip():
            ev.description = tekst.strip()[:2000]
        if proposal.categorie in CATEGORIES:
            ev.categories = [proposal.categorie]
        if proposal.leeftijd_min is not None:
            ev.age_min = proposal.leeftijd_min
        if proposal.leeftijd_max is not None:
            ev.age_max = proposal.leeftijd_max
        if proposal.binnen is not None:
            ev.indoor = bool(proposal.binnen)
    proposal.status = "approved"
    if ev:
        from .kwaliteit import bereken_kwaliteit
        ev.quality = bereken_kwaliteit(ev)
    db.session.commit()
