"""Ravot Partner: Mollie-betaling, webhook-verificatie, zichtbare voordelen."""
from datetime import datetime, timedelta
from app.extensions import db
from app.models import Event, Operator, OperatorClaim, PartnerPayment
from app import mollie


def _zaak_met_claim(app, email="info@zaak.be"):
    with app.app_context():
        ev = Event(source="osm", ext_id="node/p1", slug="partner-zaak",
                   title="Speelcafé De Ravotter", is_permanent=True, gemeente="Gent",
                   postcode="9000", lat=51.0, lng=3.7, age_min=0, age_max=12,
                   categories=["binnen"])
        op = Operator(email=email)
        db.session.add_all([ev, op]); db.session.commit()
        db.session.add(OperatorClaim(operator_id=op.id, event_id=ev.id, status="approved"))
        db.session.commit()
        return ev.id, op.id


class _Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status
    def json(self): return self._data
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_webhook_paid_activeert_partner(app):
    """Webhook met geldig id -> status bij Mollie opgehaald -> partner actief."""
    eid, oid = _zaak_met_claim(app)
    with app.app_context():
        p = PartnerPayment(operator_id=oid, event_id=eid, plan="maand",
                           amount="19.00", mollie_id="tr_test123")
        db.session.add(p); db.session.commit()
        def fake_get(url, **kw):
            assert "tr_test123" in url                      # verificatie bij Mollie zelf
            return _Resp({"id": "tr_test123", "status": "paid"})
        mollie.verwerk_webhook("tr_test123", http_get=fake_get)
        ev = db.session.get(Event, eid)
        assert ev.partner_until is not None and ev.partner_until > datetime.utcnow()
        assert PartnerPayment.query.first().status == "paid"


def test_webhook_body_spoofing_werkt_niet(app):
    """SECURITY: een aanvaller die 'paid' meestuurt in de body wint niets —
    de status komt altijd van Mollie zelf. Zegt Mollie 'open', dan geen partner."""
    eid, oid = _zaak_met_claim(app)
    with app.app_context():
        p = PartnerPayment(operator_id=oid, event_id=eid, plan="maand",
                           amount="19.00", mollie_id="tr_spoof")
        db.session.add(p); db.session.commit()
        def fake_get(url, **kw):
            return _Resp({"id": "tr_spoof", "status": "open"})   # Mollie zegt: niet betaald
        mollie.verwerk_webhook("tr_spoof", http_get=fake_get)
        assert db.session.get(Event, eid).partner_until is None  # géén activatie


def test_webhook_onbekend_id_genegeerd(app):
    with app.app_context():
        assert mollie.verwerk_webhook("tr_bestaatniet",
                                      http_get=lambda *a, **k: _Resp({})) is False


def test_webhook_idempotent_geen_dubbele_verlenging(app):
    """Twee keer dezelfde paid-webhook mag maar één keer verlengen."""
    eid, oid = _zaak_met_claim(app)
    with app.app_context():
        p = PartnerPayment(operator_id=oid, event_id=eid, plan="maand",
                           amount="19.00", mollie_id="tr_dubbel")
        db.session.add(p); db.session.commit()
        fake = lambda url, **kw: _Resp({"id": "tr_dubbel", "status": "paid"})
        mollie.verwerk_webhook("tr_dubbel", http_get=fake)
        eerste = db.session.get(Event, eid).partner_until
        mollie.verwerk_webhook("tr_dubbel", http_get=fake)      # herhaalde webhook
        assert db.session.get(Event, eid).partner_until == eerste


def test_verlenging_stapelt_op_resterende_tijd(app):
    eid, oid = _zaak_met_claim(app)
    with app.app_context():
        ev = db.session.get(Event, eid)
        ev.partner_until = datetime.utcnow() + timedelta(days=10)  # nog 10 dagen
        p = PartnerPayment(operator_id=oid, event_id=eid, plan="maand",
                           amount="19.00", mollie_id="tr_verleng")
        db.session.add(p); db.session.commit()
        mollie.verwerk_webhook("tr_verleng",
            http_get=lambda u, **k: _Resp({"id": "tr_verleng", "status": "paid"}))
        ev = db.session.get(Event, eid)
        assert ev.partner_until > datetime.utcnow() + timedelta(days=39)  # ~10+31


def test_partnerblok_op_gemeentepagina_gelabeld(client, app):
    eid, _ = _zaak_met_claim(app)
    with app.app_context():
        ev = db.session.get(Event, eid)
        ev.partner_until = datetime.utcnow() + timedelta(days=30)
        db.session.commit()
    html = client.get("/gent").get_data(as_text=True)
    assert "Partners in Gent" in html
    assert "betaalde vermelding" in html            # transparantie-label
    assert "geen invloed op scores" in html


def test_partner_pagina_vereist_goedgekeurde_claim(client, app):
    """SECURITY: Partner kopen kan enkel voor je eigen goedgekeurde zaak."""
    eid, oid = _zaak_met_claim(app)
    with app.app_context():
        ander = Operator(email="ander@zaak.be")
        db.session.add(ander); db.session.commit(); ander_id = ander.id
    with client.session_transaction() as s:
        s["operator_id"] = ander_id
    assert client.get(f"/uitbater/partner/{eid}").status_code == 403


def test_webhook_route_zonder_csrf_bereikbaar(client, app):
    """De Mollie-webhook moet zonder CSRF-token werken (externe POST) en
    antwoordt altijd 200."""
    app.config["WTF_CSRF_ENABLED"] = True
    r = client.post("/uitbater/mollie-webhook", data={"id": "tr_x"})
    assert r.status_code == 200


def test_founding_partner_gratis_jaar(client, app):
    """Founding: gratis claim -> meteen een jaar Partner, geteld in het maximum."""
    from datetime import datetime, timedelta
    eid, oid = _zaak_met_claim(app, email="founding@zaak.be")
    with client.session_transaction() as s:
        s["operator_id"] = oid
    r = client.post(f"/uitbater/founding/{eid}", data={"csrf_token": "x"})
    assert r.status_code in (302, 303)
    with app.app_context():
        ev = db.session.get(Event, eid)
        assert ev.partner_until > datetime.utcnow() + timedelta(days=300)
        p = PartnerPayment.query.filter_by(plan="founding").first()
        assert p is not None and p.amount == "0.00" and p.status == "paid"
    # tweede keer voor dezelfde zaak: geweigerd
    client.post(f"/uitbater/founding/{eid}", data={"csrf_token": "x"})
    with app.app_context():
        assert PartnerPayment.query.filter_by(plan="founding").count() == 1


def test_founding_max_afgedwongen(client, app):
    from app.models import Setting
    eid, oid = _zaak_met_claim(app, email="laat@zaak.be")
    with app.app_context():
        db.session.merge(Setting(key="founding_max", value="0"))   # volzet
        db.session.commit()
    with client.session_transaction() as s:
        s["operator_id"] = oid
    client.post(f"/uitbater/founding/{eid}", data={"csrf_token": "x"})
    with app.app_context():
        assert PartnerPayment.query.filter_by(plan="founding").count() == 0
        assert db.session.get(Event, eid).partner_until is None
