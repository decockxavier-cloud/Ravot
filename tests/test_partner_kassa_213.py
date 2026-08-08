"""Patch 213: de kassa rekent de gekozen formule af.

Het oude, statische prijsblok onderaan toonde altijd de Partner-prijs én
stuurde een verborgen plan=jaar mee — koos een uitbater Feest of Combi, dan
kon er verkeerd afgerekend worden.
"""
from app.extensions import db
from app.models import Event, Operator, OperatorClaim, Setting


def _opzet(app, client):
    with app.app_context():
        for k, v in (("partner_prijs_jaar", "200.00"),
                     ("feest_prijs_jaar", "250.00"),
                     ("combi_prijs_jaar", "360.00"),
                     ("feestjes_aan", "1")):
            db.session.add(Setting(key=k, value=v))
        op = Operator(email="kassa@t.be", bedrijfsnaam="BV Test",
                      btw_nummer="BE0123456789", active=True)
        ev = Event(title="Zaak", slug="ks-1", source="user", ext_id="ks-1",
                   is_permanent=True, pending=False, hidden=False, lat=51.0,
                   lng=3.5, subtype="horeca", indoor=True,
                   gemeente="Roeselare")
        db.session.add_all([op, ev])
        db.session.flush()
        db.session.add(OperatorClaim(operator_id=op.id, event_id=ev.id,
                                     status="approved"))
        db.session.commit()
        oid, eid = op.id, ev.id
    with client.session_transaction() as s:
        s["operator_id"] = oid
    return eid


def test_geen_verborgen_standaardplan_meer(client, app):
    eid = _opzet(app, client)
    h = client.get(f"/uitbater/partner/{eid}").get_data(as_text=True)
    assert 'name="plan" value="jaar"' not in h      # legacy hidden weg
    assert 'data-prijs="250.00"' in h               # elke formule zijn prijs
    assert 'data-prijs="360.00"' in h
    assert 'id="partner-samenvatting"' in h         # samenvatting volgt keuze
    assert "partner_kassa.js" in h                  # extern script (CSP)


def test_zonder_keuze_geen_stille_afrekening(client, app):
    eid = _opzet(app, client)
    r = client.post(f"/uitbater/partner/{eid}", data={}, follow_redirects=True)
    assert "Kies eerst een formule" in r.get_data(as_text=True)
