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
    _voorbij(ev)
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
                          parent_score=3, tags=["afgesloten speelterrein"]))
    db.session.add(SavedEvent(family_id=fam_a.id, event_id=ev.id, geweest=True))
    db.session.commit()
    login_as(client, fam_a)
    client.post(f"/mijn/review/{ev.id}", data={
        "kid_score": "5", "parent_score": "3", "tag": "afgesloten speelterrein"})
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


# ------------------------------------------------------- OSM-horeca & nakijk --

def _osm_el(tags, oid=1):
    return {"type": "node", "id": oid, "lat": 50.9, "lon": 3.1, "tags": tags}


def test_osm_horeca_normalise(app):
    from app.services.sources import osm
    # mét naam + kind-signaal → fiche met subtype horeca en ouder-filters
    d = osm.normalise(_osm_el({
        "amenity": "restaurant", "name": "De Speelvogel",
        "kids_area": "yes", "changing_table": "yes", "wheelchair": "yes",
        "addr:city": "Roeselare", "addr:postcode": "8800"}))
    assert d and d["subtype"] == "horeca" and d["title"] == "De Speelvogel"
    assert d["verzorgingstafel"] is True and d["buggy_ok"] is True
    assert "speelhoek" in d["description"]
    # zonder kind-signaal → geen fiche (poort tegen horeca-rommel)
    assert osm.normalise(_osm_el({"amenity": "restaurant",
                                  "name": "Frituur X"}, 2)) is None
    # zonder naam → geen fiche
    assert osm.normalise(_osm_el({"amenity": "cafe",
                                  "highchair": "yes"}, 3)) is None


def test_upsert_overschrijft_communityvelden_niet(app, seed):
    """Een hersync mag verzorgingstafel/foto van community of admin niet wissen."""
    from app.services.sources.base import upsert_event
    data = {"source": "osm", "ext_id": "node/9", "title": "De Speelvogel",
            "is_permanent": True, "gemeente": "Roeselare", "postcode": "8800",
            "lat": 50.9, "lng": 3.1, "age_min": 0, "age_max": 12,
            "categories": [], "subtype": "horeca", "indoor": True,
            "is_free": False, "price_info": [], "image_url": None,
            "verzorgingstafel": True,
            "venue_ext_id": "node/9", "venue_name": "De Speelvogel"}
    ev = upsert_event(dict(data))
    db.session.commit()
    assert ev.verzorgingstafel is True
    # community/admin zet extra velden + foto
    ev.omheind = True
    ev.image_url = "/foto/123"
    db.session.commit()
    # hersync zonder die info → blijft staan
    data2 = dict(data)
    data2.pop("verzorgingstafel")
    ev2 = upsert_event(data2)
    db.session.commit()
    assert ev2.id == ev.id
    assert ev2.omheind is True and ev2.verzorgingstafel is True
    assert ev2.image_url == "/foto/123"


def test_nakijk_banner_flow(client, seed):
    fam = seed["fam_a"]
    fam.gegevens_nagekeken = False
    db.session.commit()
    login_as(client, fam)
    r = client.get("/mijn/profiel")
    assert "geboortejaren" in r.get_data(as_text=True)
    # 'Klopt al' → banner weg
    client.post("/mijn/gegevens-ok")
    assert fam.gegevens_nagekeken is True
    r = client.get("/mijn/profiel")
    assert "Even nakijken" not in r.get_data(as_text=True)


def test_nakijk_via_instellingen_opslaan(client, seed):
    fam = seed["fam_b"]
    fam.gegevens_nagekeken = False
    db.session.commit()
    login_as(client, fam)
    jaar = datetime.utcnow().year
    client.post("/mijn/instellingen", data={
        "birth_year": str(jaar - 8), "postcode": "9000",
        "radius": "25", "budget": "all"})
    assert fam.gegevens_nagekeken is True


# -------------------------------------------------------------- foto-limieten --

def test_upload_verkleint_grote_foto(app, tmp_path):
    """Server-side veiligheidsnet: grote foto's worden verkleind + heringcodeerd."""
    import io
    from PIL import Image
    from app.fotos import verwerk_upload, pad_van
    from werkzeug.datastructures import FileStorage
    app.config["UPLOAD_DIR"] = str(tmp_path)
    buf = io.BytesIO()
    Image.new("RGB", (4000, 3000), "orange").save(buf, format="JPEG")
    buf.seek(0)
    naam = verwerk_upload(FileStorage(buf, filename="groot.jpg",
                                      content_type="image/jpeg"))
    assert naam
    img = Image.open(pad_van(naam))
    assert max(img.size) <= 1600            # verkleind
    assert img.format == "JPEG"


def test_upload_weigert_geen_afbeelding(app, tmp_path):
    import io
    from app.fotos import verwerk_upload
    from werkzeug.datastructures import FileStorage
    app.config["UPLOAD_DIR"] = str(tmp_path)
    nep = FileStorage(io.BytesIO(b"<script>alert(1)</script>"),
                      filename="foto.jpg", content_type="image/jpeg")
    assert verwerk_upload(nep) is None


def test_413_geeft_vriendelijke_melding(client, seed, app):
    app.config["MAX_CONTENT_LENGTH"] = 1024   # 1 KB voor de test
    login_as(client, seed["fam_a"])
    ev = seed["events"][0]
    import io
    r = client.post(f"/mijn/foto/{ev.id}",
                    data={"akkoord": "1",
                          "foto": (io.BytesIO(b"x" * 5000), "groot.jpg")},
                    content_type="multipart/form-data", follow_redirects=False)
    assert r.status_code == 302               # nette redirect, geen kale 413


# ------------------------------------------ ontdek-zijbalk, kaart & feestjes --

def test_ontdek_zijbalk_en_filterteller(client, seed):
    ev = seed["events"][0]
    ev.omheind = True
    db.session.commit()
    html = client.get("/ontdek?wanneer=alle&ouder=omheind&filter=gratis") \
        .get_data(as_text=True)
    assert "ontdek-zij" in html          # zijbalk aanwezig
    assert "filter-teller" in html       # actieve-filters-badge
    assert "wis alles (2)" in html       # gratis + omheind = 2 actief


def test_kaart_toont_horeca(client, seed):
    resto = _maak_feestpartner()         # subtype horeca met coördinaten
    html = client.get("/verkennen").get_data(as_text=True)
    assert "Feestzaak Vosje" in html


def test_publieke_feestjespagina(client, seed):
    html = client.get("/feestjes").get_data(as_text=True)
    assert "Verjaardagsfeestje" in html and "offerte" in html
    # uitgelogd: CTA loopt via login met next naar de wizard
    assert "next=/mijn/feestje/nieuw" in html


def test_login_next_landt_in_wizard(client, seed):
    from app.services import magic
    fam = seed["fam_a"]
    # 1. bezoek login met next
    client.get("/login?next=/mijn/feestje/nieuw")
    # 2. code aanvragen + verifiëren
    with client.application.app_context():
        code = magic.issue_code(fam.email)
    r = client.post("/code", data={"email": fam.email, "code": code},
                    follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/mijn/feestje/nieuw")


def test_login_next_weigert_externe_url(client, seed):
    from app.services import magic
    fam = seed["fam_b"]
    client.get("/login?next=https://kwaadaardig.be/phish")
    with client.application.app_context():
        code = magic.issue_code(fam.email)
    r = client.post("/code", data={"email": fam.email, "code": code},
                    follow_redirects=False)
    assert "kwaadaardig" not in (r.headers.get("Location") or "")


# ------------------------------------- lijst = kaart (zelfde filters) & dash --

def test_kaart_heeft_zelfde_filters_als_lijst(client, seed):
    """Ouder- en soortfilters werken nu ook op de kaart."""
    ev = seed["events"][0]
    ev.omheind = True
    db.session.commit()
    # zonder filter: beide events op de kaart
    html = client.get("/verkennen?wanneer=alle").get_data(as_text=True)
    assert "Kinderboerderij" in html and "Tienerlab" in html
    # met ouder-filter: enkel de omheinde plek
    html = client.get("/verkennen?wanneer=alle&ouder=omheind").get_data(as_text=True)
    assert "Kinderboerderij" in html and "Tienerlab" not in html


def test_weergave_switch_bewaart_filters(client, seed):
    html = client.get("/ontdek?wanneer=alle&filter=gratis&ouder=omheind") \
        .get_data(as_text=True)
    assert "weergave-switch" in html
    # de kaart-link neemt filter én ouder mee
    assert "/verkennen?" in html
    import re
    kaartlink = re.search(r'href="(/verkennen\?[^"]*)"', html).group(1)
    assert "filter=gratis" in kaartlink and "ouder=omheind" in kaartlink
    # en omgekeerd: kaart → lijst
    html = client.get("/verkennen?wanneer=alle&filter=gratis").get_data(as_text=True)
    lijstlink = re.search(r'href="(/ontdek\?[^"]*)"', html).group(1)
    assert "filter=gratis" in lijstlink


def test_dashboard_secties(client, seed):
    login_as(client, seed["fam_a"])
    html = client.get("/mijn/profiel").get_data(as_text=True)
    assert "Jullie uitstappen" in html
    assert "Verjaardagsfeestje" in html
    assert "Jullie gezin" in html
    assert "Plan een feestje" in html          # nog geen feestje open


def test_osm_horeca_standaard_aan(app):
    """Horeca-import staat aan zonder handmatige osm_tags-aanpassing."""
    from app.models import get_bool
    assert get_bool("osm_horeca_aan") is True


def test_ontdek_lege_soort_geeft_hulp(client, seed):
    html = client.get("/ontdek?wanneer=alle&soort=water_park").get_data(as_text=True)
    assert "Voeg ze toe" in html


def test_fiche_heeft_foto_actie(client, seed):
    login_as(client, seed["fam_a"])
    html = client.get(f"/e/{seed['events'][0].slug}").get_data(as_text=True)
    assert "data-open-foto" in html and 'id="foto-blok"' in html


# --------------------------------------- leeftijdsfilter, uitleg & migratie --

def test_leeftijdsfilter_op_lijst_en_kaart(client, seed):
    """Kinderboerderij (0-12) wél, Tienerlab (12-16) niet bij 4-6 jaar.
    Op slug getest: de typenamen staan ook in de soort-dropdown."""
    html = client.get("/ontdek?wanneer=alle&lft=4-6").get_data(as_text=True)
    assert "kinderboerderij-roeselare" in html and "tienerlab-roeselare" not in html
    html = client.get("/ontdek?wanneer=alle&lft=13-17").get_data(as_text=True)
    assert "tienerlab-roeselare" in html and "kinderboerderij-roeselare" not in html
    # zelfde filter op de kaart
    html = client.get("/verkennen?wanneer=alle&lft=4-6").get_data(as_text=True)
    assert "kinderboerderij-roeselare" in html and "tienerlab-roeselare" not in html


def test_score_uitlegpagina(client, seed):
    html = client.get("/ravotscore").get_data(as_text=True)
    assert "Ravotscore" in html and "Vossenkoning" in html and "+15" in html


def test_migratie_hernoemt_oude_tags(app, seed):
    from app.models import OUDE_TAGS
    ev = seed["events"][0]
    db.session.add(Review(family_id=seed["fam_b"].id, event_id=ev.id,
                          kid_score=4, parent_score=3,
                          tags=["vlot met buggy", "mooie natuur"]))
    db.session.commit()
    rv = Review.query.filter(Review.family_id == seed["fam_b"].id).first()
    rv.tags = [OUDE_TAGS.get(t, t) for t in (rv.tags or [])]
    db.session.commit()
    assert "vlot met de wandelwagen" in rv.tags and "mooie natuur" in rv.tags


# ------------------------------------------------------- horeca-verkenner --

def _nep_overpass(elementen):
    def _run(query):
        return iter(elementen)
    return _run


def test_verkenner_en_import(app, seed, monkeypatch):
    from app.services.sources import osm as ob
    elementen = [
        {"type": "node", "id": 1, "lat": 50.95, "lon": 3.12,
         "tags": {"amenity": "restaurant", "name": "Pannenkoekenhuis Vosje",
                  "highchair": "yes", "addr:city": "Roeselare"}},
        {"type": "node", "id": 2, "lat": 50.94, "lon": 3.11,
         "tags": {"amenity": "bar", "name": "Zomerbar De Vos",
                  "seasonal": "summer"}},
        {"type": "node", "id": 3, "lat": 50.93, "lon": 3.10,
         "tags": {"amenity": "bar"}},          # naamloos -> genegeerd
    ]
    monkeypatch.setattr(ob, "_run", _nep_overpass(elementen))
    rows = ob.verken_horeca(50.95, 3.12, 5)
    assert len(rows) == 2
    assert rows[0]["naam"] == "Pannenkoekenhuis Vosje"     # signalen eerst
    assert rows[1]["zomerbar"] is True
    # import: beide zaken, met soortkeuze
    n = ob.importeer_horeca([("node/1", "horeca"), ("node/2", "zomerbar")])
    db.session.commit()
    assert n == 2
    from app.models import Event
    zb = Event.query.filter_by(ext_id="node/2").first()
    assert zb.subtype == "zomerbar" and zb.curated is True
    assert zb.indoor is False
    # gemeente-fallback via dichtstbijzijnde postcode (seed heeft 8800)
    assert zb.gemeente == "Roeselare"
    resto = Event.query.filter_by(ext_id="node/1").first()
    assert resto.subtype == "horeca" and resto.buggy_ok is not True


def test_horeca_import_scherm(client, seed, app):
    from app.models import Admin
    admin = Admin(email="a2@t.be", pw_hash="x", totp_secret="s",
                  totp_confirmed=True, role="admin")
    db.session.add(admin); db.session.commit()
    with client.session_transaction() as s:
        s["admin_id"] = admin.id; s["admin_2fa_ok"] = True
    r = client.get("/beheer/horeca-import")
    assert r.status_code == 200
    assert "OpenStreetMap" in r.get_data(as_text=True)


# -------------------------------------------------------- overture-verkenner --

def test_overture_zoek_en_import(app, seed):
    from app.models import HorecaKandidaat, Event
    from app.services.sources import overture as ov
    db.session.add_all([
        HorecaKandidaat(ext_id="ov1", naam="Zomerbar Zonneke",
                        categorie="beer_garden", lat=50.95, lng=3.12,
                        zomerbar_hint=True),
        HorecaKandidaat(ext_id="ov2", naam="Brasserie 't Vosje",
                        categorie="restaurant", gemeente="Roeselare",
                        postcode="8800", lat=50.94, lng=3.13),
        HorecaKandidaat(ext_id="ver", naam="Te ver", categorie="cafe",
                        lat=51.5, lng=4.5),
    ])
    db.session.commit()
    rows = ov.zoek_kandidaten(50.95, 3.12, 5)
    assert [r["ext_id"] for r in rows] == ["ov1", "ov2"]
    assert rows[0]["zomerbar"] is True
    n = ov.importeer([("ov1", "zomerbar"), ("ov2", "horeca")])
    db.session.commit()
    assert n == 2
    zb = Event.query.filter_by(source="overture", ext_id="ov1").first()
    assert zb.subtype == "zomerbar" and zb.curated is True
    assert zb.gemeente == "Roeselare"      # fallback via postcode-centroid
    assert Event.query.filter_by(ext_id="ov2").first().subtype == "horeca"


def test_overture_categorie_en_zomerbar_heuristiek(app):
    from app.services.sources.overture import _is_horeca, lijkt_zomerbar
    assert _is_horeca("belgian_restaurant", []) is True
    assert _is_horeca("hair_salon", ["barber"]) is False
    assert lijkt_zomerbar("Strandbar Bredene", "bar") is True
    assert lijkt_zomerbar("Café Central", "cafe") is False
    assert lijkt_zomerbar("De Tuin", "beer_garden") is True


# --------------------------------------------------------------- beloningen --

def _voorbij(*events):
    """Zet events in het verleden: 'geweest' kan pas na de start (tijdslogica)."""
    from datetime import datetime, timedelta
    for ev in events:
        ev.start = datetime.utcnow() - timedelta(hours=5)
        ev.end = datetime.utcnow() - timedelta(hours=2)
    db.session.commit()


def _oud_genoeg(fam):
    """Zet het gezinsaccount ouder dan de wisseldrempel (anti-misbruik)."""
    from datetime import datetime, timedelta
    fam.created_at = datetime.utcnow() - timedelta(days=30)
    db.session.commit()


def _beloning(pt=30, voorraad=None):
    from app.models import Beloning
    b = Beloning(emoji="🦊", naam="Stickervel", punten=pt, waarde_eur=1.5,
                 voorraad=voorraad)
    db.session.add(b)
    db.session.commit()
    return b


def test_wisselen_verlaagt_saldo_niet_niveau(client, seed):
    from app import punten as pas
    from app.models import Inwissel
    fam = seed["fam_a"]
    _oud_genoeg(fam)
    _voorbij(*seed["events"][:2])
    login_as(client, fam)
    # verdien 30 punten (2 bezoeken + 2 reviews)
    for ev in seed["events"][:2]:
        client.post(f"/mijn/geweest/{ev.id}", data={"antwoord": "ja"})
        client.post(f"/mijn/review/{ev.id}", data={"kid_score": "5", "parent_score": "3"})
    assert pas.totaal(fam.id) == 30 and pas.saldo(fam.id) == 30
    b = _beloning(pt=30)
    r = client.post(f"/mijn/beloningen/{b.id}/wissel",
                    data={"adres": "Fam. Test, Teststraat 1, 8800 Roeselare"},
                    follow_redirects=True)
    assert "RAVOT-" in r.get_data(as_text=True)
    assert pas.saldo(fam.id) == 0
    assert pas.totaal(fam.id) == 30            # niveau blijft
    assert Inwissel.query.filter_by(family_id=fam.id).count() == 1
    # tweede wissel: onvoldoende saldo
    client.post(f"/mijn/beloningen/{b.id}/wissel")
    assert Inwissel.query.filter_by(family_id=fam.id).count() == 1


def test_wisselen_respecteert_voorraad(client, seed):
    from app.models import Inwissel
    fam = seed["fam_a"]
    _oud_genoeg(fam)
    login_as(client, fam)
    client.post(f"/mijn/geweest/{seed['events'][0].id}", data={"antwoord": "ja"})
    client.post(f"/mijn/review/{seed['events'][0].id}",
                data={"kid_score": "5", "parent_score": "3"})
    b = _beloning(pt=5, voorraad=0)
    client.post(f"/mijn/beloningen/{b.id}/wissel")
    assert Inwissel.query.count() == 0          # op = op


def test_annulatie_geeft_voorraad_en_punten_terug(client, seed, app):
    from app import punten as pas
    from app.models import Admin, Inwissel
    fam = seed["fam_a"]
    _oud_genoeg(fam)
    _voorbij(seed["events"][0])
    login_as(client, fam)
    client.post(f"/mijn/geweest/{seed['events'][0].id}", data={"antwoord": "ja"})
    client.post(f"/mijn/review/{seed['events'][0].id}",
                data={"kid_score": "4", "parent_score": "3"})
    b = _beloning(pt=15, voorraad=3)
    client.post(f"/mijn/beloningen/{b.id}/wissel",
                data={"adres": "Fam. Test, Teststraat 1, 8800 Roeselare"})
    assert b.voorraad == 2 and pas.saldo(fam.id) == 0
    admin = Admin(email="a3@t.be", pw_hash="x", totp_secret="s",
                  totp_confirmed=True, role="admin")
    db.session.add(admin); db.session.commit()
    with client.session_transaction() as s:
        s["admin_id"] = admin.id; s["admin_2fa_ok"] = True
    i = Inwissel.query.first()
    client.post("/beheer/beloningen", data={"actie": "status", "iid": i.id,
                                            "status": "geannuleerd"})
    assert b.voorraad == 3 and pas.saldo(fam.id) == 15


# ------------------------------------------------------------- anti-misbruik --

def test_geweest_kan_niet_voor_de_start(client, seed):
    from app import punten as pas
    fam, ev = seed["fam_a"], seed["events"][0]   # start in de toekomst
    login_as(client, fam)
    client.post(f"/mijn/geweest/{ev.id}", data={"antwoord": "ja"})
    sv = SavedEvent.query.filter_by(family_id=fam.id, event_id=ev.id).first()
    assert not (sv and sv.geweest)
    assert pas.totaal(fam.id) == 0


def test_dagplafond_bezoeken(client, seed, app):
    """Vanaf het 4e bezoek op één dag: geen punten meer (actie werkt wel)."""
    from datetime import datetime, timedelta
    from app import punten as pas
    fam = seed["fam_a"]
    login_as(client, fam)
    for i in range(5):
        ev = Event(slug=f"farm-{i}", title=f"Farm {i}", gemeente="Roeselare",
                   postcode="8800", lat=50.9, lng=3.1,
                   start=datetime.utcnow() - timedelta(hours=6),
                   end=datetime.utcnow() - timedelta(hours=3),
                   age_min=0, age_max=12, categories=[])
        db.session.add(ev)
        db.session.commit()
        client.post(f"/mijn/geweest/{ev.id}", data={"antwoord": "ja"})
    assert pas.totaal(fam.id) == 15              # 3 x 5, daarna plafond


def test_dagplafond_totaal(app, seed):
    from app import punten as pas
    fam = seed["fam_a"]
    # 50 punten verdiend vandaag -> een foto van 15 gaat over het plafond (60)
    for i in range(5):
        pas.ken_toe(fam.id, "review", 1000 + i)
    db.session.commit()
    assert pas.totaal(fam.id) == 50
    assert pas.ken_toe(fam.id, "foto", 2000) == 0
    # kleine actie die er nog onder past, kan wel
    assert pas.ken_toe(fam.id, "daguitstap", 3000) == 5


def test_wissel_vereist_ouder_account(client, seed):
    from app.models import Inwissel
    fam = seed["fam_a"]                          # net aangemaakt
    login_as(client, fam)
    b = _beloning(pt=1)
    r = client.post(f"/mijn/beloningen/{b.id}/wissel", follow_redirects=True)
    assert "dagen oud" in r.get_data(as_text=True)
    assert Inwissel.query.count() == 0


# ------------------------------------------------------------ puntenverval --

def test_saldo_vervalt_niveau_niet(app, seed):
    """Punten ouder dan de termijn tellen niet meer als saldo, wél voor het
    niveau. Uitgaven verbruiken de oudste punten eerst."""
    from datetime import datetime, timedelta
    from app import punten as pas
    from app.models import RavotPunt, Setting
    fam = seed["fam_a"]
    oud = datetime.utcnow() - timedelta(days=200)      # > 6 maanden
    vers = datetime.utcnow() - timedelta(days=10)
    db.session.add_all([
        RavotPunt(family_id=fam.id, reden="review", ref_id=1, punten=40,
                  created_at=oud),
        RavotPunt(family_id=fam.id, reden="review", ref_id=2, punten=30,
                  created_at=vers),
    ])
    db.session.commit()
    assert pas.totaal(fam.id) == 70                    # niveau op alles
    assert pas.saldo(fam.id) == 30                     # oude 40 vervallen
    # geen verval? dan telt alles
    db.session.add(Setting(key="punten_geldig_maanden", value="0"))
    db.session.commit()
    assert pas.saldo(fam.id) == 70


def test_uitgaven_verbruiken_oudste_eerst(app, seed):
    from datetime import datetime, timedelta
    from app import punten as pas
    from app.models import Beloning, Inwissel, RavotPunt
    fam = seed["fam_b"]
    oud = datetime.utcnow() - timedelta(days=200)
    vers = datetime.utcnow() - timedelta(days=5)
    db.session.add_all([
        RavotPunt(family_id=fam.id, reden="review", ref_id=1, punten=40,
                  created_at=oud),
        RavotPunt(family_id=fam.id, reden="review", ref_id=2, punten=30,
                  created_at=vers),
    ])
    b = Beloning(naam="Sticker", punten=40, waarde_eur=2)
    db.session.add(b)
    db.session.flush()
    # gaf destijds 40 uit -> dat waren de oude punten; vandaag rest 30
    db.session.add(Inwissel(family_id=fam.id, beloning_id=b.id, punten=40,
                            code="RAVOT-TEST01"))
    db.session.commit()
    assert pas.saldo(fam.id) == 30                     # niets dubbel vervallen


def test_verval_waarschuwing(app, seed):
    from datetime import datetime, timedelta
    from app import punten as pas
    from app.models import RavotPunt
    fam = seed["fam_a"]
    bijna_oud = datetime.utcnow() - timedelta(days=170)   # vervalt over ±10 d
    db.session.add(RavotPunt(family_id=fam.id, reden="review", ref_id=9,
                             punten=25, created_at=bijna_oud))
    db.session.commit()
    assert pas.vervalt_binnenkort(fam.id, dagen=30) == 25
    assert pas.vervalt_binnenkort(fam.id, dagen=5) == 0


# ---------------------------------------------- beloningsvoorwaarden-migratie --

def test_voorwaarden_paragraaf_seed_en_append(app):
    """Nieuwe installs krijgen de paragraaf via de seed; bestaande (bewerkte)
    voorwaardenpagina's krijgen ze via migrate-db aangevuld — zonder de eigen
    tekst te overschrijven, en idempotent."""
    from app.content_teksten import BELONING_VOORWAARDEN
    from app.models import ContentPage
    # bestaande pagina met eigen tekst, zoals op de live site
    vw = ContentPage.query.filter_by(slug="voorwaarden").first()
    if vw is None:
        vw = ContentPage(slug="voorwaarden", titel="Gebruiksvoorwaarden")
        db.session.add(vw)
    vw.inhoud_md = ("## Eigen voorwaarden\n\nEigen intro van Xavier.\n\n"
                    "### Wijzigingen\n\nWe passen soms iets aan.")
    db.session.commit()
    # zelfde logica als in migrate-db
    def _append():
        p = ContentPage.query.filter_by(slug="voorwaarden").first()
        if p and p.inhoud_md and "Ravotpunten en beloningen" not in p.inhoud_md:
            if "### Wijzigingen" in p.inhoud_md:
                p.inhoud_md = p.inhoud_md.replace(
                    "### Wijzigingen", BELONING_VOORWAARDEN + "\n### Wijzigingen", 1)
            else:
                p.inhoud_md = p.inhoud_md.rstrip() + "\n\n" + BELONING_VOORWAARDEN
            db.session.commit()
            return True
        return False
    assert _append() is True
    tekst = ContentPage.query.filter_by(slug="voorwaarden").first().inhoud_md
    assert "Eigen intro van Xavier." in tekst          # niets overschreven
    assert "geen kansspel" in tekst and "YAMY BV" in tekst
    assert tekst.index("Ravotpunten en beloningen") < tekst.index("### Wijzigingen")
    assert _append() is False                          # idempotent


def test_ai_triage_bewaart_advies(app, seed, monkeypatch):
    from app.models import HorecaKandidaat
    from app.services.sources import overture as ov
    db.session.add_all([
        HorecaKandidaat(ext_id="t1", naam="IJssalon Vosje", categorie="ice_cream_shop",
                        lat=50.95, lng=3.12),
        HorecaKandidaat(ext_id="t2", naam="Nachtcafé De Uil", categorie="bar",
                        lat=50.95, lng=3.12),
        HorecaKandidaat(ext_id="t3", naam="Al beoordeeld", categorie="cafe",
                        lat=50.95, lng=3.12, ai_advies="ja"),
    ])
    db.session.commit()
    import app.enrich as enrich
    aanroepen = []
    def nep_generate(prompt, system, max_tokens=500):
        aanroepen.append(prompt)
        return ('{"beoordelingen": [{"id": "t1", "advies": "ja"}, '
                '{"id": "t2", "advies": "nee"}]}')
    monkeypatch.setattr(enrich, "_generate", nep_generate)
    ks = ov.kandidaten_in_gebied(50.95, 3.12, 5)
    n = ov.ai_triage(ks)
    assert n == 2 and len(aanroepen) == 1        # t3 werd overgeslagen
    assert HorecaKandidaat.query.filter_by(ext_id="t1").first().ai_advies == "ja"
    assert HorecaKandidaat.query.filter_by(ext_id="t2").first().ai_advies == "nee"
    # zoekresultaten: 'ja' bovenaan, 'nee' onderaan
    rows = ov.zoek_kandidaten(50.95, 3.12, 5)
    assert rows[0]["ai"] == "ja" and rows[-1]["ai"] == "nee"


def test_ai_triage_achtergrond_start(app, seed, monkeypatch):
    from app.models import HorecaKandidaat
    from app.services.sources import overture as ov
    db.session.add(HorecaKandidaat(ext_id="bg1", naam="Pannenkoekenhuis",
                                   categorie="restaurant", lat=50.95, lng=3.12))
    db.session.commit()
    import app.enrich as enrich
    monkeypatch.setattr(enrich, "_generate", lambda p, s, max_tokens=500:
                        '{"beoordelingen": [{"id": "bg1", "advies": "ja"}]}')
    ok = ov.start_ai_triage_achtergrond(app, 50.95, 3.12, 5, synchroon=True)
    assert ok is True
    assert ov.triage_actief() is False           # netjes afgerond
    assert HorecaKandidaat.query.filter_by(ext_id="bg1").first().ai_advies == "ja"


# ------------------------------------------------- winterbar & communiefeest --

def test_winterbar_heuristiek_en_import(app, seed):
    from app.models import Event, HorecaKandidaat
    from app.services.sources import overture as ov
    assert ov.lijkt_winterbar("Winterbar 't Chalet") is True
    assert ov.lijkt_winterbar("Zomerbar Zon") is False
    db.session.add(HorecaKandidaat(ext_id="wb1", naam="Après-Ski Bar De Piste",
                                   categorie="bar", lat=50.95, lng=3.12,
                                   winterbar_hint=True))
    db.session.commit()
    rows = ov.zoek_kandidaten(50.95, 3.12, 5)
    assert rows[0]["winterbar"] is True
    ov.importeer([("wb1", "winterbar")])
    db.session.commit()
    ev = Event.query.filter_by(ext_id="wb1").first()
    assert ev.subtype == "winterbar" and ev.indoor is True


def test_seizoensfilter(client, seed):
    """Zomerbar enkel apr-sep, winterbar enkel okt-mrt — tenzij je er
    expliciet op filtert."""
    from datetime import date
    from app.types import in_seizoen
    zomer = in_seizoen("zomerbar")
    winter = in_seizoen("winterbar")
    assert zomer != winter                       # nooit allebei tegelijk
    assert in_seizoen("zomerbar", maand=7) and not in_seizoen("zomerbar", maand=1)
    assert in_seizoen("winterbar", maand=12) and not in_seizoen("winterbar", maand=7)
    assert in_seizoen("playground", maand=1)     # gewone types altijd
    # in de lijst: het type buiten seizoen verschijnt niet...
    uit_seizoen = "winterbar" if zomer else "zomerbar"
    ev = Event(slug="seizoen-test", title="Seizoensbar Test", gemeente="Roeselare",
               postcode="8800", lat=50.9, lng=3.1, is_permanent=True,
               subtype=uit_seizoen, age_min=0, age_max=12, categories=[],
               curated=True, quality=90)
    db.session.add(ev)
    db.session.commit()
    html = client.get("/ontdek?wanneer=alle").get_data(as_text=True)
    assert "Seizoensbar Test" not in html
    # ...maar wél als je er bewust op filtert
    html = client.get(f"/ontdek?wanneer=alle&soort={uit_seizoen}").get_data(as_text=True)
    assert "Seizoensbar Test" in html


def test_feestje_met_aanleiding_communie(client, seed, monkeypatch):
    from app.models import Feestje
    fam = seed["fam_a"]
    login_as(client, fam)
    from datetime import date, timedelta
    datum = (date.today() + timedelta(days=60)).isoformat()
    r = client.post("/mijn/feestje/nieuw", data={
        "aanleiding": "communie", "datum": datum, "leeftijd": "7",
        "aantal": "12", "postcode": "8800", "straal": "20"},
        follow_redirects=False)
    assert r.status_code == 302
    f = Feestje.query.filter_by(family_id=fam.id).first()
    assert f.aanleiding == "communie" and f.leeftijd == 7


def test_offertemail_vermeldt_aanleiding(app, seed):
    from app.services import feestjes as fs
    from app.models import Feestje
    from datetime import date, timedelta
    fam = seed["fam_a"]
    f = Feestje(family_id=fam.id, aanleiding="lentefeest", leeftijd=12,
                datum=date.today() + timedelta(days=30), aantal_kinderen=15,
                postcode="8800", gemeente="Roeselare")
    db.session.add(f)
    partner = _maak_feestpartner()
    db.session.commit()
    verstuurd = {}
    def nep_send(naar, onderwerp, html, text=None, headers=None):
        verstuurd.update(onderwerp=onderwerp, text=text)
        return True
    import app.services.feestjes as module
    orig = module.send_mail
    module.send_mail = nep_send
    try:
        with app.test_request_context():
            ok = fs.stuur_offerte(f, partner, fam)
    finally:
        module.send_mail = orig
    assert ok
    assert "lentefeest" in (verstuurd["onderwerp"] + verstuurd["text"]).lower()


def test_horeca_import_ai_knop(client, seed, app, monkeypatch):
    """De AI-knop (POST) zelf — de GET-test miste de current_app-import."""
    from app.models import Admin, HorecaKandidaat
    db.session.add(HorecaKandidaat(ext_id="knop1", naam="Testcafé",
                                   categorie="cafe", lat=50.94, lng=3.13))
    admin = Admin(email="ai@t.be", pw_hash="x", totp_secret="s",
                  totp_confirmed=True, role="admin")
    db.session.add(admin); db.session.commit()
    with client.session_transaction() as s:
        s["admin_id"] = admin.id; s["admin_2fa_ok"] = True
    import app.enrich as enrich
    monkeypatch.setattr(enrich, "_generate", lambda p, s:
                        '{"beoordelingen": [{"id": "knop1", "advies": "ja"}]}')
    r = client.post("/beheer/horeca-import", data={
        "actie": "ai", "bron": "overture", "plaats": "8800", "straal": "5"})
    assert r.status_code == 200
    assert "achtergrond" in r.get_data(as_text=True)


# ------------------------------------------------ status & partneropvolging --

def _admin_login(client):
    from app.models import Admin
    admin = Admin(email="st@t.be", pw_hash="x", totp_secret="s",
                  totp_confirmed=True, role="admin")
    db.session.add(admin); db.session.commit()
    with client.session_transaction() as s:
        s["admin_id"] = admin.id; s["admin_2fa_ok"] = True
    return admin


def test_status_dashboard(client, seed, monkeypatch):
    """Alle checks renderen; externe pings gemockt (ok én kapot)."""
    import app.services.health as health
    monkeypatch.setattr(health.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("weg")))
    _admin_login(client)
    r = client.get("/beheer/status")
    h = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "Database (PostgreSQL)" in h and "Mollie" in h and "AI-verrijking" in h
    assert "probleem" in h                     # kapotte pings netjes getoond
    assert "laatste synchronisatie" in h


def test_partners_lidmaatschappen_en_nafacturatie(client, seed, monkeypatch):
    from datetime import datetime, timedelta
    from app.models import Operator, PartnerPayment
    ev = seed["events"][0]
    ev.partner_until = datetime.utcnow() + timedelta(days=10)   # verloopt snel
    op = Operator(email="zaak@t.be", bedrijfsnaam="Testzaak BV", btw_nummer="BE0123456789")
    db.session.add(op); db.session.flush()
    b = PartnerPayment(operator_id=op.id, event_id=ev.id, plan="jaar",
                       amount="190.00", status="paid",
                       paid_at=datetime.utcnow())
    db.session.add(b); db.session.commit()
    _admin_login(client)
    h = client.get("/beheer/partners").get_data(as_text=True)
    assert "Testzaak BV" in h and "verloopt binnen 14 d" in h
    assert "€190" in h                       # omzet dit jaar
    # nafacturatie: odoo gemockt
    import app.odoo as odoo
    monkeypatch.setattr(odoo, "actief", lambda: True)
    def nep_factureer(p, http_post=None):
        p.odoo_invoice_id, p.odoo_invoice_ref = 7, "INV/2026/0007"
    monkeypatch.setattr(odoo, "factureer_betaling", nep_factureer)
    r = client.post(f"/beheer/partners/factuur/{b.id}", follow_redirects=True)
    assert "INV/2026/0007" in r.get_data(as_text=True)
    assert b.odoo_invoice_ref == "INV/2026/0007"


# ------------------------------------------- feedback-ronde: crud & bronnen --

def test_beloning_bewerken_en_verwijderen(client, seed):
    from app.models import Admin, Beloning, Inwissel
    b = Beloning(naam="Petje", punten=100, waarde_eur=5)
    db.session.add(b)
    admin = Admin(email="crud@t.be", pw_hash="x", totp_secret="s",
                  totp_confirmed=True, role="admin")
    db.session.add(admin); db.session.commit()
    with client.session_transaction() as s:
        s["admin_id"] = admin.id; s["admin_2fa_ok"] = True
    # bewerken
    client.post("/beheer/beloningen", data={
        "actie": "bewerk", "bid": b.id, "naam": "Ravot-petje", "emoji": "🧢",
        "waarde": "6", "punten": "120", "voorraad": "10", "beschrijving": ""})
    assert b.naam == "Ravot-petje" and b.punten == 120 and b.voorraad == 10
    # verwijderen kan zolang er geen inwisselingen zijn
    client.post("/beheer/beloningen", data={"actie": "verwijder", "bid": b.id})
    assert db.session.get(Beloning, b.id) is None
    # met inwisseling: verwijderen geweigerd
    b2 = Beloning(naam="Sjaal", punten=50, waarde_eur=3)
    db.session.add(b2); db.session.flush()
    db.session.add(Inwissel(family_id=seed["fam_a"].id, beloning_id=b2.id,
                            punten=50, code="RAVOT-CRUD01"))
    db.session.commit()
    r = client.post("/beheer/beloningen", data={"actie": "verwijder", "bid": b2.id},
                    follow_redirects=True)
    assert db.session.get(Beloning, b2.id) is not None
    assert "inwisselingen" in r.get_data(as_text=True)


def test_bron_data_wissen(client, seed):
    from app.models import Admin, Review
    ev_uit = Event(slug="wis-uit", title="UiT Testevent", source="uit",
                   gemeente="Gent", postcode="9000", lat=51.0, lng=3.7,
                   age_min=0, age_max=12, categories=[])
    ev_cur = Event(slug="wis-cur", title="Gecureerd", source="uit", curated=True,
                   gemeente="Gent", postcode="9000", lat=51.0, lng=3.7,
                   age_min=0, age_max=12, categories=[])
    db.session.add_all([ev_uit, ev_cur])
    admin = Admin(email="wis@t.be", pw_hash="x", totp_secret="s",
                  totp_confirmed=True, role="admin")
    db.session.add(admin); db.session.commit()
    uit_id = ev_uit.id
    db.session.add(Review(family_id=seed["fam_b"].id, event_id=uit_id,
                          kid_score=3, parent_score=3))
    db.session.commit()
    with client.session_transaction() as s:
        s["admin_id"] = admin.id; s["admin_2fa_ok"] = True
    client.post("/beheer/verbindingen/wis-bron",
                data={"bron": "uit", "behoud_gecureerd": "1"})
    assert Event.query.filter_by(slug="wis-uit").first() is None
    assert Event.query.filter_by(slug="wis-cur").first() is not None  # behouden
    assert Review.query.filter_by(event_id=uit_id).count() == 0       # mee gewist


def test_registry_beperkt_tot_kernbronnen(app):
    from app.services.sources import REGISTRY
    assert set(REGISTRY) == {"uit", "osm"}


# ------------------------------------------- filtermatrix + fiche-verdieping --

def _matrix_events():
    from datetime import datetime, timedelta
    nu = datetime.utcnow()
    evs = [
        Event(slug="m-gratis-nat", title="GratisNatuur", gemeente="Roeselare",
              postcode="8800", lat=50.95, lng=3.12, start=nu + timedelta(hours=1),
              end=nu + timedelta(hours=3), is_free=True, indoor=False,
              age_min=4, age_max=8, categories=["natuur"]),
        Event(slug="m-binnen-cult", title="BinnenCultuur", gemeente="Roeselare",
              postcode="8800", lat=50.94, lng=3.11, start=nu + timedelta(hours=1),
              end=nu + timedelta(hours=3), is_free=False, indoor=True,
              age_min=10, age_max=14, categories=["cultuur"]),
        Event(slug="m-resto", title="RestoKind", gemeente="Roeselare",
              postcode="8800", lat=50.93, lng=3.10, is_permanent=True,
              subtype="horeca", curated=True, quality=80, indoor=True,
              kinderstoel=True, kindermenu=True, speelhoek=False,
              age_min=0, age_max=12, categories=[]),
    ]
    db.session.add_all(evs)
    db.session.commit()
    return evs


def test_filtermatrix_lijst_en_kaart(client, seed):
    _matrix_events()
    T = lambda url: client.get(url).get_data(as_text=True)
    # elke filter apart
    h = T("/ontdek?wanneer=alle&filter=gratis")
    assert "GratisNatuur" in h and "BinnenCultuur" not in h
    h = T("/ontdek?wanneer=alle&filter=binnen")
    assert "BinnenCultuur" in h and "RestoKind" in h and "GratisNatuur" not in h
    h = T("/ontdek?wanneer=alle&cat=natuur")
    assert "GratisNatuur" in h and "BinnenCultuur" not in h
    h = T("/ontdek?wanneer=alle&lft=10-12")
    assert "BinnenCultuur" in h and "GratisNatuur" not in h
    h = T("/ontdek?wanneer=alle&soort=horeca")
    assert "RestoKind" in h and "GratisNatuur" not in h
    h = T("/ontdek?wanneer=alle&ouder=kinderstoel&ouder=kindermenu")
    assert "RestoKind" in h and "BinnenCultuur" not in h
    h = T("/ontdek?wanneer=alle&ouder=speelhoek")
    assert "RestoKind" not in h                     # heeft géén speelhoek
    # combinaties
    h = T("/ontdek?wanneer=alle&filter=gratis&cat=natuur&lft=4-6")
    assert "GratisNatuur" in h and "BinnenCultuur" not in h and "RestoKind" not in h
    h = T("/ontdek?wanneer=alle&filter=binnen&lft=10-12&cat=cultuur")
    assert "BinnenCultuur" in h and "RestoKind" not in h
    h = T("/ontdek?wanneer=alle&soort=horeca&ouder=kindermenu&lft=4-6")
    assert "RestoKind" in h
    # zelfde combinaties op de kaart
    h = T("/verkennen?wanneer=alle&filter=gratis&cat=natuur")
    assert "m-gratis-nat" in h and "m-binnen-cult" not in h
    h = T("/verkennen?wanneer=alle&soort=horeca&ouder=kinderstoel")
    assert "m-resto" in h and "m-gratis-nat" not in h
    # zoekterm + filter samen
    h = T("/ontdek?wanneer=alle&q=RestoKind&soort=horeca")
    assert "RestoKind" in h


def test_kindfotos_apart_blok(client, seed, app):
    from app.models import Photo
    ev = _matrix_events()[2]
    db.session.add_all([
        Photo(event_id=ev.id, filename="menu.jpg", soort="kindermenu",
              status="approved"),
        Photo(event_id=ev.id, filename="hoek.jpg", soort="kinderhoek",
              status="approved"),
        Photo(event_id=ev.id, filename="gezin.jpg", soort="gezin",
              status="approved"),
    ])
    db.session.commit()
    h = client.get(f"/e/{ev.slug}").get_data(as_text=True)
    assert "Voor de kinderen" in h and "Kindermenu" in h


def test_uitbater_voorzieningen_en_upload(client, seed, app):
    import io
    from app.models import EditProposal, Event, Operator, OperatorClaim, Photo
    ev = Event.query.filter_by(slug="kinderboerderij-roeselare-1").first()
    op = Operator(email="zaak@test.be")
    db.session.add(op); db.session.flush()
    db.session.add(OperatorClaim(operator_id=op.id, event_id=ev.id,
                                 status="approved"))
    db.session.commit()
    with client.session_transaction() as s:
        s["operator_id"] = op.id
    r = client.post(f"/uitbater/fiche/{ev.id}", data={
        "kinderstoel": "on", "kindermenu": "on"})
    prop = EditProposal.query.filter_by(event_id=ev.id).first()
    assert prop and prop.changes.get("kinderstoel") is True
    # upload kindermenu-foto -> pending (echte mini-PNG, anders faalt Pillow)
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (238, 128, 53)).save(buf, format="PNG")
    buf.seek(0)
    r = client.post(f"/uitbater/fiche/{ev.id}/foto", data={
        "soort": "kindermenu", "foto": (buf, "menu.png")},
        content_type="multipart/form-data", follow_redirects=True)
    p = Photo.query.filter_by(event_id=ev.id, soort="kindermenu").first()
    assert p is not None and p.status == "pending"


def test_feestje_bewerken_en_annuleren(client, seed):
    from datetime import date, timedelta
    from app.models import Feestje, FeestjeAanvraag
    fam = seed["fam_a"]
    login_as(client, fam)
    f = Feestje(family_id=fam.id, datum=date.today() + timedelta(days=40),
                leeftijd=6, aantal_kinderen=10, postcode="8800")
    db.session.add(f); db.session.flush()
    db.session.add(FeestjeAanvraag(feestje_id=f.id,
                                   event_id=seed["events"][0].id))
    db.session.commit()
    client.post(f"/mijn/feestje/{f.id}/bewerk", data={
        "aanleiding": "communie", "datum": (date.today() + timedelta(days=50)).isoformat(),
        "leeftijd": "7", "aantal": "20", "wensen": "glutenvrij"})
    assert f.aanleiding == "communie" and f.aantal_kinderen == 20
    client.post(f"/mijn/feestje/{f.id}/annuleer")
    assert f.status == "geannuleerd"
    # aanvraag blijft bestaan -> partner-evaluatie blijft kloppen
    assert FeestjeAanvraag.query.filter_by(feestje_id=f.id).count() == 1
    # en het feestje staat niet meer in de lijst
    h = client.get("/mijn/feestjes").get_data(as_text=True)
    assert f"/mijn/feestje/{f.id}" not in h


def test_ai_triage_overleeft_kapotte_batch(app, seed, monkeypatch):
    """Eén afgekapt/kapot antwoord mag de rest niet tegenhouden (de
    7-van-357-bug): volgende batches gaan gewoon door."""
    from app.models import HorecaKandidaat
    from app.services.sources import overture as ov
    from app.models import Setting
    db.session.merge(Setting(key="verrijk_backend", value="cloud"))
    db.session.add_all([HorecaKandidaat(ext_id=f"r{i}", naam=f"Zaak {i}",
                                        categorie="cafe", lat=50.9, lng=3.1)
                        for i in range(50)])   # 2 cloud-batches van 25
    db.session.commit()
    import app.enrich as enrich
    beurten = []
    def nep(prompt, system, max_tokens=500):
        beurten.append(max_tokens)
        if len(beurten) == 1:
            return '{"beoordelingen": [{"id": "r0", "advies"'   # afgekapt
        import json as _j, re as _re
        ids = _re.findall(r'"id": "(r[0-9]+)"', prompt)
        return _j.dumps({"beoordelingen": [{"id": i, "advies": "ja"} for i in ids]})
    monkeypatch.setattr(enrich, "_generate", nep)
    n = ov.ai_triage(ov.kandidaten_in_gebied(50.9, 3.1, 5))
    assert len(beurten) == 2                    # doorgegaan na de kapotte batch
    assert beurten[0] >= 2000                   # ruime tokens gevraagd
    assert n == 25                              # tweede batch volledig gelukt


def test_wissel_ravot_vereist_adres_partner_niet(client, seed):
    from app.models import Beloning, Event, Inwissel
    fam = seed["fam_a"]
    _oud_genoeg(fam)
    login_as(client, fam)
    from app import punten as pas
    for i in range(4):
        pas.ken_toe(fam.id, "review", 5000 + i)
    db.session.commit()
    b = _beloning(pt=10)
    # zonder adres: geweigerd
    client.post(f"/mijn/beloningen/{b.id}/wissel")
    assert Inwissel.query.count() == 0
    # partner-goodie: geen adres nodig (code tonen bij de partner)
    zaak = seed["events"][0]
    bp = Beloning(naam="Gratis pannenkoek", punten=10, waarde_eur=3,
                  soort="partner", partner_event_id=zaak.id)
    db.session.add(bp); db.session.commit()
    client.post(f"/mijn/beloningen/{bp.id}/wissel")
    iw = Inwissel.query.first()
    assert iw is not None and iw.bezorg_adres is None


def test_admin_puntencorrectie_en_gezinsdetail(client, seed):
    from app.models import Admin
    from app import punten as pas
    fam = seed["fam_a"]
    pas.ken_toe(fam.id, "review", 7000)
    admin = Admin(email="fd@t.be", pw_hash="x", totp_secret="s",
                  totp_confirmed=True, role="admin")
    db.session.add(admin); db.session.commit()
    with client.session_transaction() as s:
        s["admin_id"] = admin.id; s["admin_2fa_ok"] = True
    client.post(f"/beheer/families/{fam.id}", data={
        "actie": "punten", "aantal": "-5", "reden": "rechtzetting test"})
    assert pas.totaal(fam.id) == 5
    h = client.get(f"/beheer/families/{fam.id}").get_data(as_text=True)
    assert "Ravotpas" in h and "rechtzetting test" in h
    assert "Toegekende scores" in h and "Nieuwsbrief" in h


def test_ai_voortgang_endpoint(client, seed):
    from app.models import Admin, HorecaKandidaat, PostcodeCentroid
    db.session.add_all([
        HorecaKandidaat(ext_id="v1", naam="Zaak 1", categorie="cafe",
                        lat=50.95, lng=3.12, ai_advies="ja"),
        HorecaKandidaat(ext_id="v2", naam="Zaak 2", categorie="cafe",
                        lat=50.95, lng=3.12),
    ])
    admin = Admin(email="vg@t.be", pw_hash="x", totp_secret="s",
                  totp_confirmed=True, role="admin")
    db.session.add(admin); db.session.commit()
    with client.session_transaction() as s:
        s["admin_id"] = admin.id; s["admin_2fa_ok"] = True
    d = client.get("/beheer/horeca-import/ai-voortgang?plaats=8800&straal=5").get_json()
    assert d["totaal"] == 2 and d["klaar"] == 1 and d["bezig"] is False


def test_ai_uitleg_en_gesloten(client, seed, monkeypatch):
    from app.models import Admin, Event, HorecaKandidaat
    from app.services.sources import overture as ov
    db.session.add_all([
        HorecaKandidaat(ext_id="u1", naam="Pannenkoekenparadijs",
                        categorie="restaurant", lat=50.95, lng=3.12),
        HorecaKandidaat(ext_id="dood", naam="Gesloten Zaak", categorie="cafe",
                        lat=50.95, lng=3.12),
    ])
    db.session.commit()
    import app.enrich as enrich
    monkeypatch.setattr(enrich, "_generate", lambda p, s, max_tokens=500:
                        '{"beoordelingen": [{"id": "u1", "advies": "ja", '
                        '"uitleg": "pannenkoeken zijn kindermagneet"}, '
                        '{"id": "dood", "advies": "twijfel", "uitleg": "onbekend"}]}')
    ov.ai_triage(ov.kandidaten_in_gebied(50.95, 3.12, 5))
    k = HorecaKandidaat.query.filter_by(ext_id="u1").first()
    assert k.ai_advies == "ja" and "kindermagneet" in k.ai_uitleg
    # uitleg zichtbaar in de zoekresultaten
    rows = ov.zoek_kandidaten(50.95, 3.12, 5)
    assert any(r.get("ai_uitleg") for r in rows)
    # gesloten markeren: kandidaat weg uit resultaten, fiche verborgen
    ov.importeer([("dood", "horeca")])
    db.session.commit()
    admin = Admin(email="gs@t.be", pw_hash="x", totp_secret="s",
                  totp_confirmed=True, role="admin")
    db.session.add(admin); db.session.commit()
    with client.session_transaction() as s:
        s["admin_id"] = admin.id; s["admin_2fa_ok"] = True
    client.post("/beheer/horeca-import", data={
        "actie": "importeer", "actie_gesloten": "dood", "bron": "overture",
        "plaats": "8800", "straal": "5"})
    assert HorecaKandidaat.query.filter_by(ext_id="dood").first().gesloten is True
    assert Event.query.filter_by(ext_id="dood").first().hidden is True
    assert all(r["ext_id"] != "dood" for r in ov.zoek_kandidaten(50.95, 3.12, 5))
    # 'dood' handmatig + 'u1' automatisch live (AI-ja) = 2 overture-fiches;
    # de gesloten-klik zelf importeert niets extra.
    assert Event.query.filter_by(source="overture").count() == 2


def test_zaken_werkvoorraad(client, seed):
    from app.models import Admin, Event
    ev = Event(slug="wv-1", title="Nieuw Geimporteerd Resto", source="overture",
               gemeente="Roeselare", postcode="8800", lat=50.9, lng=3.1,
               is_permanent=True, subtype="horeca", curated=True, quality=70,
               age_min=0, age_max=12, categories=[])
    db.session.add(ev)
    admin = Admin(email="wv@t.be", pw_hash="x", totp_secret="s",
                  totp_confirmed=True, role="admin")
    db.session.add(admin); db.session.commit()
    with client.session_transaction() as s:
        s["admin_id"] = admin.id; s["admin_2fa_ok"] = True
    # standaardweergave = werkvoorraad, met tellers
    h = client.get("/beheer/activiteiten").get_data(as_text=True)
    assert "Nog na te kijken (1)" in h and "Nieuw Geimporteerd Resto" in h
    # afvinken -> verdwijnt uit de werkvoorraad, verschijnt bij 'klaar'
    client.post(f"/beheer/activiteiten/{ev.id}/nagekeken")
    assert ev.nagekeken is True
    h = client.get("/beheer/activiteiten?status=nakijken").get_data(as_text=True)
    assert "Nieuw Geimporteerd Resto" not in h
    h = client.get("/beheer/activiteiten?status=klaar").get_data(as_text=True)
    assert "Nieuw Geimporteerd Resto" in h


def test_toevoegen_met_kaartpin(client, seed):
    from app.models import Event
    fam = seed["fam_a"]
    login_as(client, fam)
    r = client.post("/mijn/toevoegen", data={
        "titel": "Geprikt Speelbos", "categorie": "natuur",
        "age_min": "2", "age_max": "10",
        "lat": "50.9480", "lng": "3.1210",          # pin, géén adres/postcode
    }, follow_redirects=False)
    assert r.status_code == 302
    ev = Event.query.filter_by(title="Geprikt Speelbos").first()
    assert ev is not None and ev.pending is True
    assert abs(ev.lat - 50.948) < 0.001 and abs(ev.lng - 3.121) < 0.001
    assert ev.gemeente == "Roeselare" and ev.postcode == "8800"  # via centroid
    # ongeldige pin (buiten België) wordt genegeerd
    client.post("/mijn/toevoegen", data={
        "titel": "Fout Gepind", "categorie": "natuur",
        "lat": "48.0", "lng": "2.0", "postcode": "8800"})
    ev2 = Event.query.filter_by(title="Fout Gepind").first()
    assert ev2.lat != 48.0                           # centroid gebruikt


def test_goedgekeurde_gezinsplek_op_de_kaart(client, seed):
    """Toevoegen -> goedkeuren -> zichtbaar op kaart én lijst (de bug van de
    verdwenen gezinsplek: geen quality/curated na goedkeuring)."""
    from app.models import Admin, Event
    fam = seed["fam_a"]
    login_as(client, fam)
    client.post("/mijn/toevoegen", data={
        "titel": "Speelweide De Pin", "categorie": "natuur",
        "age_min": "0", "age_max": "12", "lat": "50.9480", "lng": "3.1210"})
    ev = Event.query.filter_by(title="Speelweide De Pin").first()
    assert ev.pending is True
    admin = Admin(email="np@t.be", pw_hash="x", totp_secret="s",
                  totp_confirmed=True, role="admin")
    db.session.add(admin); db.session.commit()
    with client.session_transaction() as s:
        s["admin_id"] = admin.id; s["admin_2fa_ok"] = True
    client.post(f"/beheer/nazicht/plek/{ev.id}/goedkeuren")
    assert ev.pending is False and ev.curated is True
    assert ev.nagekeken is True and ev.quality is not None
    # zichtbaar op kaart en lijst
    h = client.get("/verkennen?wanneer=alle").get_data(as_text=True)
    assert "Speelweide De Pin" in h
    h = client.get("/ontdek?wanneer=alle").get_data(as_text=True)
    assert "Speelweide De Pin" in h


def test_toevoegen_soort_en_tijdelijk(client, seed):
    """Zomerbar als vaste plek (seizoenslogica) én een tijdelijke activiteit
    met datums — de twee nieuwe smaken van het toevoegformulier."""
    from datetime import date, timedelta
    from app.models import Event
    fam = seed["fam_a"]
    login_as(client, fam)
    # 1. vaste zomerbar
    client.post("/mijn/toevoegen", data={
        "titel": "Zomerbar 't Weitje", "categorie": "buiten",
        "aard": "vast", "soort": "zomerbar",
        "lat": "50.9480", "lng": "3.1210"})
    zb = Event.query.filter_by(title="Zomerbar 't Weitje").first()
    assert zb.is_permanent is True and zb.subtype == "zomerbar"
    assert zb.start is None
    # 2. tijdelijke activiteit met periode
    d1 = date.today() + timedelta(days=10)
    d2 = date.today() + timedelta(days=12)
    client.post("/mijn/toevoegen", data={
        "titel": "Kermis Krottegem", "categorie": "buiten",
        "aard": "tijdelijk", "soort": "rommelmarkt",
        "datum_van": d1.isoformat(), "datum_tot": d2.isoformat(),
        "postcode": "8800"})
    km = Event.query.filter_by(title="Kermis Krottegem").first()
    assert km.is_permanent is False and km.subtype == "rommelmarkt"
    assert km.start.date() == d1 and km.end.date() == d2
    # 3. einddatum in het verleden: geweigerd
    client.post("/mijn/toevoegen", data={
        "titel": "Te Laat Feest", "categorie": "buiten", "aard": "tijdelijk",
        "datum_van": (date.today() - timedelta(days=5)).isoformat(),
        "postcode": "8800"})
    assert Event.query.filter_by(title="Te Laat Feest").first() is None
    # 4. ongeldig soort wordt genegeerd
    client.post("/mijn/toevoegen", data={
        "titel": "Raar Soort", "categorie": "buiten", "aard": "vast",
        "soort": "hacksoort", "postcode": "8800"})
    assert Event.query.filter_by(title="Raar Soort").first().subtype is None


def test_horeca_ai_alles_cli(app, monkeypatch):
    """Bulk-triage + auto-import: de bespaarknop voor de initiële curatie."""
    from app.models import Event, HorecaKandidaat, Setting
    with app.app_context():
        db.session.merge(Setting(key="verrijk_backend", value="cloud"))
        db.session.add_all([
            HorecaKandidaat(ext_id=f"b{i}", naam=f"Bulkzaak {i}",
                            categorie="restaurant", lat=50.9, lng=3.1)
            for i in range(4)])
        db.session.commit()
        import app.enrich as enrich
        import json as _j, re as _re
        def nep(prompt, system, max_tokens=500):
            ids = _re.findall(r'"id": "(b[0-9]+)"', prompt)
            return _j.dumps({"beoordelingen": [
                {"id": i, "advies": "ja" if i != "b3" else "nee",
                 "uitleg": "test"} for i in ids]})
        monkeypatch.setattr(enrich, "_generate", nep)
        runner = app.test_cli_runner()
        uit = runner.invoke(args=["horeca-ai-alles", "--importeer"])
        assert "Klaar." in uit.output
        assert Event.query.filter_by(source="overture").count() == 3  # 3x ja
        ev = Event.query.filter_by(source="overture").first()
        assert ev.curated is True and ev.nagekeken is True
        # herstartbaar: tweede run heeft niets meer te doen
        uit2 = runner.invoke(args=["horeca-ai-alles"])
        assert "Te beoordelen: 0" in uit2.output


def test_bulk_alles_nagekeken(client, seed):
    from app.models import Admin, Event
    for i in range(3):
        db.session.add(Event(slug=f"bulk-{i}", title=f"Bulk {i}",
                             source="overture", gemeente="Gent",
                             postcode="9000", lat=51.0, lng=3.7,
                             is_permanent=True, curated=True, quality=60,
                             age_min=0, age_max=12, categories=[]))
    admin = Admin(email="bk@t.be", pw_hash="x", totp_secret="s",
                  totp_confirmed=True, role="admin")
    db.session.add(admin); db.session.commit()
    with client.session_transaction() as s:
        s["admin_id"] = admin.id; s["admin_2fa_ok"] = True
    client.post("/beheer/activiteiten/alles-nagekeken")
    assert Event.query.filter(Event.curated.is_(True),
                              Event.nagekeken.is_(False)).count() == 0


# ------------------------------------------ datapakket: auto-live + vangnet --

def test_ai_ja_gaat_automatisch_live(app, seed, monkeypatch):
    from app.models import Event, HorecaKandidaat
    from app.services.sources import overture as ov
    db.session.add_all([
        HorecaKandidaat(ext_id="live1", naam="Kindercafe De Speeltuin",
                        categorie="cafe", lat=50.95, lng=3.12),
        HorecaKandidaat(ext_id="nee1", naam="Nachtkroeg", categorie="bar",
                        lat=50.95, lng=3.12),
    ])
    db.session.commit()
    import app.enrich as enrich
    monkeypatch.setattr(enrich, "_generate", lambda p, s, max_tokens=500:
                        '{"beoordelingen": [{"id": "live1", "advies": "ja", '
                        '"uitleg": "speeltuin"}, {"id": "nee1", "advies": "nee", '
                        '"uitleg": "nachtcafe"}]}')
    ov.beoordeel_voorraad(log=lambda *a: None)
    # ja-zaak staat meteen live (curated + nagekeken), nee-zaak niet
    ev = Event.query.filter_by(ext_id="live1").first()
    assert ev is not None and ev.curated is True and ev.nagekeken is True
    assert Event.query.filter_by(ext_id="nee1").first() is None


def test_bulk_nagekeken_en_typefilter(client, seed):
    from app.models import Admin, Event
    db.session.add_all([
        Event(slug="b-play", title="Speeltuin Bulk", source="osm",
              subtype="playground", is_permanent=True, curated=True,
              nagekeken=False, quality=60, gemeente="Gent", postcode="9000",
              lat=51.0, lng=3.7, age_min=0, age_max=12, categories=[]),
        Event(slug="b-hor", title="Horeca Bulk", source="overture",
              subtype="horeca", is_permanent=True, curated=True,
              nagekeken=False, quality=60, gemeente="Gent", postcode="9000",
              lat=51.0, lng=3.7, age_min=0, age_max=12, categories=[]),
    ])
    admin = Admin(email="bulk@t.be", pw_hash="x", totp_secret="s",
                  totp_confirmed=True, role="admin")
    db.session.add(admin); db.session.commit()
    with client.session_transaction() as s:
        s["admin_id"] = admin.id; s["admin_2fa_ok"] = True
    # typefilter: enkel speeltuinen
    h = client.get("/beheer/activiteiten?status=nakijken&soort=_speeltuin").get_data(as_text=True)
    assert "Speeltuin Bulk" in h and "Horeca Bulk" not in h
    # bulk enkel osm-plekken nakijken
    client.post("/beheer/activiteiten/bulk-nagekeken",
                data={"bron": "osm", "soort": ""})
    assert Event.query.filter_by(slug="b-play").first().nagekeken is True
    assert Event.query.filter_by(slug="b-hor").first().nagekeken is False  # overture ongemoeid


def test_overture_verrijkt_contact(app, seed):
    from app.models import Event, HorecaKandidaat
    from app.services.sources import overture as ov
    ev = Event(slug="v-plek", title="Speelpark De Bron", source="osm",
               is_permanent=True, gemeente="Gent", postcode="9000",
               lat=51.0500, lng=3.7000, age_min=0, age_max=12, categories=[])
    db.session.add(ev)
    db.session.add(HorecaKandidaat(
        ext_id="v-k", naam="Speelpark De Bron", categorie="attraction",
        lat=51.0500, lng=3.7000, website="https://debron.be",
        telefoon="09 123 45 67"))
    db.session.commit()
    n = ov.verrijk_contact(log=lambda *a: None)
    assert n == 1
    assert ev.source_url == "https://debron.be" and ev.telefoon == "09 123 45 67"


# --------------------------------------------------- feedback: markt/export/help

def test_avondmarkt_en_rommelmarkt_types(app):
    from app.types import TYPES
    assert TYPES["avondmarkt"][2] is True
    assert TYPES["rommelmarkt"][2] is True


def test_horeca_partner_export(client, seed):
    from app.models import Admin, Event, HorecaKandidaat
    ev = Event(slug="exp-1", title="Café De Vos", source="overture",
               subtype="horeca", is_permanent=True, curated=True,
               gemeente="Roeselare", postcode="8800", lat=50.9, lng=3.1,
               ext_id="exp-k", age_min=0, age_max=12, categories=[])
    db.session.add(ev)
    db.session.add(HorecaKandidaat(ext_id="exp-k", naam="Café De Vos",
                                   categorie="cafe", lat=50.9, lng=3.1,
                                   website="https://devos.be",
                                   telefoon="051 20 30 40", email="info@devos.be"))
    admin = Admin(email="exp@t.be", pw_hash="x", totp_secret="s",
                  totp_confirmed=True, role="admin")
    db.session.add(admin); db.session.commit()
    with client.session_transaction() as s:
        s["admin_id"] = admin.id; s["admin_2fa_ok"] = True
    r = client.get("/beheer/horeca-import/export.csv")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Café De Vos" in body and "info@devos.be" in body
    assert "051 20 30 40" in body and "https://devos.be" in body
    assert r.headers["Content-Type"].startswith("text/csv")


def test_handleidingen_publiek(client):
    for url in ("/help", "/help/partners"):
        r = client.get(url)
        assert r.status_code == 200
    assert "gezinnen" in client.get("/help").get_data(as_text=True).lower()
    assert "partner" in client.get("/help/partners").get_data(as_text=True).lower()


def test_overture_filtert_winkels_en_adresloos(app):
    from app.services.sources.overture import _is_horeca, kuis_kandidaten
    from app.models import HorecaKandidaat, Event
    # categorie-filter
    assert _is_horeca("restaurant") and _is_horeca("cafe")
    assert not _is_horeca("bakery") and not _is_horeca("butcher")
    assert not _is_horeca("food_processing") and not _is_horeca("supermarket")
    # opkuis verwijdert rommel maar spaart gecureerde fiches
    db.session.add_all([
        HorecaKandidaat(ext_id="goed", naam="Resto", categorie="restaurant",
                        adres="Straat 1", lat=50.9, lng=3.1, confidence=0.9),
        HorecaKandidaat(ext_id="bakker", naam="Bakkerij Jan",
                        categorie="bakery", adres="Straat 2", lat=50.9, lng=3.1,
                        confidence=0.9),
        HorecaKandidaat(ext_id="geenadres", naam="Vaag Café", categorie="cafe",
                        adres=None, postcode=None, lat=50.9, lng=3.1, confidence=0.9),
        HorecaKandidaat(ext_id="onzeker", naam="Twijfel", categorie="bar",
                        adres="Straat 3", lat=50.9, lng=3.1, confidence=0.3),
        HorecaKandidaat(ext_id="gecureerd_bakker", naam="Ooit Geimporteerd",
                        categorie="bakery", adres="Straat 4", lat=50.9, lng=3.1,
                        confidence=0.9),
    ])
    # de laatste is al een fiche -> moet blijven ondanks slechte categorie
    db.session.add(Event(slug="gc-bakker", title="Ooit Geimporteerd",
                         source="overture", ext_id="gecureerd_bakker",
                         is_permanent=True, curated=True, gemeente="Gent",
                         postcode="9000", lat=50.9, lng=3.1, age_min=0,
                         age_max=12, categories=[]))
    db.session.commit()
    n = kuis_kandidaten(log=lambda *a: None)
    assert n == 3                                          # bakker, geenadres, onzeker
    over = {k.ext_id for k in HorecaKandidaat.query.all()}
    assert over == {"goed", "gecureerd_bakker"}            # fiche-bakker gespaard
