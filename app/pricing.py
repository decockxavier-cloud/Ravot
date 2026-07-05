"""Budget laag 1: de gepersonaliseerde gezinsprijs (strategienota §3.6)
+ Ravotscore-aggregatie met leeftijdssplitsing (§3.5).
"""
from collections import defaultdict

ADULTS_DEFAULT = 2

AGE_BANDS = [(0, 2, "0-2"), (3, 5, "3-5"), (6, 9, "6-9"), (10, 12, "10-12"), (13, 17, "13+")]


def family_price(price_info, child_ages, adults=ADULTS_DEFAULT):
    """price_info: lijst van tarieven [{name, price, min_age, max_age}].
    Conventie uit de UiT-sync: tarief 'basis' = volwassene; kindertarieven
    dragen een leeftijdsrange; 'gratis' = prijs 0. Retourneert (totaal, gratis_bool)
    of (None, None) als er geen prijsinfo is."""
    if not price_info:
        return None, None
    base = None
    kid_tariffs = []
    for t in price_info:
        price = float(t.get("price", 0) or 0)
        if t.get("name", "").lower() in ("basis", "base", "basistarief"):
            base = price
        elif t.get("min_age") is not None or t.get("max_age") is not None:
            kid_tariffs.append(t)
    if base is None:
        base = max((float(t.get("price", 0) or 0) for t in price_info), default=0.0)

    total = adults * base
    for age in child_ages:
        applied = None
        for t in kid_tariffs:
            lo = t.get("min_age", 0) or 0
            hi = t.get("max_age", 99) if t.get("max_age") is not None else 99
            if lo <= age <= hi:
                applied = float(t.get("price", 0) or 0)
                break
        total += base if applied is None else applied
    return round(total, 2), total == 0


def euro_indicator(total):
    """Budget laag 3: € t.e.m. €€€€ (fase 2 combineert dit met echte kost)."""
    if total is None:
        return None
    if total == 0:
        return "gratis"
    if total <= 15:
        return "€"
    if total <= 35:
        return "€€"
    if total <= 70:
        return "€€€"
    return "€€€€"


def aggregate_ravotscore(reviews):
    """Leeftijdsgesplitste aggregatie — dé differentiator.
    reviews: iterabele met .kid_score en .child_ages (lijst)."""
    overall, per_band = [], defaultdict(list)
    costs = defaultdict(int)
    for rv in reviews:
        overall.append(rv.kid_score)
        for age in rv.child_ages or []:
            for lo, hi, label in AGE_BANDS:
                if lo <= age <= hi:
                    per_band[label].append(rv.kid_score)
        if rv.cost_range:
            costs[rv.cost_range] += 1
    if not overall:
        return None
    bands = {
        label: round(sum(v) / len(v), 1)
        for label, v in per_band.items()
    }
    top_cost = max(costs, key=costs.get) if costs else None
    return {
        "avg": round(sum(overall) / len(overall), 1),
        "count": len(overall),
        "bands": bands,
        "typical_cost": top_cost,
    }
