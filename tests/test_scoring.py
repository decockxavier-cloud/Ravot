"""Kernlogica: leeftijdsmatch, afstand, budget, gezinsprijs, aggregatie."""
from app.scoring import Profile, age_match, distance_decay, score_event, adjust_weight
from app.pricing import family_price, euro_indicator, aggregate_ravotscore


class Ev:
    def __init__(self, **kw):
        self.age_min = kw.get("age_min", 0); self.age_max = kw.get("age_max", 99)
        self.lat = kw.get("lat"); self.lng = kw.get("lng")
        self.categories = kw.get("categories", []); self.is_free = kw.get("is_free", False)


def test_age_hard_filter():
    assert age_match([4, 7], 3, 10) == 1.0
    assert age_match([4], 6, 12) == 0.0          # buiten range → uitsluiten
    assert age_match([5], 6, 12) == 0.5          # zachte rand: één jaar ernaast
    assert age_match([], 6, 12) == 1.0           # geen leeftijden gekend → niet uitsluiten


def test_distance_decay():
    assert distance_decay(0, 25) == 1.0
    assert 0.3 < distance_decay(25, 25) < 0.45   # ~e^-1 op de rand
    assert distance_decay(60, 25) == 0.0         # ver buiten straal


def test_score_excludes_wrong_age(app):
    p = Profile(child_ages=[4, 7], lat=50.946, lng=3.123, radius_km=25)
    assert score_event(Ev(age_min=13, age_max=17, lat=50.95, lng=3.12), p) == 0.0
    assert score_event(Ev(age_min=2, age_max=10, lat=50.95, lng=3.12), p) > 0


def test_budget_pref_free(app):
    p = Profile(child_ages=[4], lat=50.9, lng=3.1, radius_km=25, budget_pref="free")
    paid = score_event(Ev(age_min=2, age_max=10, lat=50.9, lng=3.1, is_free=False), p)
    free = score_event(Ev(age_min=2, age_max=10, lat=50.9, lng=3.1, is_free=True), p)
    assert free > paid * 4                        # gratis domineert bij 'enkel gratis'


def test_family_price_with_kid_tariff():
    tarieven = [{"name": "basis", "price": 8},
                {"name": "kinderen", "price": 5, "min_age": 3, "max_age": 12}]
    total, is_free = family_price(tarieven, [4, 7])   # 2 volw × 8 + 2 kind × 5
    assert total == 26.0 and is_free is False
    assert euro_indicator(0) == "gratis" and euro_indicator(26) == "€€"


def test_weight_bounds():
    w = 1.0
    for _ in range(30):
        w = adjust_weight(w, liked=True)
    assert w <= 2.5
    for _ in range(60):
        w = adjust_weight(w, liked=False)
    assert w >= 0.3


def test_aggregate_age_bands():
    class R:  # leeftijdsgesplitste aggregatie
        def __init__(self, score, ages, cost=None):
            self.kid_score = score; self.child_ages = ages; self.cost_range = cost
    agg = aggregate_ravotscore([R(5, [4]), R(4, [4, 7]), R(2, [10], "20-50")])
    assert agg["count"] == 3
    assert agg["bands"]["3-5"] == 4.5             # kleuters scoren hoger
    assert agg["bands"]["10-12"] == 2.0
    assert agg["typical_cost"] == "20-50"
