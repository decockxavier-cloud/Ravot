"""Odoo-facturatie: Peppol-conforme facturen via de boekhouding, nooit eigen PDF's."""
from datetime import datetime
from app.extensions import db
from app.models import Event, Operator, OperatorClaim, PartnerPayment, Setting
from app import odoo, mollie


def _betaalde_betaling(app, met_facturatie=True):
    with app.app_context():
        ev = Event(source="osm", ext_id="node/f1", slug="factuur-zaak",
                   title="Speelcafé Factuur", is_permanent=True, gemeente="Gent",
                   postcode="9000", lat=51.0, lng=3.7, age_min=0, age_max=12,
                   categories=["binnen"])
        op = Operator(email="info@factuur.be",
                      bedrijfsnaam="Factuur BV" if met_facturatie else None,
                      btw_nummer="BE0123456789" if met_facturatie else None,
                      straat="Markt 1", postcode="9000", gemeente="Gent")
        db.session.add_all([ev, op]); db.session.commit()
        p = PartnerPayment(operator_id=op.id, event_id=ev.id, plan="maand",
                           amount="22.99", mollie_id="tr_fact", status="paid",
                           paid_at=datetime.utcnow())
        db.session.add(p); db.session.commit()
        return p.id, op.id, ev.id


class _OdooFake:
    """Nep-Odoo die JSON-RPC-payloads beantwoordt en alles bijhoudt."""
    def __init__(self, bestaande_klant=None):
        self.calls = []
        self.bestaande_klant = bestaande_klant
        self.geboekt = []
    def __call__(self, url, json=None, timeout=None):
        self.calls.append(json)
        p = json["params"]
        class R:
            status_code = 200
            def raise_for_status(self): pass
            def json(inner): return {"result": self._antwoord(p)}
        R._antwoord = lambda inner, pp=p: self._antwoord(pp)
        r = R(); r.json = lambda pp=p: {"result": self._antwoord(pp)}
        return r
    def _antwoord(self, p):
        if p.get("service") == "common":
            return 7                      # uid
        model, methode = p["args"][3], p["args"][4]
        if model == "res.partner" and methode == "search":
            return [self.bestaande_klant] if self.bestaande_klant else []
        if model == "res.partner" and methode == "create":
            return 501                    # nieuwe klant
        if model == "account.move" and methode == "create":
            return 9001                   # factuur-id
        if model == "account.move" and methode == "action_post":
            self.geboekt.append(p["args"][5][0][0])
            return True
        if model == "account.move" and methode == "read":
            return [{"id": 9001, "name": "INV/2026/0042"}]
        return None


def _odoo_aan(app):
    app.config.update(ODOO_URL="https://odoo.test", ODOO_DB="ravot",
                      ODOO_USER="bot@cluma.com", ODOO_API_KEY="key")


def test_factuur_concept_standaard(app):
    """Standaard (odoo_factuur_auto=0): factuur als CONCEPT, klant aangemaakt op btw."""
    pid, _, _ = _betaalde_betaling(app)
    _odoo_aan(app)
    fake = _OdooFake(bestaande_klant=None)
    with app.app_context():
        p = db.session.get(PartnerPayment, pid)
        assert odoo.factureer_betaling(p, http_post=fake) is True
        p = db.session.get(PartnerPayment, pid)
        assert p.odoo_invoice_id == 9001 and p.odoo_invoice_ref == "CONCEPT"
    assert fake.geboekt == []                          # NIET gevalideerd (concept)
    create_klant = [c for c in fake.calls
                    if c["params"].get("args", [None]*5)[3:5] == ["res.partner", "create"]]
    assert create_klant and create_klant[0]["params"]["args"][5][0]["vat"] == "BE0123456789"


def test_factuur_auto_valideert(app):
    pid, _, _ = _betaalde_betaling(app)
    _odoo_aan(app)
    with app.app_context():
        db.session.merge(Setting(key="odoo_factuur_auto", value="1")); db.session.commit()
        fake = _OdooFake(bestaande_klant=314)          # klant bestaat al (op btw)
        p = db.session.get(PartnerPayment, pid)
        odoo.factureer_betaling(p, http_post=fake)
        p = db.session.get(PartnerPayment, pid)
        assert p.odoo_invoice_ref == "INV/2026/0042"
    assert fake.geboekt == [9001]                      # action_post gebeurd
    # geen nieuwe klant aangemaakt
    assert not any(c["params"].get("args", [None]*5)[3:5] == ["res.partner", "create"]
                   for c in fake.calls)


def test_facturatie_idempotent(app):
    pid, _, _ = _betaalde_betaling(app)
    _odoo_aan(app)
    fake = _OdooFake()
    with app.app_context():
        p = db.session.get(PartnerPayment, pid)
        odoo.factureer_betaling(p, http_post=fake)
        n = len(fake.calls)
        assert odoo.factureer_betaling(p, http_post=fake) is False   # tweede keer: nee
        assert len(fake.calls) == n                                   # geen extra calls


def test_odoo_fout_breekt_activatie_niet(app):
    """Faalt Odoo, dan blijft de betaling gewoon 'paid' (activatie sneuvelt niet)."""
    pid, _, _ = _betaalde_betaling(app)
    _odoo_aan(app)
    def kapot(url, json=None, timeout=None):
        raise RuntimeError("odoo down")
    with app.app_context():
        p = db.session.get(PartnerPayment, pid)
        assert odoo.factureer_betaling(p, http_post=kapot) is False
        p = db.session.get(PartnerPayment, pid)
        assert p.status == "paid" and p.odoo_invoice_id is None


def test_partner_kopen_vereist_facturatiegegevens(client, app):
    """Zonder bedrijfsnaam+btw -> POST op partner leidt naar het facturatieformulier."""
    _, oid, eid = _betaalde_betaling(app, met_facturatie=False)
    with app.app_context():
        db.session.add(OperatorClaim(operator_id=oid, event_id=eid, status="approved"))
        db.session.commit()
    with client.session_transaction() as s:
        s["operator_id"] = oid
    r = client.post(f"/uitbater/partner/{eid}", data={"plan": "maand", "csrf_token": "x"})
    assert r.status_code in (302, 303) and "/uitbater/facturatie" in r.headers["Location"]


def test_btw_validatie(client, app):
    _, oid, _ = _betaalde_betaling(app, met_facturatie=False)
    with client.session_transaction() as s:
        s["operator_id"] = oid
    client.post("/uitbater/facturatie", data={"bedrijfsnaam": "Zaak BV",
                "btw_nummer": "FOUT123", "csrf_token": "x"})
    with app.app_context():
        assert db.session.get(Operator, oid).btw_nummer is None     # geweigerd
    client.post("/uitbater/facturatie", data={"bedrijfsnaam": "Zaak BV",
                "btw_nummer": "be 0123.456.789", "csrf_token": "x"})
    with app.app_context():
        assert db.session.get(Operator, oid).btw_nummer == "BE0123456789"  # genormaliseerd


def test_prijs_incl_btw(app):
    with app.app_context():
        assert mollie.prijs_incl("maand") == "22.99"    # 19.00 * 1.21
        assert mollie.prijs_incl("jaar") == "229.90"    # 190.00 * 1.21
