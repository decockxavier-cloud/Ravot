"""Patch 184: een ander gezin uitnodigen levert punten op — maar pas wanneer
dat gezin zijn eerste eigen punten verdient, zodat wegwerp-aanmeldingen
niets opleveren. Plus: het Ravotpas-blok op de landing is gekalmeerd."""
from app.extensions import db
from app.models import Family, RavotPunt, Setting


def _uitnodiger(app):
    with app.app_context():
        fam = Family(email="uitnodiger@t.be", postcode="8800")
        db.session.add(fam)
        db.session.commit()
        from app.punten import deelcode
        return fam.id, deelcode(fam)


def test_deelcode_is_stabiel_en_uniek(app):
    with app.app_context():
        a = Family(email="a@t.be", postcode="8800")
        b = Family(email="b@t.be", postcode="8800")
        db.session.add_all([a, b])
        db.session.commit()
        from app.punten import deelcode
        ca, cb = deelcode(a), deelcode(b)
        assert len(ca) == 8 and ca != cb
        assert deelcode(a) == ca                 # tweede aanroep: zelfde code


def test_bonus_pas_bij_eerste_echte_actie(client, app):
    iid, code = _uitnodiger(app)
    client.get(f"/?ref={code.lower()}")          # deellink gevolgd
    with client.session_transaction() as s:
        s["pending_email"] = "nieuw@t.be"
    client.post("/mijn/start", data={"postcode": "9000", "birth_year": "2019"},
                follow_redirects=True)
    with app.app_context():
        nieuw = Family.query.filter_by(email="nieuw@t.be").first()
        assert nieuw.invited_by == iid
        assert RavotPunt.query.filter_by(family_id=iid).count() == 0
        from app.punten import ken_toe
        ken_toe(nieuw.id, "review", ref_id=1)    # eerste echte actie
        db.session.commit()
        bonus = RavotPunt.query.filter_by(family_id=iid,
                                          reden="uitnodiging").all()
        assert len(bonus) == 1 and bonus[0].punten == 25
        ken_toe(nieuw.id, "geweest", ref_id=1)   # tweede actie: geen dubbele
        db.session.commit()
        assert RavotPunt.query.filter_by(family_id=iid,
                                         reden="uitnodiging").count() == 1


def test_registratie_zonder_deellink_geeft_niets(client, app):
    iid, _ = _uitnodiger(app)
    with client.session_transaction() as s:
        s["pending_email"] = "los@t.be"
    client.post("/mijn/start", data={"postcode": "9000", "birth_year": "2019"},
                follow_redirects=True)
    with app.app_context():
        assert Family.query.filter_by(email="los@t.be").first().invited_by is None


def test_deellink_op_beloningenpagina(client, app):
    iid, code = _uitnodiger(app)
    with app.app_context():
        db.session.add(Setting(key="beloningen_aan", value="1"))
        db.session.commit()
    with client.session_transaction() as s:
        s["family_id"] = iid
    h = client.get("/mijn/beloningen").get_data(as_text=True)
    assert f"?ref={code}" in h and "+25" in h


def test_uitnodiging_in_publieke_uitleg(client):
    h = client.get("/ravotscore").get_data(as_text=True)
    assert "ander gezin uitnodigen" in h and "+25" in h
