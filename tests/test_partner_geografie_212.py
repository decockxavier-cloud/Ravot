"""Patch 212: partners worden alleen uitgelicht als ze in de buurt liggen,
de zoekstraal komt uit het profiel, en het label is 'Partner'."""
from datetime import timedelta

from app.extensions import db
from app.models import Event, utcnow


def _zaken(app):
    with app.app_context():
        tot = utcnow().replace(tzinfo=None) + timedelta(days=200)
        db.session.add(Event(title="Torhout Frituur", slug="pg-1",
                             source="user", ext_id="pg-1", is_permanent=True,
                             pending=False, hidden=False, gemeente="Torhout",
                             lat=51.0686, lng=3.1006, subtype="horeca",
                             indoor=True, quality=70, partner_until=tot))
        db.session.add(Event(title="Brugge Pannenkoeken", slug="pg-2",
                             source="user", ext_id="pg-2", is_permanent=True,
                             pending=False, hidden=False, gemeente="Brugge",
                             lat=51.2089, lng=3.2242, subtype="horeca",
                             indoor=True, quality=70))
        db.session.add(Event(title="Genk Partner", slug="pg-3", source="user",
                             ext_id="pg-3", is_permanent=True, pending=False,
                             hidden=False, gemeente="Genk", lat=50.9650,
                             lng=5.5006, subtype="horeca", indoor=True,
                             quality=70, partner_until=tot))
        db.session.commit()


def test_zoeken_op_plaats_licht_alleen_lokale_partner_uit(client, app):
    _zaken(app)
    h = client.get("/ontdek?zoek=Brugge&groep=smullen").get_data(as_text=True)
    blok = h.split("partner-uitgelicht")[1][:900] \
        if "partner-uitgelicht" in h else ""
    assert "Torhout Frituur" not in blok        # buurgemeente ≠ gezocht
    assert "Brugge Pannenkoeken" in h           # lokale zaak wél


def test_zonder_locatie_hoogstens_enkele_partners(client, app):
    """Zonder bekende locatie blijven partners uitgelicht (dat is de betaalde
    belofte), maar geplafonneerd en met een uitnodiging om de postcode in te
    vullen — zodat de kop niet volloopt bij honderden partners."""
    _zaken(app)
    h = client.get("/ontdek").get_data(as_text=True)
    assert "partner-uitgelicht" in h
    assert "Vul je postcode in" in h
    from app.routes.public import PARTNER_MAX_ZONDER_LOCATIE
    assert h.count("partner-uitgelicht-sub") <= PARTNER_MAX_ZONDER_LOCATIE


def test_label_zegt_partner(client, app):
    _zaken(app)
    h = client.get("/ontdek").get_data(as_text=True)
    assert "Betalende partner" not in h
