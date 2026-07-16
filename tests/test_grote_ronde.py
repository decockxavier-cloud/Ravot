"""Tests grote ronde 2026-07: daguitstappen, feestjes, ravotpas,
ouder-filters, weerbericht en de Ravotscore × Partner-afspraak."""
from datetime import datetime, timedelta

from app.extensions import db
from app.models import (DagUitstap, DagUitstapItem, Event, Feestje,
                        FeestjeAanvraag, RavotPunt, Review, SavedEvent)
from tests.conftest import login_as


# ---------------------------------------------------------------- daguitstap --

def test_daguitstap_aanmaken_en_vullen(client, seed):
    login_as(client, seed["fam_a"])
    r = client.post("/mijn/daguitstap/nieuw", data={"titel": "Zaterdag aan zee"},
                    follow_redirects=True)
    assert r.status_code == 200
    d = DagUitstap.query.filter_by(family_id=seed["fam_a"].id).first()
    assert d and d.titel == "Zaterdag aan zee"
    ev = seed["events"][0]
    client.post(f"/mijn/daguitstap/{d.id}/voeg/{ev.id}")
    assert len(d.items) == 1
    # dubbel toevoegen = geen tweede item
    client.post(f"/mijn/daguitstap/{d.id}/voeg/{ev.id}")
    assert len(DagUitstapItem.query.filter_by(daguitstap_id=d.id).all()) == 1


def test_daguitstap_volgorde_en_verwijderen(client, seed):
    login_as(client, seed["fam_a"])
    client.post("/mijn/daguitstap/nieuw", data={"titel": "Plan"})
    d = DagUitstap.query.first()
    for ev in seed["events"][:2]:
        client.post(f"/mijn/daguitstap/{d.id}/voeg/{ev.id}")
    eerste, tweede = sorted(d.items, key=lambda i: i.volgorde)
    client.post(f"/mijn/daguitstap/{d.id}/item/{tweede.id}/op")
    db.session.refresh(eerste); db.session.refresh(tweede)
    assert tweede.volgorde < eerste.volgorde
    client.post(f"/mijn/daguitstap/{d.id}/item/{eerste.id}/weg")
    assert len(DagUitstapItem.query.filter_by(daguitstap_id=d.id).all()) == 1


def test_daguitstap_delen_publiek(client, seed):
    login_as(client, seed["fam_a"])
    client.post("/mijn/daguitstap/nieuw",
                data={"titel": "Deelplan", "event_id": seed["events"][0].id})
    d = DagUitstap.query.first()
    # zonder token: geen publieke pagina
    assert client.get("/d/nietbestaand").status_code == 404
    client.post(f"/mijn/daguitstap/{d.id}/deel")
    assert d.share_token
    uit = client.get(f"/d/{d.share_token}")
    assert uit.status_code == 200
    assert "Deelplan" in uit.get_data(as_text=True)
    # ander gezin kan de daguitstap zelf niet openen/bewerken
    login_as(client, seed["fam_b"])
    assert client.get(f"/mijn/daguitstap/{d.id}").status_code == 404
    # delen weer uit
    login_as(client, seed["fam_a"])
    client.post(f"/mijn/daguitstap/{d.id}/deel")
    assert d.share_token is None


# ------------------------------------------------------------------ feestjes --

def _maak_feestpartner(gemeente="Roeselare", postcode="8800", lat=50.946,
                       lng=3.123, partner=True, contact="zaak@test.be"):
    ev = Event(slug=f"feestzaak-{postcode}-{datetime.utcnow().timestamp()}",
               title="Feestzaak Vosje", gemeente=gemeente, postcode=postcode,
               lat=lat, lng=lng, is_permanent=True, subtype="horeca",
               feest=True, feest_soorten=["horeca", "zaal"],
               feest_contact=contact, age_min=0, age_max=99)
    if partner:
        ev.partner_until = datetime.utcnow() + timedelta(days=30)
    db.session.add(ev)
    db.session.commit()
    return ev


def test_feestje_wizard_en_offerte(client, seed, app):
    partner = _maak_feestpartner()
    login_as(client, seed["fam_a"])
    datum = (datetime.utcnow() + timedelta(days=30)).date()
    r = client.post("/mijn/feestje/nieuw", data={
        "datum": datum.isoformat(), "aantal": "10", "postcode": "8800",
        "kind": str(seed["fam_a"].id and db.session.query(
            __import__("app.models", fromlist=["Child"]).Child.id
        ).filter_by(family_id=seed["fam_a"].id).first()[0]),
        "wensen": "thema dino's",
    }, follow_redirects=False)
    assert r.status_code == 302
    f = Feestje.query.first()
    assert f is not None and f.aantal_kinderen == 10
    # leeftijd automatisch uit geboortejaar van het gekozen kind
    assert f.leeftijd is not None and 3 <= f.leeftijd <= 8
    # stap 2: partner staat in de lijst en offerte vertrekt (console-mail in test)
    r = client.get(f"/mijn/feestje/{f.id}/partners")
    assert "Feestzaak Vosje" in r.get_data(as_text=True)
    r = client.post(f"/mijn/feestje/{f.id}/partners",
                    data={"partner": str(partner.id)}, follow_redirects=True)
    assert r.status_code == 200
    assert FeestjeAanvraag.query.filter_by(feestje_id=f.id,
                                           event_id=partner.id).count() == 1
    # dubbel versturen naar dezelfde partner kan niet
    client.post(f"/mijn/feestje/{f.id}/partners", data={"partner": str(partner.id)})
    assert FeestjeAanvraag.query.filter_by(feestje_id=f.id).count() == 1


def test_feestpartner_zonder_contact_onzichtbaar(client, seed):
    _maak_feestpartner(contact=None)
    login_as(client, seed["fam_a"])
    datum = (datetime.utcnow() + timedelta(days=10)).date()
    client.post("/mijn/feestje/nieuw",
                data={"datum": datum.isoformat(), "postcode": "8800"})
    f = Feestje.query.first()
    r = client.get(f"/mijn/feestje/{f.id}/partners")
    assert "Feestzaak Vosje" not in r.get_data(as_text=True)


def test_feestje_status_bijwerken(client, seed):
    partner = _maak_feestpartner()
    login_as(client, seed["fam_a"])
    datum = (datetime.utcnow() + timedelta(days=5)).date()
    client.post("/mijn/feestje/nieuw",
                data={"datum": datum.isoformat(), "postcode": "8800"})
    f = Feestje.query.first()
    client.post(f"/mijn/feestje/{f.id}/partners", data={"partner": str(partner.id)})
    a = FeestjeAanvraag.query.first()
    client.post(f"/mijn/feestje/{f.id}/aanvraag/{a.id}", data={"status": "bevestigd"})
    assert a.status == "bevestigd" and f.status == "geregeld"


def test_feestje_niet_van_ander_gezin(client, seed):
    login_as(client, seed["fam_a"])
    datum = (datetime.utcnow() + timedelta(days=5)).date()
    client.post("/mijn/feestje/nieuw",
                data={"datum": datum.isoformat(), "postcode": "8800"})
    f = Feestje.query.first()
    login_as(client, seed["fam_b"])
    assert client.get(f"/mijn/feestje/{f.id}").status_code == 404


# ------------------------------------------------------------------ ravotpas --

def test_punten_geweest_en_review_idempotent(client, seed):
    from app import punten as pas
    fam, ev = seed["fam_a"], seed["events"][0]
    login_as(client, fam)
    client.post(f"/mijn/geweest/{ev.id}", data={"antwoord": "ja"})
    assert pas.totaal(fam.id) == 5
    # tweede keer bevestigen = geen extra punten
    client.post(f"/mijn/geweest/{ev.id}", data={"antwoord": "ja"})
    assert pas.totaal(fam.id) == 5
    client.post(f"/mijn/review/{ev.id}", data={"kid_score": "5", "parent_score": "3"})
    assert pas.totaal(fam.id) == 15
    assert RavotPunt.query.filter_by(family_id=fam.id).count() == 2


def test_ravotpas_pagina_en_badges(client, seed):
    fam = seed["fam_a"]
    login_as(client, fam)
    for ev in seed["events"][:2]:
        client.post(f"/mijn/geweest/{ev.id}", data={"antwoord": "ja"})
    r = client.get("/mijn/ravotpas")
    tekst = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "Ravotpas" in tekst and "Stempelkaart" in tekst
    assert "plekken verzameld" in tekst


def test_punten_bij_foto_goedkeuring(client, seed, app):
    """Foto goedkeuren geeft punten; állereerste foto van een plek geeft bonus."""
    from app.models import Photo, Admin
    from app import punten as pas
    fam, ev = seed["fam_a"], seed["events"][0]
    assert not ev.image_url
    p = Photo(event_id=ev.id, family_id=fam.id, filename="t.jpg", status="pending")
    db.session.add(p)
    admin = Admin(email="admin@test.be", pw_hash="x", totp_secret="s",
                  totp_confirmed=True, role="admin")
    db.session.add(admin)
    db.session.commit()
    with client.session_transaction() as s:
        s["admin_id"] = admin.id
        s["admin_2fa_ok"] = True
    client.post(f"/beheer/foto/{p.id}/goedkeuren")
    assert p.status == "approved"
    assert pas.totaal(fam.id) == 15 + 10   # foto + eerste_foto


# ------------------------------------------------- ouder-filters & community --

def test_ouder_filter_op_ontdek(client, seed):
    ev = seed["events"][0]
    ev.omheind = True
    db.session.commit()
    r = client.get("/ontdek?ouder=omheind&wanneer=alle")
    tekst = r.get_data(as_text=True)
    assert "Kinderboerderij" in tekst
    assert "Tienerlab" not in tekst


def test_review_tags_zetten_velden_aan(client, seed):
    """Vanaf 'tag_drempel' reviews met dezelfde praktische tag gaat het veld aan."""
    ev = seed["events"][0]
    fam_a, fam_b = seed["fam_a"], seed["fam_b"]
    # eerste review (bestaande review van ander gezin) met de tag
    db.session.add(Review(family_id=fam_b.id, event_id=ev.id, kid_score=4,
                          parent_score=3, tags=["omheind terrein"]))
    db.session.add(SavedEvent(family_id=fam_a.id, event_id=ev.id, geweest=True))
    db.session.commit()
    login_as(client, fam_a)
    client.post(f"/mijn/review/{ev.id}", data={
        "kid_score": "5", "parent_score": "3", "tag": "omheind terrein"})
    assert ev.omheind is True


# ------------------------------------------- Ravotscore × Partner (2c en 3c) --

def _horeca(partner):
    ev = Event(slug=f"resto-{partner}-{datetime.utcnow().timestamp()}",
               title="Kinderresto", gemeente="Roeselare", postcode="8800",
               lat=50.946, lng=3.123, is_permanent=True, subtype="horeca",
               age_min=0, age_max=99)
    if partner:
        ev.partner_until = datetime.utcnow() + timedelta(days=10)
    db.session.add(ev)
    db.session.flush()
    db.session.add(Review(family_id=None, event_id=ev.id, kid_score=5,
                          parent_score=3, child_ages=[5]))
    db.session.commit()
    return ev


def test_score_zichtbaar_afspraak(app, seed):
    from app.routes.public import score_zichtbaar
    met = _horeca(partner=True)
    zonder = _horeca(partner=False)
    speeltuin = seed["events"][0]           # niet-commercieel
    assert score_zichtbaar(met) is True
    assert score_zichtbaar(zonder) is False
    assert score_zichtbaar(speeltuin) is True


def test_commercieel_factor(app, seed):
    from app.routes.public import commercieel_factor
    met = _horeca(partner=True)
    zonder = _horeca(partner=False)
    assert commercieel_factor(met) > 1.0
    assert commercieel_factor(zonder) < 1.0
    assert commercieel_factor(seed["events"][0]) == 1.0


def test_fiche_verbergt_score_zonder_partner(client, seed):
    zonder = _horeca(partner=False)
    r = client.get(f"/e/{zonder.slug}")
    tekst = r.get_data(as_text=True)
    assert "Ravotscore 5" not in tekst          # community-score niet getoond
    met = _horeca(partner=True)
    r = client.get(f"/e/{met.slug}")
    assert "Ravotscore" in r.get_data(as_text=True)


def test_agg_fallback_zonder_reeks(client, seed):
    """Permanente plek zonder reeks toont haar eigen reviews (bugfix)."""
    ev = Event(slug="speeltuin-los", title="Speeltuin Los", gemeente="Roeselare",
               postcode="8800", lat=50.946, lng=3.123, is_permanent=True,
               subtype="playground", age_min=0, age_max=12)
    db.session.add(ev)
    db.session.flush()
    db.session.add(Review(family_id=None, event_id=ev.id, kid_score=5,
                          parent_score=3, child_ages=[4]))
    db.session.commit()
    r = client.get("/e/speeltuin-los")
    assert "Ravotscore" in r.get_data(as_text=True)


# -------------------------------------------------------------- weerbericht --

def test_weerbericht_helper(app, seed, monkeypatch):
    from app.routes.public import weerbericht
    import app.weer as weer

    def nep_voorspelling(lat, lng, dag=None):
        return {"emoji": "🌧️", "label": "regen", "tmax": 14.2, "tmin": 8.0,
                "regenkans": 80, "datum": dag}
    monkeypatch.setattr(weer, "voorspelling", nep_voorspelling)
    v = weerbericht("vandaag", seed["fam_a"])
    assert v and v["plaats"] == "Roeselare" and v["regenkans"] == 80
    assert v["dag_label"] == "Vandaag"
    # met expliciet centrum (gezochte activiteitenplaats)
    v2 = weerbericht("vandaag", None, centrum=(51.05, 3.72), plaats="Gent")
    assert v2 and v2["plaats"] == "Gent"


def test_weerbericht_uit_te_schakelen(app, seed, monkeypatch):
    from app.routes.public import weerbericht
    from app.models import Setting
    db.session.add(Setting(key="weer_aan", value="0"))
    db.session.commit()
    assert weerbericht("vandaag", seed["fam_a"]) is None


# ------------------------------------------------------ geboortejaar-invoer --

def test_onboarding_met_geboortejaar(client, app):
    from app.models import Child, Family
    with client.session_transaction() as s:
        s["pending_email"] = "nieuw@test.be"
    jaar = datetime.utcnow().year
    r = client.post("/mijn/start", data={
        "birth_year": [str(jaar - 4), str(jaar - 9)],
        "postcode": "8800", "radius": "25",
    }, follow_redirects=False)
    assert r.status_code == 302
    fam = Family.query.filter_by(email="nieuw@test.be").first()
    jaren = sorted(c.birth_year for c in Child.query.filter_by(family_id=fam.id))
    assert jaren == [jaar - 9, jaar - 4]


def test_instellingen_geboortejaar_en_legacy_age(client, seed):
    """Nieuw veld werkt; oude 'age'-invoer (gecachte PWA) blijft ook werken."""
    from app.models import Child
    fam = seed["fam_a"]
    login_as(client, fam)
    jaar = datetime.utcnow().year
    client.post("/mijn/instellingen", data={
        "birth_year": str(jaar - 3), "age": "10",
        "postcode": "8800", "radius": "25", "budget": "all",
    })
    jaren = sorted(c.birth_year for c in Child.query.filter_by(family_id=fam.id))
    assert jaren == [jaar - 10, jaar - 3]


def test_geboortejaar_validatie(client, seed):
    """Onzinnige jaartallen (te oud, toekomst) worden genegeerd."""
    from app.models import Child
    fam = seed["fam_a"]
    login_as(client, fam)
    jaar = datetime.utcnow().year
    client.post("/mijn/instellingen", data={
        "birth_year": [str(jaar - 40), str(jaar + 1), str(jaar - 5)],
        "postcode": "8800", "radius": "25", "budget": "all",
    })
    jaren = [c.birth_year for c in Child.query.filter_by(family_id=fam.id)]
    assert jaren == [jaar - 5]
