"""Patch 95: eerlijke feedbackknoppen en een scoreflow die terugkeert.

- 'Leuk' en 'Niet voor ons' zijn toggles die elkaar uitsluiten; gewichten
  stapelen niet meer bij herhaald klikken.
- Een weggeklikt event zakt hard achteraan in de gepersonaliseerde lijsten
  (score × 0.02) maar blijft vindbaar.
- Wie vanaf Mijn Ravot een score geeft, landt daarna terug op Mijn Ravot.
"""
import re
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import (Child, Event, Family, Interaction, Interest, Review,
                        SavedEvent)


@pytest.fixture()
def gezin(app, client):
    with app.app_context():
        fam = Family(email="f95@t.be", postcode="8800", radius_km=25)
        db.session.add(fam)
        db.session.flush()
        db.session.add(Child(family_id=fam.id, birth_year=2019))
        nu = datetime.utcnow()
        a = Event(uit_id="95a", slug="ev-a", title="Natuurwandeling A",
                  start=nu + timedelta(hours=3), end=nu + timedelta(hours=5),
                  gemeente="Roeselare", postcode="8800", lat=50.946, lng=3.123,
                  categories=["natuur"], age_min=3, age_max=10)
        b = Event(uit_id="95b", slug="ev-b", title="Natuurwandeling B",
                  start=nu + timedelta(hours=3), end=nu + timedelta(hours=5),
                  gemeente="Roeselare", postcode="8800", lat=50.946, lng=3.123,
                  categories=["natuur"], age_min=3, age_max=10)
        db.session.add_all([a, b])
        from app.models import PostcodeCentroid
        db.session.add(PostcodeCentroid(postcode="8800", gemeente="Roeselare",
                                        lat=50.946, lng=3.123, n_events=2))
        db.session.commit()
        ids = (fam.id, a.id, b.id)
    with client.session_transaction() as s:
        s["family_id"] = ids[0]
    return client, ids


def _feedback(client, eid, verdict):
    return client.post(f"/mijn/feedback/{eid}/{verdict}", follow_redirects=True)


def test_dismiss_is_toggle_en_stapelt_niet(gezin, app):
    client, (fid, a, _) = gezin
    _feedback(client, a, "dismiss")
    _feedback(client, a, "dismiss")   # tweede klik = ongedaan maken
    with app.app_context():
        assert Interaction.query.filter_by(family_id=fid, event_id=a,
                                           type="dismiss").count() == 0
        w = Interest.query.filter_by(family_id=fid, category="natuur").first()
        assert w is None or abs(w.weight - 1.0) < 1e-6  # netto nul effect


def test_like_en_dismiss_sluiten_elkaar_uit(gezin, app):
    client, (fid, a, _) = gezin
    _feedback(client, a, "like")
    _feedback(client, a, "dismiss")   # wissel: like moet verdwijnen
    with app.app_context():
        soorten = {i.type for i in Interaction.query.filter_by(
            family_id=fid, event_id=a).all() if i.type in ("like", "dismiss")}
        assert soorten == {"dismiss"}
        w = Interest.query.filter_by(family_id=fid, category="natuur").first()
        assert w is not None and abs(w.weight - 0.9) < 1e-6  # enkel de dismiss telt


def test_weggeklikt_event_zakt_hard_achteraan(gezin):
    client, (_, a, b) = gezin
    _feedback(client, a, "dismiss")
    html = client.get("/vandaag").data.decode()
    assert "Natuurwandeling A" in html          # nog vindbaar, niet verdwenen
    assert html.index("Natuurwandeling B") < html.index("Natuurwandeling A")


def test_dismissknop_toont_actieve_toestand(gezin):
    client, (_, a, _) = gezin
    _feedback(client, a, "dismiss")
    html = client.get("/vandaag").data.decode()
    assert "✕ Niet voor ons" in html
    assert "actie-uit" in html


def test_score_vanaf_mijn_ravot_keert_terug(gezin, app):
    client, (fid, a, _) = gezin
    with app.app_context():
        db.session.add(SavedEvent(family_id=fid, event_id=a, geweest=True))
        db.session.commit()
    r = client.get(f"/mijn/review/{a}?terug=mijn")
    assert r.status_code == 200
    tok = re.search(rb'name="csrf_token" value="([^"]+)"', r.data)
    data = {"kid_score": "5", "parent_score": "3", "terug": "mijn"}
    if tok:
        data["csrf_token"] = tok.group(1).decode()
    r = client.post(f"/mijn/review/{a}", data=data, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].rstrip("/").endswith("/mijn")


def test_score_vanaf_eventpagina_blijft_op_event(gezin, app):
    client, (fid, _, b) = gezin
    with app.app_context():
        db.session.add(SavedEvent(family_id=fid, event_id=b, geweest=True))
        db.session.commit()
    r = client.get(f"/mijn/review/{b}")
    tok = re.search(rb'name="csrf_token" value="([^"]+)"', r.data)
    data = {"kid_score": "4", "parent_score": "2"}
    if tok:
        data["csrf_token"] = tok.group(1).decode()
    r = client.post(f"/mijn/review/{b}", data=data, follow_redirects=False)
    assert r.status_code == 302
    assert "/e/ev-b" in r.headers["Location"]
