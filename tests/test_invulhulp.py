"""Patch 155: invulhulp-machtiging — expliciet mandaat, herleidbaar, intrekbaar."""
from datetime import datetime, timedelta
from app.extensions import db
from app.models import (Event, Operator, OperatorClaim, Verkoper,
                        VerkoperMachtiging, EditProposal)


def _opzet(app):
    with app.app_context():
        op = Operator(email="u@t.be")
        vk = Verkoper(naam="Sam", email="s@v.be", code="RAV-T1", commissie_pct=15)
        db.session.add_all([op, vk]); db.session.flush()
        ev = Event(title="Bistro", slug="bi", source="user", is_permanent=True,
                   pending=False, hidden=False)
        db.session.add(ev); db.session.flush()
        db.session.add(OperatorClaim(operator_id=op.id, event_id=ev.id,
                                     status="approved"))
        db.session.add(VerkoperMachtiging(verkoper_id=vk.id, event_id=ev.id,
                                          operator_id=op.id,
                                          tot=datetime.utcnow() + timedelta(days=30)))
        db.session.commit()
        return op.id, vk.id, ev.id


def test_gemachtigde_verkoper_dient_wijziging_in(client, app):
    _, vkid, evid = _opzet(app)
    with client.session_transaction() as s:
        s["verkoper_id"] = vkid
    client.post(f"/verkoper/fiche/{evid}", data={"beschrijving": "Nieuw!"})
    with app.app_context():
        ep = EditProposal.query.first()
        assert ep is not None and ep.verkoper_id == vkid
        assert ep.changes.get("description") == "Nieuw!"


def test_zonder_machtiging_403(client, app):
    _, vkid, _ = _opzet(app)
    with app.app_context():
        ander = Event(title="Ander", slug="an", source="user", is_permanent=True,
                      pending=False, hidden=False)
        db.session.add(ander); db.session.commit()
        aid = ander.id
    with client.session_transaction() as s:
        s["verkoper_id"] = vkid
    assert client.get(f"/verkoper/fiche/{aid}").status_code == 403


def test_verlopen_machtiging_403(client, app):
    _, vkid, evid = _opzet(app)
    with app.app_context():
        VerkoperMachtiging.query.first().tot = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()
    with client.session_transaction() as s:
        s["verkoper_id"] = vkid
    assert client.get(f"/verkoper/fiche/{evid}").status_code == 403
