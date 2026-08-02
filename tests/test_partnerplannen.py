"""Patch 153: drie formules, rechtenmatrix, caps, verkoperscode en commissie."""
from datetime import datetime, timedelta
from app.extensions import db
from app.models import Event, Verkoper, PartnerPayment, Operator
from app import mollie


def _zaak(app, plan, slug, **kw):
    with app.app_context():
        e = Event(title=slug.title(), slug=slug, source="user", is_permanent=True,
                  pending=False, hidden=False, gemeente="Roeselare",
                  postcode="8800", quality=70, curated=True, **kw)
        e.partner_until = datetime.utcnow() + timedelta(days=100)
        e.partner_plan = plan
        db.session.add(e)
        db.session.commit()
        return e.id


def test_rechtenmatrix(app):
    with app.app_context():
        combi = db.session.get(Event, _zaak(app, "combi", "z-combi"))
        feest = db.session.get(Event, _zaak(app, "feest", "z-feest"))
        legacy = db.session.get(Event, _zaak(app, "jaar", "z-legacy"))
        assert mollie.is_zichtbaar_partner(combi) and mollie.is_feestpartner(combi)
        assert not mollie.is_zichtbaar_partner(feest)
        assert mollie.is_feestpartner(feest)
        # Legacy-plannen (bestaande partners) behouden alles.
        assert mollie.is_zichtbaar_partner(legacy) and mollie.is_feestpartner(legacy)


def test_feestplan_geen_ster_op_fiche(client, app):
    _zaak(app, "feest", "feestzaak")
    _zaak(app, "combi", "combizaak")
    assert "⭐ Partner" not in client.get("/e/feestzaak").get_data(as_text=True)
    assert "⭐ Partner" in client.get("/e/combizaak").get_data(as_text=True)


def test_caps_tellen_per_pool(app):
    with app.app_context():
        _zaak(app, "partner", "c-1")
        _zaak(app, "combi", "c-2")
        _zaak(app, "feest", "c-3")
        assert mollie.plekken_bezet("Roeselare", "zichtbaar") == 2  # partner+combi
        assert mollie.plekken_bezet("Roeselare", "feest") == 2     # feest+combi


def test_prijzen_per_formule(app):
    with app.app_context():
        assert mollie.prijs("partner") == "200.00"
        assert mollie.prijs("feest") == "250.00"
        assert mollie.prijs("combi") == "360.00"


def test_commissie_via_code(app):
    with app.app_context():
        op = Operator(email="o@t.be")
        vk = Verkoper(naam="Sam", email="s@v.be", code="RAV-TEST", commissie_pct=15)
        db.session.add_all([op, vk])
        db.session.flush()
        eid = _zaak(app, "combi", "c-deal")
        db.session.add(PartnerPayment(operator_id=1, event_id=eid, plan="combi",
                                      verkoper_id=vk.id, amount="435.60",
                                      status="paid", mollie_id="tr_x",
                                      paid_at=datetime.utcnow()))
        db.session.commit()
        # excl = 435.60/1.21 = 360 → 15% = 54
        b = PartnerPayment.query.first()
        excl = float(b.amount) / (1 + mollie.btw_pct() / 100)
        assert round(excl * b.verkoper.commissie_pct / 100, 2) == 54.00
