"""Kwaliteitsscore — laat de data zichzelf beoordelen (0–100).

Meet hoe COMPLEET een fiche is (naam, foto, beschrijving, adres, website,
prijsinfo, bronbetrouwbaarheid, gezinsreviews). Rommel zakt automatisch weg,
goede fiches drijven boven — zonder handwerk.

Strikt gescheiden van de Ravotscore: kwaliteit = "hoe volledig is de fiche"
(technisch, van ons); Ravotscore = "hoe goed is de plek" (van gezinnen).
Betalen (Partner) speelt hier geen enkele rol.
"""

# Generieke fallback-labels: een titel die hiermee begint is geen echte naam.
_GENERIEK = ("speeltuin", "park", "natuurgebied", "zwemplek", "minigolf",
             "pretpark", "zoo", "aquarium", "museum", "uitzichtpunt",
             "attractie", "kasteel", "uitstap", "waterpark")

# Bron-bonus: gecureerde/opgeschoonde bronnen zijn betrouwbaarder dan ruwe.
_BRON_BONUS = {"uit": 10, "feed": 10, "wd": 8, "user": 8, "tv": 5, "tm": 5, "osm": 0}


def _echte_naam(titel):
    t = (titel or "").strip().lower()
    if len(t) < 4:
        return False
    # Fallback-vormen: exact een generiek label, of "label — straat/gemeente"
    for g in _GENERIEK:
        if t == g or t.startswith(f"{g} —") or t.startswith(f"{g} -"):
            return False
    return True


def bereken_kwaliteit(ev, heeft_reviews=None):
    """Score 0–100 voor één event. `heeft_reviews` mag meegegeven worden om
    een query uit te sparen (bv. bij bulk); anders wordt het opgezocht."""
    score = 0
    if _echte_naam(ev.title):
        score += 30
    if ev.image_url:
        score += 20
    d = (ev.description or "").strip()
    if len(d) >= 80:
        score += 15
    elif len(d) >= 20:
        score += 8
    if ev.adres:
        score += 10
    if ev.source_url:
        score += 10
    if ev.price_info:
        score += 5
    score += _BRON_BONUS.get(ev.source, 0)
    if heeft_reviews is None:
        try:
            from .models import Review
            heeft_reviews = Review.query.filter_by(event_id=ev.id).count() > 0
        except Exception:
            heeft_reviews = False
    if heeft_reviews:
        score += 15          # bewezen relevant voor gezinnen
    return min(score, 100)


def herbereken_alles(batch=500):
    """Herbereken de kwaliteit van alle events. Retourneert aantal bijgewerkt."""
    from .extensions import db
    from .models import Event, Review
    # één set met alle event-ids die reviews hebben (spaart n queries uit)
    met_reviews = {r[0] for r in db.session.query(Review.event_id).distinct().all()}
    n = 0
    for ev in Event.query.yield_per(batch):
        nieuw = bereken_kwaliteit(ev, heeft_reviews=ev.id in met_reviews)
        if ev.quality != nieuw:
            ev.quality = nieuw
            n += 1
        if n and n % batch == 0:
            db.session.commit()
    db.session.commit()
    return n
