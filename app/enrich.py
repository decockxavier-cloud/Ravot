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


def _prompt(event, webtekst=None):
    blok = (
        "Feiten over een plek (gebruik ENKEL deze, verzin niets bij):\n"
        f"{json.dumps(_feiten(event), ensure_ascii=False, indent=2)}\n\n"
    )
    if webtekst:
        blok += ("Tekst van de website van deze plek (gebruik dit als bron, maar "
                 "neem enkel over wat duidelijk over deze plek gaat):\n"
                 f"\"\"\"\n{webtekst}\n\"\"\"\n\n")
    return blok + (
        "Geef een JSON-object met exact deze sleutels:\n"
        '- "beschrijving": 2 à 3 zinnen, warm en uitnodigend, gericht op gezinnen '
        "met jonge kinderen, in het Nederlands. Baseer je enkel op de feiten"
        f"{' en de websitetekst' if webtekst else ''}.\n"
        f'- "categorie": één waarde uit deze lijst: {list(CATEGORIES)}.\n'
        '- "leeftijd_min": geschatte minimumleeftijd (geheel getal 0-18).\n'
        '- "leeftijd_max": geschatte maximumleeftijd (geheel getal 0-18).\n'
        '- "binnen": true als het een binnenlocatie is, anders false.\n'
        '- "gratis": true als de plek duidelijk gratis toegankelijk is, false als '
        "er duidelijk betaald moet worden, of null als je het niet zeker weet.\n"
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


def _cloud_generate(prompt, system, timeout=60, max_tokens=500):
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
                      json={"model": model, "max_tokens": max_tokens, "system": system,
                            "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    blokken = r.json().get("content") or []
    return "".join(b.get("text", "") for b in blokken if b.get("type") == "text")


def _generate(prompt, system, max_tokens=500):
    from .models import get_setting
    backend = (get_setting("verrijk_backend") or "ollama").lower()
    if backend == "cloud":
        return _cloud_generate(prompt, system, max_tokens=max_tokens)
    return _ollama_generate(prompt, system)


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


def _haal_webtekst(url, max_chars=3000):
    """Haal de zichtbare tekst van een webpagina op (voor AI-context).
    Veilig: enkel http/https naar publieke hosts (SSRF-bescherming: geen
    loopback/privé/link-local adressen, dus geen interne diensten zoals
    Ollama, de databank of cloud-metadata), korte timeout, HTML gestript."""
    if not url or not str(url).startswith(("http://", "https://")):
        return None
    try:
        import socket, ipaddress
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast):
                return None
    except Exception:
        return None
    try:
        import requests
        r = requests.get(url, timeout=12, allow_redirects=False,
                         headers={"User-Agent": "Ravot/1.0 (+https://ravot.be)"})
        r.raise_for_status()
        html = r.text
    except Exception:
        return None
    from html.parser import HTMLParser

    class _Tekst(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stukken = []
            self._skip = False
        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "nav", "footer", "header"):
                self._skip = True
        def handle_endtag(self, tag):
            if tag in ("script", "style", "nav", "footer", "header"):
                self._skip = False
        def handle_data(self, data):
            if not self._skip:
                t = data.strip()
                if t:
                    self.stukken.append(t)

    p = _Tekst()
    try:
        p.feed(html)
    except Exception:
        return None
    tekst = " ".join(p.stukken)
    return tekst[:max_chars] if tekst else None


def verrijk_plek(event, generate=None, extra_url=None):
    """Vraag een AI-voorstel voor een plek. Geeft een dict met voorstel-velden.
    Als er een website (event.source_url of extra_url) is, halen we die tekst op
    en geven we ze als extra context mee — zo baseert de AI zich op wat online
    staat i.p.v. enkel op de kale feiten.
    'generate' is injecteerbaar voor tests (zodat we geen live model nodig hebben)."""
    gen = generate or _generate
    webtekst = _haal_webtekst(extra_url or event.source_url)
    ruw = gen(_prompt(event, webtekst=webtekst), SYSTEM)
    data = _parse_json(ruw)
    cat = data.get("categorie")
    lo = _clamp_leeftijd(data.get("leeftijd_min"), event.age_min or 0)
    hi = _clamp_leeftijd(data.get("leeftijd_max"), event.age_max or 12)
    if hi < lo:
        lo, hi = hi, lo
    # is_free (gratis): AI mag None teruggeven -> dan behouden we de bestaande waarde
    gratis = data.get("gratis")
    return {
        "beschrijving": (data.get("beschrijving") or "").strip()[:2000],
        "categorie": cat if cat in CATEGORIES else None,
        "leeftijd_min": lo,
        "leeftijd_max": hi,
        "binnen": bool(data.get("binnen")),
        "gratis": bool(gratis) if gratis is not None else event.is_free,
        "webtekst_gebruikt": bool(webtekst),
        "ruw": ruw,   # voor debugging in de testknop
    }


# ------------------------------------------------ wachtrij (batch) --

def te_verrijken(limit=20, zone="midden"):
    """Kandidaten voor AI-verrijking: permanente, zichtbare plekken zonder
    voorstel.

    zone="midden" (standaard): enkel de middenzone (kwaliteit_min ≤ score <
    kwaliteit_hoog), met de fiches die het díchtst bij de groene drempel zitten
    eerst — die hebben aan één beschrijving genoeg om voorrang te halen, dus het
    hoogste rendement per batch. Binnen gelijke score krijgen fiches zonder
    beschrijving voorrang (daar voegt de AI het meeste toe).
    zone="alles": elke kandidaat (oud gedrag), nieuwste eerst."""
    from .models import Event, EnrichProposal, get_int
    from .extensions import db
    heeft_voorstel = db.session.query(EnrichProposal.event_id)
    q = Event.query.filter(
        Event.is_permanent.is_(True),
        Event.pending.is_(False),
        Event.hidden.is_(False),
        ~Event.id.in_(heeft_voorstel))
    if zone == "midden":
        k_min = get_int("kwaliteit_min_lijst", 30)
        k_hoog = get_int("kwaliteit_hoog", 60)
        q = q.filter(Event.quality.isnot(None),
                     Event.quality >= k_min,
                     Event.quality < k_hoog)
        # dichtst bij groen eerst; daarna: lege beschrijving eerst
        geen_tekst = db.case((db.or_(Event.description.is_(None),
                                     Event.description == ""), 0), else_=1)
        q = q.order_by(Event.quality.desc(), geen_tekst.asc())
    else:
        q = q.order_by(Event.id.desc())
    return q.limit(limit).all()


def verrijk_batch(limit=20, generate=None, zone="midden"):
    """Genereer AI-voorstellen voor tot 'limit' plekken en zet ze in de wachtrij.
    Geeft (aantal_gelukt, aantal_mislukt) terug. Traag op CPU: bedoeld als
    achtergrond- of nachtelijke batch. zone stuurt de selectie (zie te_verrijken)."""
    from .models import EnrichProposal
    from .extensions import db
    gelukt = mislukt = 0
    for ev in te_verrijken(limit, zone=zone):
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
