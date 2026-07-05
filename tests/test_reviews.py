"""Ravotscore: één per gezin per event; publiek maar anoniem."""
from tests.conftest import login_as

from app.extensions import db
from app.models import Review


def test_one_review_per_family_per_event(client, seed):
    fam, ev = seed["fam_a"], seed["events"][0]
    login_as(client, fam)
    data = {"kid_score": "5", "parent_score": "3", "cost_range": "<20", "tag": ["goed bij regen"]}
    r1 = client.post(f"/mijn/review/{ev.id}", data=data, follow_redirects=True)
    assert r1.status_code == 200
    assert Review.query.filter_by(family_id=fam.id, event_id=ev.id).count() == 1
    r2 = client.post(f"/mijn/review/{ev.id}", data={"kid_score": "1", "parent_score": "1"},
                     follow_redirects=True)
    assert Review.query.filter_by(family_id=fam.id, event_id=ev.id).count() == 1  # geblokkeerd
    rv = Review.query.first()
    assert rv.kid_score == 5                            # eerste score blijft staan


def test_review_snapshot_has_only_ages(client, seed):
    fam, ev = seed["fam_a"], seed["events"][0]
    login_as(client, fam)
    client.post(f"/mijn/review/{ev.id}", data={"kid_score": "4", "parent_score": "2"})
    rv = Review.query.first()
    assert sorted(rv.child_ages) == [4, 7]
    pub = rv.public_dict()
    assert "a@test.be" not in str(pub) and "Familie A" not in str(pub)
    assert pub["afzender"] == "gezin met kinderen van 4, 7"


def test_review_validation(client, seed):
    login_as(client, seed["fam_a"])
    ev = seed["events"][0]
    assert client.post(f"/mijn/review/{ev.id}", data={"kid_score": "9", "parent_score": "3"}).status_code == 400
    assert client.post(f"/mijn/review/{ev.id}", data={"kid_score": "4", "parent_score": "3",
                                                      "cost_range": "onzin"}).status_code == 400
