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
    r = client.post(f"/mijn/beloningen/{b.id}/wissel", follow_redirects=True)
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
    client.post(f"/mijn/beloningen/{b.id}/wissel")
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
