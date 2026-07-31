"""Redactie: pagina's, blog, nieuwsbrieven en zoekdata als één geheel.

- preview van de weekendmail (exact dezelfde bouwfunctie als de echte
  verzending, dus wat je ziet is wat er vertrekt)
- datagedreven artikelsuggesties uit de eigen instrumentatie:
  nul-resultaat-zoekopdrachten, meest bekeken fiches, komende vakanties,
  gemeenten met het rijkste aanbod
- AI-concept: van een suggestie naar een klaarstaand conceptartikel via de
  bestaande verrijk-backend (Ollama of cloud, instelling 'verrijk_backend')
"""
from collections import Counter
from datetime import datetime, timedelta

from ..extensions import db


def voorbeeldgezin():
    """Een representatief gezin voor de mailpreview: het eerste met de
    nieuwsbrief aan. Geen gezinnen? Dan None (de pagina legt het uit)."""
    from ..models import Family
    return Family.query.filter_by(newsletter_opt_in=True).first()


def artikel_suggesties():
    """Concrete schrijfonderwerpen, gerangschikt op datasignaal."""
    from ..models import Interaction, Event
    from ..vakantie import komende_vakantie
    grens = datetime.utcnow() - timedelta(days=30)
    sug = []

    # 1. Waar zochten mensen naar zonder resultaat? (directe contentgaten)
    nul = (Interaction.query
           .filter(Interaction.type == "zero_result",
                   Interaction.created_at >= grens).all())
    teller = Counter()
    for r in nul:
        m = r.meta or {}
        sleutel = " · ".join(str(m[k]) for k in ("scope", "soort", "postcode")
                             if m.get(k)) or "algemeen"
        teller[sleutel] += 1
    for wat, n in teller.most_common(3):
        sug.append({"bron": "Nul resultaten", "n": n,
                    "onderwerp": f"Zoekopdracht zonder resultaat: {wat}",
                    "hoek": "Schrijf een gids die dit gat vult en link naar de "
                            "dichtstbijzijnde alternatieven."})

    # 2. Meest bekeken fiches: daar leeft interesse — verdiep ze.
    views = (db.session.query(Interaction.event_id,
                              db.func.count(Interaction.id).label("n"))
             .filter(Interaction.type == "view",
                     Interaction.created_at >= grens,
                     Interaction.event_id.isnot(None))
             .group_by(Interaction.event_id)
             .order_by(db.text("n DESC")).limit(3).all())
    for eid, n in views:
        e = db.session.get(Event, eid)
        if e:
            sug.append({"bron": "Populaire fiche", "n": n,
                        "onderwerp": f"Uitgelicht: {e.title} ({e.gemeente})",
                        "hoek": "Insider-gids: beste moment, leeftijden, "
                                "eten in de buurt — met interne links."})

    # 3. Komende schoolvakantie: seizoenscontent op tijd klaar.
    vak = komende_vakantie(binnen_dagen=45)
    if vak:
        naam, dagen = vak
        sug.append({"bron": "Kalender", "n": dagen,
                    "onderwerp": f"{naam}: de leukste gezinsuitstappen",
                    "hoek": "Publiceer 2-3 weken vooraf; Google indexeert "
                            "seizoensartikels traag."})

    # 4. Gemeenten met het rijkste aanbod: gidsen die zichzelf schrijven.
    top_gem = (db.session.query(Event.gemeente, db.func.count(Event.id).label("n"))
               .filter(Event.hidden.is_(False), Event.pending.is_(False),
                       Event.is_permanent.is_(True), Event.gemeente.isnot(None))
               .group_by(Event.gemeente).order_by(db.text("n DESC")).limit(3).all())
    for gem, n in top_gem:
        sug.append({"bron": "Rijk aanbod", "n": n,
                    "onderwerp": f"Gratis en goedkoop ravotten in {gem}",
                    "hoek": f"{n} plekken in de databank — kies de 7 beste, "
                            f"link naar /{gem.lower()} en de fiches."})
    return sug


_AI_SYSTEM = (
    "Je bent de redacteur van Ravot.be, een Vlaams platform voor gezinsuitstappen "
    "met kinderen tot 12 jaar. Schrijf in warm, helder Nederlands (Vlaanderen), "
    "praktisch en zonder overdrijving. Structuur: korte inleiding, 3-7 "
    "tussenkoppen (##), concrete tips, afsluiter met uitnodiging om Ravot te "
    "gebruiken. Gebruik Markdown. Verzin GEEN feiten, adressen of prijzen; "
    "gebruik enkel de aangeleverde gegevens en blijf algemeen waar je iets "
    "niet zeker weet.")


def ai_concept(onderwerp, hoek=""):
    """Genereer een conceptartikel en bewaar het als concept (niet publiek).
    Retourneert het Artikel of None bij een AI-fout."""
    from ..enrich import _generate
    from ..models import Artikel
    from ..routes.admin import _artikel_slug
    prompt = (f"Schrijf een blogartikel voor Ravot.\n\nOnderwerp: {onderwerp}\n"
              f"Invalshoek: {hoek or 'praktische gids voor ouders'}\n\n"
              "Geef eerst op één regel 'TITEL: ...', dan op één regel "
              "'SAMENVATTING: ...' (max 180 tekens), daarna een lege regel "
              "en het artikel in Markdown (400-700 woorden).")
    try:
        ruw = _generate(prompt, _AI_SYSTEM, max_tokens=1400) or ""
    except Exception:
        return None
    if not ruw.strip():
        return None
    titel, samenvatting, body = onderwerp[:160], "", []
    for regel in ruw.splitlines():
        r = regel.strip()
        if r.upper().startswith("TITEL:"):
            titel = r[6:].strip()[:160] or titel
        elif r.upper().startswith("SAMENVATTING:"):
            samenvatting = r[13:].strip()[:200]
        else:
            body.append(regel)
    a = Artikel(titel=titel, samenvatting=samenvatting,
                inhoud_md="\n".join(body).strip(), gepubliceerd=False,
                # Slug vóór het opslaan bepalen: de kolom is verplicht, dus
                # een tussentijdse flush zonder slug crasht op Postgres.
                slug=_artikel_slug(titel))
    db.session.add(a)
    db.session.commit()
    return a


# ---------------------------------------------------------------------------
# Wekelijkse conceptgeneratie: blogconcept + socialposts (patch 140)
# ---------------------------------------------------------------------------

_SOCIAL_SYSTEM = (
    "Je schrijft socialmediaposts voor Ravot.be, een Vlaams platform voor "
    "gezinsuitstappen met kinderen tot 12 jaar. Toon: warm, speels, ouder-tot-"
    "ouder, Vlaams Nederlands, hooguit één emoji per zin. Verzin GEEN feiten; "
    "gebruik enkel de aangeleverde gegevens. Geef exact twee blokken terug: "
    "eerst een regel 'IG:' gevolgd door een korte Instagram-tekst (max 3 "
    "zinnen), dan een regel 'FB:' gevolgd door een iets langere Facebook-tekst "
    "(3-5 zinnen). Sluit af zonder hashtags.")


def _social_ai(opdracht, terugval_ig, terugval_fb):
    """AI-tekst met deterministische terugval: de cron levert áltijd iets op,
    ook als de AI-backend niet bereikbaar is."""
    from ..enrich import _generate
    try:
        ruw = _generate(opdracht, _SOCIAL_SYSTEM, max_tokens=500) or ""
    except Exception:
        ruw = ""
    ig, fb, doel = [], [], None
    for regel in ruw.splitlines():
        r = regel.strip()
        if r.upper().startswith("IG:"):
            doel, rest = ig, r[3:].strip()
            if rest:
                doel.append(rest)
        elif r.upper().startswith("FB:"):
            doel, rest = fb, r[3:].strip()
            if rest:
                doel.append(rest)
        elif doel is not None and r:
            doel.append(r)
    return ("\n".join(ig).strip() or terugval_ig,
            "\n".join(fb).strip() or terugval_fb)


def _utm(link, bron, campagne):
    sep = "&" if "?" in link else "?"
    return f"{link}{sep}utm_source={bron}&utm_medium=social&utm_campaign={campagne}"


def maak_socialconcepten():
    """Genereer de conceptposts voor de komende week volgens het ritme.
    Slaat over wat er deze week al staat (idempotent per week)."""
    from datetime import date, timedelta, datetime
    from flask import current_app
    from ..models import SocialPost, Event, Artikel
    site = current_app.config["SITE_URL"]
    vandaag = date.today()
    week = vandaag.isocalendar()[1]
    recent = datetime.utcnow() - timedelta(days=6)
    al = {p.soort for p in SocialPost.query.filter(
        SocialPost.created_at >= recent).all()}
    gemaakt = []

    # 1. Weekendtip (richtdag: komende vrijdag) — uit de echte weekendmail-picks.
    if "weekendtip" not in al:
        fam = voorbeeldgezin()
        picks = []
        if fam:
            from .weekendmail import bouw_weekendmail
            _, _, picks = bouw_weekendmail(fam)
        if picks:
            top = [p["event"] for p in picks[:3]]
            vrijdag = vandaag + timedelta(days=(4 - vandaag.weekday()) % 7)
            camp = f"post-weekend-w{week}"
            regels = "\n".join(f"- {e.title} ({e.gemeente})" for e in top)
            link_ig = _utm(site, "instagram", camp)
            link_fb = _utm(site, "facebook", camp)
            ig, fb = _social_ai(
                f"Weekendtip-post. Deze uitstappen springen er dit weekend uit:\n{regels}\n"
                f"Verwerk 1-2 ervan concreet en nodig uit om meer te ontdekken op Ravot.",
                terugval_ig=f"Weekend in zicht! 🦊 Onze tips: {', '.join(e.title for e in top[:2])}. "
                            f"Ontdek alles in jouw buurt via de link in bio.",
                terugval_fb=f"Waar gaan we dit weekend ravotten? Enkele parels:\n{regels}\n"
                            f"Alle uitstappen in jouw buurt, gratis en zonder account: {link_fb}")
            gemaakt.append(SocialPost(
                soort="weekendtip", onderwerp="Weekendtips " + "-".join(e.gemeente or "" for e in top[:2]),
                tekst_ig=f"{ig}\n\nLink in bio → {link_ig}", tekst_fb=fb,
                beeld_tip=f"Foto van {top[0].title} (of sfeerbeeld spelende kinderen buiten)",
                gepland_voor=vrijdag))

    # 2. Parel van de week (richtdag: maandag) — roterende gemeente, beste fiche.
    if "parel" not in al:
        top_gem = (db.session.query(Event.gemeente, db.func.count(Event.id).label("n"))
                   .filter(Event.is_permanent.is_(True), Event.hidden.is_(False),
                           Event.pending.is_(False), Event.gemeente.isnot(None))
                   .group_by(Event.gemeente).order_by(db.text("n DESC")).limit(10).all())
        if top_gem:
            gemeente = top_gem[week % len(top_gem)][0]
            parel = (Event.query.filter_by(gemeente=gemeente, is_permanent=True,
                                           hidden=False, pending=False)
                     .order_by(Event.quality.desc().nullslast()).first())
            if parel:
                maandag = vandaag + timedelta(days=(0 - vandaag.weekday()) % 7 or 7)
                camp = f"post-parel-w{week}"
                link = f"{site}/e/{parel.slug}"
                ig, fb = _social_ai(
                    f"Parel-van-de-week-post over: {parel.title} in {gemeente} "
                    f"(type: {parel.subtype or 'uitstap'}, "
                    f"{'gratis' if parel.is_free else 'betalend'}). "
                    f"Maak ouders nieuwsgierig om er met de kinderen heen te gaan.",
                    terugval_ig=f"Parel van de week 💎 {parel.title} in {gemeente} — "
                                f"een topplek om te ravotten. Link in bio!",
                    terugval_fb=f"Parel van de week: {parel.title} in {gemeente} 💎\n"
                                f"Alle info, openingsuren en de Ravotscore van echte "
                                f"gezinnen: {_utm(link, 'facebook', camp)}")
                gemaakt.append(SocialPost(
                    soort="parel", onderwerp=f"{parel.title} ({gemeente})",
                    tekst_ig=f"{ig}\n\nLink in bio → {_utm(link, 'instagram', camp)}",
                    tekst_fb=fb, beeld_tip=f"Foto van {parel.title} zelf",
                    gepland_voor=maandag))

    # 3. Blog-doorplaatsing: enkel als er de voorbije week een artikel verscheen.
    if "blog" not in al:
        vers = (Artikel.query.filter(Artikel.gepubliceerd.is_(True),
                                     Artikel.publicatie_datum >= recent)
                .order_by(Artikel.publicatie_datum.desc()).first())
        if vers:
            camp = f"post-blog-{vers.slug[:40]}"
            link = f"{site}/blog/{vers.slug}"
            ig, fb = _social_ai(
                f"Post die dit blogartikel aankondigt: '{vers.titel}'. "
                f"Samenvatting: {vers.samenvatting or vers.titel}",
                terugval_ig=f"Nieuw op de blog 📖 {vers.titel}. Link in bio!",
                terugval_fb=f"Vers van de blog: {vers.titel} 📖\n"
                            f"{vers.samenvatting}\nLees het hier: {_utm(link, 'facebook', camp)}")
            gemaakt.append(SocialPost(
                soort="blog", onderwerp=vers.titel,
                tekst_ig=f"{ig}\n\nLink in bio → {_utm(link, 'instagram', camp)}",
                tekst_fb=fb, beeld_tip="Beeld passend bij het artikelonderwerp",
                gepland_voor=vandaag + timedelta(days=1)))

    for p in gemaakt:
        db.session.add(p)
    db.session.commit()
    return gemaakt


def maak_weekconcept_artikel():
    """Wekelijks één automatisch blogconcept uit de beste datasuggestie —
    idempotent: bestaat er al een concept van de voorbije 6 dagen, dan niets."""
    from datetime import datetime, timedelta
    from ..models import Artikel
    recent = datetime.utcnow() - timedelta(days=6)
    if Artikel.query.filter(Artikel.gepubliceerd.is_(False),
                            Artikel.updated_at >= recent).first():
        return None
    sug = artikel_suggesties()
    if not sug:
        return None
    beste = sug[0]
    return ai_concept(beste["onderwerp"], beste.get("hoek", ""))
