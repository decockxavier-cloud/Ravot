"""Patch 143: terugkeer van Mollie + Odoo-factuurdetails.

Gemeld na een echte testbetaling: uitgelogd na het betalen (leek mislukt),
factuur in het verkeerde dagboek, bestaande Odoo-klant met fout btw-nummer
stil hergebruikt, en gratis partners onterecht in 'betaald zonder factuur'.
"""
from app.extensions import db
from app.models import Event, Operator, OperatorClaim, PartnerPayment


def _betaling(app):
    with app.app_context():
        op = Operator(email="u@test.be", bedrijfsnaam="Yamy BV",
                      btw_nummer="BE0505624079")
        db.session.add(op)
        ev = Event(title="Zaak", slug="zaak-x", source="user", is_permanent=True,
                   pending=False, hidden=False)
        db.session.add(ev); db.session.flush()
        db.session.add(OperatorClaim(operator_id=op.id, event_id=ev.id,
                                     status="approved"))
        p = PartnerPayment(operator_id=op.id, event_id=ev.id, plan="jaar",
                           amount=5.0, status="paid", mollie_id="tr_t")
        db.session.add(p); db.session.commit()
        return op.id, p.id


def test_terugkeer_token_herstelt_sessie(client, app):
    op_id, pid = _betaling(app)
    from app import mollie
    with app.test_request_context():
        tok = mollie._terugkeer_token(db.session.get(PartnerPayment, pid))
    r = client.get(f"/uitbater/partner/klaar/{pid}?t={tok}")
    assert r.status_code == 302
    assert "/uitbater/login" not in (r.headers.get("Location") or "")
    # sessie is hersteld: dashboard is nu bereikbaar
    assert client.get(r.headers["Location"]).status_code == 200


def test_terugkeer_zonder_token_naar_login(client, app):
    _, pid = _betaling(app)
    r = client.get(f"/uitbater/partner/klaar/{pid}")
    assert r.status_code == 302 and "/uitbater/login" in r.headers["Location"]


def test_vervalst_token_werkt_niet(client, app):
    _, pid = _betaling(app)
    r = client.get(f"/uitbater/partner/klaar/{pid}?t=vervalst.token")
    assert "/uitbater/login" in r.headers["Location"]
