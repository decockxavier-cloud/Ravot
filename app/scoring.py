"""Ravot scoringsengine (strategienota §3.1) — deterministisch en uitlegbaar.

score = leeftijdsmatch (hard, met zachte randen)
        × afstand-decay
        × interesse-overlap
        × budgetfactor
        × recency/score-bonus

Werkt identiek voor ingelogde gezinnen en de anonieme modus (tijdelijk profiel).
"""
import math
from dataclasses import dataclass, field


@dataclass
class Profile:
    """Minimaal profiel — ook opbouwbaar zonder account (anonieme modus)."""
    child_ages: list
    lat: float | None = None
    lng: float | None = None
    radius_km: int = 25
    budget_pref: str = "all"           # all | free | low
    interest_weights: dict = field(default_factory=dict)  # categorie -> gewicht


def haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def age_match(child_ages, age_min, age_max):
    """1.0 als minstens één kind in de range valt; 0.5 bij één jaar ernaast
    (zachte rand: een kind van 6 mag een 7+-event nog nét zien); anders 0."""
    if not child_ages:
        return 1.0  # geen leeftijden gekend → niet uitsluiten
    best = 0.0
    for age in child_ages:
        if age_min <= age <= age_max:
            return 1.0
        if age_min - 1 <= age <= age_max + 1:
            best = max(best, 0.5)
    return best


def distance_decay(km, radius_km):
    """1.0 dichtbij, vloeiend dalend, ~0.37 op de rand van de straal, 0 ver erbuiten."""
    if km is None:
        return 0.6  # locatie onbekend → neutraal-laag, niet uitsluiten
    if km > radius_km * 1.5:
        return 0.0
    return math.exp(-km / max(radius_km, 1))


def interest_factor(event_categories, weights):
    if not event_categories or not weights:
        return 1.0
    matched = [weights.get(c, 1.0) for c in event_categories]
    return max(0.4, min(2.0, max(matched)))


def budget_factor(is_free, budget_pref):
    if budget_pref == "free":
        return 1.0 if is_free else 0.15
    if budget_pref == "low":
        return 1.25 if is_free else 0.9
    return 1.1 if is_free else 1.0


def score_event(event, profile: Profile, ravot_avg: float | None = None):
    """event: object met age_min, age_max, lat, lng, categories, is_free."""
    a = age_match(profile.child_ages, event.age_min or 0, event.age_max or 99)
    if a == 0.0:
        return 0.0
    km = None
    if profile.lat is not None and event.lat is not None:
        km = haversine_km(profile.lat, profile.lng, event.lat, event.lng)
    d = distance_decay(km, profile.radius_km)
    if d == 0.0:
        return 0.0
    i = interest_factor(event.categories or [], profile.interest_weights)
    b = budget_factor(bool(event.is_free), profile.budget_pref)
    r = 1.0 + (max(0.0, (ravot_avg or 0) - 3.0) * 0.1)  # bonus voor sterk gescoorde reeksen
    return round(a * d * i * b * r, 4)


# -- Feedbackloop: gewichten bijsturen (eenvoudige bandit) ---------------------
LIKE_STEP, DISMISS_STEP = 0.15, 0.10
W_MIN, W_MAX = 0.3, 2.5


def adjust_weight(current: float, liked: bool) -> float:
    w = current + (LIKE_STEP if liked else -DISMISS_STEP)
    return round(min(W_MAX, max(W_MIN, w)), 3)


def reverse_weight(current: float, liked: bool) -> float:
    """Draai één eerdere like/dismiss-stap terug (voor toggles en wissels),
    zodat herhaald klikken niet stapelt."""
    w = current - (LIKE_STEP if liked else -DISMISS_STEP)
    return round(min(W_MAX, max(W_MIN, w)), 3)
