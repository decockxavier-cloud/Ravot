"""Herkomst-tracking: waar komen bezoekers vandaan?

Volledig server-side en privacyvriendelijk — géén Google, géén extra cookies,
géén IP-opslag, dus ook geen consentbanner nodig (de merkbelofte "geen
pop-ups" blijft overeind). Per bezoeker wordt hoogstens één regel per dag
gelogd in de bestaande Interaction-tabel (type "herkomst") met:

    ref        : de verwijzende pagina (afgekapt)
    ref_domein : enkel het domein daarvan (voor groepering)
    utm_*      : campagneparameters (bron/medium/campagne/term/content)
    pad        : de landingspagina binnen Ravot

Het dashboard in /beheer/herkomst groepeert dit tot kanalen en bronnen.
"""
from datetime import date
from urllib.parse import urlparse

from flask import request, session

from .extensions import db

# User-agents die we niet als bezoek tellen (crawlers, previews, scripts).
_BOT_TEKENS = ("bot", "crawler", "spider", "slurp", "preview", "curl", "wget",
               "python-requests", "headless", "facebookexternalhit",
               "whatsapp", "telegram", "lighthouse", "pingdom", "uptime")

# Paden die nooit een 'bezoek' zijn.
_SKIP_PREFIX = ("/static", "/beheer", "/uitbater", "/health", "/sw.js",
                "/manifest", "/robots", "/sitemap", "/llms", "/favicon",
                "/apple-touch")

_UTM_VELDEN = ("utm_source", "utm_medium", "utm_campaign", "utm_term",
               "utm_content")

_ZOEKMACHINES = ("google.", "bing.", "duckduckgo.", "ecosia.", "qwant.",
                 "startpage.", "yahoo.", "brave.")
_SOCIAAL = ("facebook.", "instagram.", "fb.me", "m.facebook", "l.facebook",
            "t.co", "twitter.", "x.com", "linkedin.", "pinterest.",
            "tiktok.", "youtube.", "reddit.", "wa.me", "web.whatsapp")


def _is_bot(ua):
    ua = (ua or "").lower()
    return any(t in ua for t in _BOT_TEKENS)


def registreer_bezoek():
    """Log hoogstens één herkomst-regel per sessie per dag. Mag NOOIT een
    paginaweergave breken: alles zit in een try/except."""
    try:
        if request.method != "GET":
            return
        pad = request.path or "/"
        if pad.startswith(_SKIP_PREFIX):
            return
        if _is_bot(request.headers.get("User-Agent")):
            return
        vandaag = date.today().isoformat()
        if session.get("hk_dag") == vandaag:
            return
        session["hk_dag"] = vandaag

        ref = (request.referrer or "")[:300]
        ref_domein = ""
        if ref:
            ref_domein = (urlparse(ref).netloc or "").lower().removeprefix("www.")
            if ref_domein and ref_domein in (request.host or "").lower():
                ref, ref_domein = "", ""   # interne navigatie is geen herkomst
        meta = {"ref": ref, "ref_domein": ref_domein, "pad": pad[:200]}
        for veld in _UTM_VELDEN:
            waarde = (request.args.get(veld) or "").strip()[:120]
            if waarde:
                meta[veld] = waarde

        from .models import Interaction
        db.session.add(Interaction(
            family_id=session.get("family_id"), type="herkomst", meta=meta))
        db.session.commit()
    except Exception:
        db.session.rollback()


def kanaal(meta):
    """Classificeer één herkomst-regel tot een leesbaar kanaal."""
    meta = meta or {}
    medium = (meta.get("utm_medium") or "").lower()
    if medium == "email" or (meta.get("utm_source") or "").lower() in ("ravot-mail", "ravot_mail"):
        return "E-mail"
    if any(meta.get(v) for v in _UTM_VELDEN):
        return "Campagne"
    dom = meta.get("ref_domein") or ""
    if not dom:
        return "Direct"
    if any(z in dom for z in _ZOEKMACHINES):
        return "Zoekmachine"
    if any(s in dom for s in _SOCIAAL):
        return "Social"
    return "Verwijzing"


def rapport(dagen=30):
    """Aggregatie voor het beheer-dashboard: totalen, kanalen, bronnen,
    campagnes, zoektermen (enkel uit utm_term), landingspagina's en een
    weektrend. Eén query; groepering in Python (meta is JSON)."""
    from datetime import datetime, timedelta
    from collections import Counter
    from .models import Interaction

    grens = datetime.utcnow() - timedelta(days=dagen)
    rijen = (Interaction.query
             .filter(Interaction.type == "herkomst",
                     Interaction.created_at >= grens)
             .order_by(Interaction.created_at.asc()).all())

    kanalen, bronnen, campagnes, termen, paden, weken = (
        Counter(), Counter(), Counter(), Counter(), Counter(), Counter())
    ingelogd = 0
    for r in rijen:
        m = r.meta or {}
        kanalen[kanaal(m)] += 1
        bron = m.get("ref_domein") or ("(direct)" if not any(
            m.get(v) for v in _UTM_VELDEN) else f"campagne: {m.get('utm_source', '?')}")
        bronnen[bron] += 1
        if m.get("utm_campaign"):
            campagnes[f"{m.get('utm_source', '?')} · {m['utm_campaign']}"] += 1
        if m.get("utm_term"):
            termen[m["utm_term"]] += 1
        paden[m.get("pad") or "/"] += 1
        if r.created_at:
            iso = r.created_at.isocalendar()
            weken[f"{iso[0]}-W{iso[1]:02d}"] += 1
        if r.family_id:
            ingelogd += 1

    return {
        "totaal": len(rijen), "ingelogd": ingelogd, "dagen": dagen,
        "kanalen": kanalen.most_common(),
        "bronnen": bronnen.most_common(15),
        "campagnes": campagnes.most_common(10),
        "termen": termen.most_common(10),
        "paden": paden.most_common(10),
        "weken": sorted(weken.items()),
    }
