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
                inhoud_md="\n".join(body).strip(), gepubliceerd=False)
    db.session.add(a)
    db.session.flush()
    a.slug = _artikel_slug(titel, a.id)
    db.session.commit()
    return a
