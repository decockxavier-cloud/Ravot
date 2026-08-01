"""Patch 142: de uitbatersflow van de kandidaat-partner afdekken.

Gemeld door een echte kandidaat-partner: zaak goedgekeurd maar niet op de
kaart (geen ligging), openingsuren niet invulbaar, en zes voorzieningen-
checkboxes die stil genegeerd werden bij goedkeuring (whitelist-mismatch).
"""
from app.extensions import db
from app.models import Event, EditProposal, EDIT_VELDEN


def test_voorzieningen_in_whitelist():
    for veld in ("kinderstoel", "speelhoek", "kindermenu", "verzorgingstafel",
                 "buggy_ok", "omheind", "openingsuren", "subtype", "lat", "lng"):
        assert veld in EDIT_VELDEN, veld


def test_urenparser_rondrit():
    from app.services.openingsuren import parse_dagtekst, dag_tekst
    w, ok = parse_dagtekst("09:00-12:00, 13:00-18:00")
    assert ok and w == [["09:00", "12:00"], ["13:00", "18:00"]]
    assert dag_tekst(w) == "09:00-12:00, 13:00-18:00"
    assert parse_dagtekst("") == (None, True)
    assert parse_dagtekst("gesloten") == (None, True)
    assert parse_dagtekst("blabla") == (None, False)


def test_goedgekeurd_wijzigingsvoorstel_past_alles_toe(app):
    """De kern van de klacht: dient de uitbater uren/soort/ligging/voorzieningen
    in, dan moeten die na goedkeuring écht op de fiche staan."""
    with app.app_context():
        ev = Event(title="Pannenkoekenhuis Test", slug="pk-test", source="user",
                   is_permanent=True, pending=False, hidden=False,
                   gemeente="Roeselare", postcode="8800")
        db.session.add(ev)
        db.session.commit()
        voorstel = EditProposal(operator_id=1, event_id=ev.id, changes={
            "subtype": "horeca" if "horeca" in __import__("app.types", fromlist=["TYPES"]).TYPES else "playground",
            "openingsuren": {"ma": None, "di": [["09:00", "18:00"]], "wo": [["09:00", "18:00"]],
                             "do": [["09:00", "18:00"]], "vr": [["09:00", "18:00"]],
                             "za": [["10:00", "20:00"]], "zo": None},
            "lat": 50.95, "lng": 3.12,
            "kinderstoel": True, "speelhoek": True, "kindermenu": True,
        })
        db.session.add(voorstel)
        db.session.commit()
        # Toepassen zoals de admin-route dat doet (whitelist + setattr).
        for veld, waarde in voorstel.changes.items():
            if veld in EDIT_VELDEN:
                setattr(ev, veld, waarde)
        db.session.commit()

        vers = db.session.get(Event, ev.id)
        assert vers.lat == 50.95 and vers.lng == 3.12
        assert vers.openingsuren["di"] == [["09:00", "18:00"]]
        assert vers.kinderstoel and vers.speelhoek and vers.kindermenu
        assert vers.subtype


def test_zaak_met_ligging_staat_op_de_kaart(client, app):
    """Goedgekeurde eigen zaak mét ligging hoort op /verkennen te staan."""
    with app.app_context():
        db.session.add(Event(title="Zaak Op De Kaart", slug="zodk", source="user",
                             is_permanent=True, pending=False, hidden=False,
                             curated=True, quality=60, gemeente="Roeselare",
                             postcode="8800", lat=50.95, lng=3.12))
        db.session.commit()
    html = client.get("/verkennen").get_data(as_text=True)
    assert "Zaak Op De Kaart" in html


def test_auto_ok_past_wijziging_meteen_toe(client, app):
    """Patch 147: met 'uitbater_auto_ok' aan gaan fichewijzigingen van
    goedgekeurde uitbaters meteen live, mét logboekregel."""
    from app.models import Operator, OperatorClaim, Setting, EditProposal
    with app.app_context():
        op = Operator(email="a@t.be"); db.session.add(op)
        ev = Event(title="Zaak Auto", slug="za", source="user", is_permanent=True,
                   pending=False, hidden=False)
        db.session.add(ev); db.session.flush()
        db.session.add(OperatorClaim(operator_id=op.id, event_id=ev.id,
                                     status="approved"))
        db.session.add(Setting(key="uitbater_auto_ok", value="1"))
        db.session.commit()
        opid, evid = op.id, ev.id
    with client.session_transaction() as s:
        s["operator_id"] = opid
    client.post(f"/uitbater/fiche/{evid}", data={"description": "Nieuw!"})
    with app.app_context():
        assert db.session.get(Event, evid).description == "Nieuw!"
        assert EditProposal.query.filter_by(event_id=evid,
                                            status="approved").count() == 1
