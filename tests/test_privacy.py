"""De belangrijkste tests: kan gezin A ooit data van gezin B zien? En is
verwijdering echt volledig? (strategienota §10, teststrategie)"""
import json

from tests.conftest import login_as

from app.extensions import db
from app.models import Family, Review, Interaction, SavedEvent, Share


def test_export_contains_only_own_data(client, seed):
    login_as(client, seed["fam_a"])
    data = json.loads(client.get("/mijn/export").get_data(as_text=True))
    assert data["email"] == "a@test.be"
    assert "b@test.be" not in json.dumps(data)


def test_event_page_leaks_no_identity(client, seed):
    """Reviews en pagina's tonen nooit e-mail of naam van een reviewer."""
    fam, ev = seed["fam_a"], seed["events"][0]
    login_as(client, fam)
    client.post(f"/mijn/review/{ev.id}", data={"kid_score": "5", "parent_score": "3"})
    with client.session_transaction() as s:
        s.clear()                                    # bekijk als anonieme bezoeker
    html = client.get(f"/e/{ev.slug}").get_data(as_text=True)
    assert "a@test.be" not in html
    assert "gezin met kinderen van 4, 7" in html      # wél de anonieme afzender


def test_share_default_off_even_for_friends(client, seed):
    """Interesse is standaard NIET zichtbaar, ook niet voor gekoppelde gezinnen."""
    from app.models import Connection
    fam_a, fam_b, ev = seed["fam_a"], seed["fam_b"], seed["events"][0]
    a, b = sorted((fam_a.id, fam_b.id))
    db.session.add(Connection(family_a=a, family_b=b))
    db.session.add(SavedEvent(family_id=fam_a.id, event_id=ev.id))  # bewaard ≠ gedeeld
    db.session.commit()
    login_as(client, fam_b)
    html = client.get(f"/e/{ev.slug}").get_data(as_text=True)
    assert "geïnteresseerd" not in html               # niets zichtbaar zonder opt-in
    # pas na expliciete opt-in van A:
    login_as(client, fam_a)
    client.post(f"/mijn/deel/{ev.id}")
    login_as(client, fam_b)
    html = client.get(f"/e/{ev.slug}").get_data(as_text=True)
    assert "Familie A is geïnteresseerd" in html      # 'geïnteresseerd', nooit 'gaat naar'
    assert "gaat naar" not in html


def test_account_delete_is_complete(client, seed):
    fam, ev = seed["fam_a"], seed["events"][0]
    login_as(client, fam)
    client.post(f"/mijn/bewaar/{ev.id}")
    client.post(f"/mijn/deel/{ev.id}")
    client.post(f"/mijn/review/{ev.id}", data={"kid_score": "4", "parent_score": "2"})
    fid = fam.id
    resp = client.post("/mijn/verwijderen", follow_redirects=True)
    assert resp.status_code == 200
    assert db.session.get(Family, fid) is None
    assert SavedEvent.query.filter_by(family_id=fid).count() == 0
    assert Share.query.filter_by(family_id=fid).count() == 0
    assert Interaction.query.filter_by(family_id=fid).count() == 0
    rv = Review.query.filter_by(event_id=ev.id).first()
    assert rv is not None and rv.family_id is None    # review blijft, definitief losgekoppeld


def test_profile_requires_login(client, seed):
    assert client.get("/mijn/profiel").status_code == 302  # redirect naar login
