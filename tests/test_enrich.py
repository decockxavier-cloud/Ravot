"""AI-verrijking: prompt-opbouw + JSON-parsing, met geïnjecteerde nep-generator."""
import json
from app.extensions import db
from app.models import Event
from app import enrich


def _plek(app):
    with app.app_context():
        ev = Event(source="osm", ext_id="node/1", slug="test-museum",
                   title="Speelmuseum", is_permanent=True, gemeente="Gent",
                   adres="Kraanlei 65", categories=["cultuur"], indoor=True,
                   age_min=0, age_max=12, lat=51.05, lng=3.72)
        db.session.add(ev); db.session.commit()
        return db.session.get(Event, ev.id)


def test_verrijk_parst_net_json(app):
    ev = _plek(app)
    def fake(prompt, system):
        assert "Speelmuseum" in prompt and "Kraanlei 65" in prompt   # feiten in prompt
        return json.dumps({"beschrijving": "Een fijne plek voor kinderen.",
                           "categorie": "cultuur", "leeftijd_min": 3,
                           "leeftijd_max": 12, "binnen": True})
    with app.app_context():
        v = enrich.verrijk_plek(ev, generate=fake)
    assert v["beschrijving"].startswith("Een fijne plek")
    assert v["categorie"] == "cultuur" and v["binnen"] is True
    assert v["leeftijd_min"] == 3 and v["leeftijd_max"] == 12


def test_verrijk_verdraagt_rommel_rond_json(app):
    ev = _plek(app)
    def fake(prompt, system):
        return "Hier is het:\n```json\n{\"beschrijving\":\"Leuk!\",\"binnen\":false}\n```"
    with app.app_context():
        v = enrich.verrijk_plek(ev, generate=fake)
    assert v["beschrijving"] == "Leuk!" and v["binnen"] is False


def test_verrijk_negeert_ongeldige_categorie_en_clamp_leeftijd(app):
    ev = _plek(app)
    def fake(prompt, system):
        return json.dumps({"categorie": "onzin", "leeftijd_min": -5, "leeftijd_max": 99})
    with app.app_context():
        v = enrich.verrijk_plek(ev, generate=fake)
    assert v["categorie"] is None                    # onbekende categorie geweigerd
    assert v["leeftijd_min"] == 0 and v["leeftijd_max"] == 18   # geklemd op 0-18


def test_verrijk_testpagina_rendert(client, app):
    """De admin-testpagina laadt (zonder live model aan te spreken)."""
    import pyotp
    from argon2 import PasswordHasher
    from app.models import Admin
    with app.app_context():
        a = Admin(email="a@ravot.be", pw_hash=PasswordHasher().hash("x"),
                  totp_secret=pyotp.random_base32())
        db.session.add(a); db.session.commit(); aid = a.id
    with client.session_transaction() as s:
        s["admin_id"] = aid; s["admin_2fa_ok"] = True
    r = client.get("/beheer/verrijk")
    assert r.status_code == 200 and "AI-verrijking" in r.get_data(as_text=True)


def _admin(client, app):
    import pyotp
    from argon2 import PasswordHasher
    from app.models import Admin
    with app.app_context():
        a = Admin(email="a@ravot.be", pw_hash=PasswordHasher().hash("x"),
                  totp_secret=pyotp.random_base32())
        db.session.add(a); db.session.commit(); aid = a.id
    with client.session_transaction() as s:
        s["admin_id"] = aid; s["admin_2fa_ok"] = True


def test_batch_maakt_voorstellen(app):
    from app.models import EnrichProposal
    from app import enrich
    with app.app_context():
        for i in range(3):
            db.session.add(Event(source="osm", ext_id=f"node/{i}", slug=f"p-{i}",
                title=f"Plek {i}", is_permanent=True, gemeente="Gent",
                categories=["buiten"], age_min=0, age_max=12, lat=51.0, lng=3.7))
        db.session.commit()
        def fake(prompt, system):
            import json
            return json.dumps({"beschrijving": "Fijne plek voor gezinnen.",
                              "categorie": "buiten", "leeftijd_min": 2,
                              "leeftijd_max": 10, "binnen": False})
        gelukt, mislukt = enrich.verrijk_batch(limit=10, generate=fake)
        assert gelukt == 3 and mislukt == 0
        assert EnrichProposal.query.filter_by(status="pending").count() == 3
        # tweede run maakt geen dubbels (ze hebben al een voorstel)
        assert enrich.verrijk_batch(limit=10, generate=fake) == (0, 0)


def test_goedkeuren_past_voorstel_toe(client, app):
    from app.models import EnrichProposal
    _admin(client, app)
    with app.app_context():
        ev = Event(source="osm", ext_id="node/9", slug="toe-te-passen",
            title="Kaal Museum", is_permanent=True, gemeente="Gent",
            categories=["buiten"], age_min=0, age_max=18, indoor=False,
            lat=51.0, lng=3.7)
        db.session.add(ev); db.session.commit()
        vp = EnrichProposal(event_id=ev.id, beschrijving="Een prachtig museum voor kinderen.",
            categorie="cultuur", leeftijd_min=4, leeftijd_max=12, binnen=True, status="pending")
        db.session.add(vp); db.session.commit()
        pid, eid = vp.id, ev.id
    client.post(f"/beheer/verrijk/voorstel/{pid}/goedkeuren",
                data={"beschrijving": "Een prachtig museum voor kinderen.", "csrf_token": "x"})
    with app.app_context():
        ev = db.session.get(Event, eid)
        assert ev.description.startswith("Een prachtig museum")
        assert ev.categories == ["cultuur"] and ev.indoor is True
        assert ev.age_min == 4 and ev.age_max == 12
        assert db.session.get(EnrichProposal, pid).status == "approved"


def test_afwijzen_zet_status(client, app):
    from app.models import EnrichProposal
    _admin(client, app)
    with app.app_context():
        ev = Event(source="osm", ext_id="node/8", slug="afwijs",
            title="Plek", is_permanent=True, categories=["buiten"],
            age_min=0, age_max=12, lat=51.0, lng=3.7)
        db.session.add(ev); db.session.commit()
        vp = EnrichProposal(event_id=ev.id, beschrijving="x", status="pending")
        db.session.add(vp); db.session.commit()
        pid = vp.id
    client.post(f"/beheer/verrijk/voorstel/{pid}/afwijzen", data={"csrf_token": "x"})
    with app.app_context():
        assert db.session.get(EnrichProposal, pid).status == "rejected"
