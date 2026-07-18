"""Ravotscore: één per gezin per event; publiek maar anoniem; pas na 'geweest'."""
from tests.conftest import login_as

from app.extensions import db
from app.models import Review, SavedEvent


def _markeer_geweest(client, fam, ev):
    from datetime import datetime, timedelta
    from app.extensions import db
    if ev.start and ev.start > datetime.utcnow():
        ev.start = datetime.utcnow() - timedelta(hours=5)
        ev.end = datetime.utcnow() - timedelta(hours=2)
        db.session.commit()
    """Bevestig 'we waren erbij' zodat scoren mag (wraak-preventie)."""
    client.post(f"/mijn/geweest/{ev.id}", data={"antwoord": "ja"}, follow_redirects=True)


def test_one_review_per_family_per_event(client, seed):
    fam, ev = seed["fam_a"], seed["events"][0]
    login_as(client, fam)
    _markeer_geweest(client, fam, ev)
    data = {"kid_score": "5", "parent_score": "3", "tag": ["mooie natuur"]}
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
    _markeer_geweest(client, fam, ev)
    client.post(f"/mijn/review/{ev.id}", data={"kid_score": "4", "parent_score": "2"})
    rv = Review.query.first()
    assert sorted(rv.child_ages) == [4, 7]
    pub = rv.public_dict()
    assert "a@test.be" not in str(pub) and "Familie A" not in str(pub)
    assert pub["afzender"] == "gezin met kinderen van 4, 7"


def test_review_validation(client, seed):
    fam, ev = seed["fam_a"], seed["events"][0]
    login_as(client, fam)
    _markeer_geweest(client, fam, ev)
    # ongeldige kid_score
    assert client.post(f"/mijn/review/{ev.id}", data={"kid_score": "9", "parent_score": "3"}).status_code == 400
    # ongeldige parent_score
    assert client.post(f"/mijn/review/{ev.id}", data={"kid_score": "4", "parent_score": "9"}).status_code == 400


def test_score_pas_na_geweest(client, seed):
    """Wraak-preventie: zonder 'geweest' krijg je de bevestigingsvraag, geen score."""
    fam, ev = seed["fam_a"], seed["events"][0]
    login_as(client, fam)
    # direct scoren zonder 'geweest' → toont de geweest-vraag, slaat niets op
    r = client.post(f"/mijn/review/{ev.id}", data={"kid_score": "5", "parent_score": "3"},
                    follow_redirects=True)
    assert Review.query.filter_by(family_id=fam.id, event_id=ev.id).count() == 0
    assert "erbij" in r.get_data(as_text=True).lower() or "geweest" in r.get_data(as_text=True).lower()


def test_karakter_schuifjes_bewaard(client, seed):
    fam, ev = seed["fam_a"], seed["events"][0]
    login_as(client, fam)
    _markeer_geweest(client, fam, ev)
    client.post(f"/mijn/review/{ev.id}", data={
        "kid_score": "4", "parent_score": "3",
        "sfeer_rustig_actief": "5", "sfeer_prijs": "2", "sfeer_leeftijd": "1"})
    rv = Review.query.first()
    assert rv.sfeer_rustig_actief == 5
    assert rv.sfeer_prijs == 2
    assert rv.sfeer_leeftijd == 1
