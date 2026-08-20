"""Patch 265: gestructureerde gemeentepagina + veldvragen + evenementformulier.

Drie beloftes worden hier vastgeklikt:
1. De bestaande gemeentelinks blijven exact werken (geen redirect, geen 404) —
   de mailing naar de diensten toerisme mag nooit breken.
2. De dienst kan veldvragen beantwoorden met één klik; die stemmen lopen in
   dezelfde VeldStem-teller als alle andere en zijn intrekbaar.
3. Evenementen doorgeven landt als pending in de nazichtwachtrij, met de
   honeypot als botvanger.
"""
import re

from argon2 import PasswordHasher

from app.extensions import db
from app.models import Admin, Event, Setting, VeldStem


def _token(app, client, gemeente="Brussel"):
    with app.app_context():
        db.session.add(Setting(key="uit_zichtbaar", value="0"))
        db.session.add(Admin(email="gs@r.be", pw_hash=PasswordHasher().hash("x"),
                             role="admin", totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        db.session.add(Event(title="Speeltuin Zuid", slug="gs265-1",
                             source="osm", ext_id="gs265-1", is_permanent=True,
                             pending=False, hidden=False, lat=50.85, lng=4.35,
                             gemeente=gemeente, subtype="playground"))
        db.session.add(Event(title="Stadspark", slug="gs265-2",
                             source="osm", ext_id="gs265-2", is_permanent=True,
                             pending=False, hidden=False, lat=50.86, lng=4.36,
                             gemeente=gemeente, subtype="park"))
        db.session.add(Event(title="Speeltuin Elders", slug="gs265-3",
                             source="osm", ext_id="gs265-3", is_permanent=True,
                             pending=False, hidden=False, lat=51.0, lng=3.7,
                             gemeente="Gent", subtype="playground"))
        db.session.commit()
        aid = Admin.query.filter_by(email="gs@r.be").first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    det = client.get(f"/beheer/gemeentecontacten/{gemeente.lower()}").get_data(as_text=True)
    return re.search(r"gemeente-bijdrage/([\w\-]+)", det).group(1)


def _event_id(app, slug):
    with app.app_context():
        return Event.query.filter_by(slug=slug).first().id


# ── 1. Link-stabiliteit ─────────────────────────────────────────────────────

def test_bestaande_link_blijft_exact_werken(client, app):
    """De verstuurde URL geeft 200 op precies hetzelfde pad — geen redirect,
    geen nieuw slug-formaat. Wie de mail van vorige maand heeft, komt binnen."""
    token = _token(app, client)
    r = client.get(f"/gemeente-bijdrage/{token}")
    assert r.status_code == 200                      # geen 301/302/404
    assert "Ravot &amp; Brussel" in r.get_data(as_text=True)


def test_oude_alles_parameter_breekt_niet(client, app):
    """'?alles=1' stond in eerder gedeelde links; die mag nooit een fout geven."""
    token = _token(app, client)
    assert client.get(f"/gemeente-bijdrage/{token}?alles=1").status_code == 200


def test_ongeldig_token_geeft_404(client, app):
    _token(app, client)
    assert client.get("/gemeente-bijdrage/geen-geldig-token-xxxxx").status_code == 404


# ── 2. Structuur ────────────────────────────────────────────────────────────

def test_pagina_groepeert_per_type_met_voortgang(client, app):
    token = _token(app, client)
    h = client.get(f"/gemeente-bijdrage/{token}").get_data(as_text=True)
    assert "gem-groep" in h and "voortgang" in h     # accordeon + balk
    assert "Speeltuin Zuid" in h and "Stadspark" in h
    assert "Speeltuin Elders" not in h               # andere gemeente
    # Open veldvragen zichtbaar (playground → o.a. toilet-vraag).
    assert "Is er een toilet?" in h
    # Type-specifiek blijft type-specifiek: kindermenu hoort hier nergens.
    assert "kindermenu" not in h.lower()


# ── 3. Veldvragen ───────────────────────────────────────────────────────────

def test_gemeente_stem_telt_en_is_intrekbaar(client, app):
    token = _token(app, client)
    eid = _event_id(app, "gs265-1")
    r = client.post(f"/gemeente-bijdrage/{token}/stem/{eid}/toilet/ja")
    assert r.status_code == 302
    with app.app_context():
        s = VeldStem.query.filter_by(event_id=eid, veld="toilet",
                                     stemmer="gemeente:brussel").first()
        assert s is not None and s.waarde is True
        assert s.gewicht == 3.5                      # GEMEENTE_GEWICHT (anker)
        assert db.session.get(Event, eid).toilet is True   # boolean loopt mee
    # Nogmaals hetzelfde antwoord = intrekken (zoals de anonieme stemmen).
    client.post(f"/gemeente-bijdrage/{token}/stem/{eid}/toilet/ja")
    with app.app_context():
        assert VeldStem.query.filter_by(event_id=eid, veld="toilet",
                                        stemmer="gemeente:brussel").count() == 0


def test_stem_buiten_eigen_gemeente_geweigerd(client, app):
    """Het token van Brussel geeft geen stemrecht over Gent."""
    token = _token(app, client)
    eid = _event_id(app, "gs265-3")
    assert client.post(
        f"/gemeente-bijdrage/{token}/stem/{eid}/toilet/ja").status_code == 403


def test_stem_op_irrelevant_veld_geweigerd(client, app):
    """Een kindermenu-vraag bij een speeltuin bestaat niet — ook niet via POST."""
    token = _token(app, client)
    eid = _event_id(app, "gs265-1")
    assert client.post(
        f"/gemeente-bijdrage/{token}/stem/{eid}/kindermenu/ja").status_code == 400


# ── 4. Evenement doorgeven ──────────────────────────────────────────────────

def test_evenement_landt_pending_in_wachtrij(client, app):
    token = _token(app, client)
    assert client.get(f"/gemeente-bijdrage/{token}/evenement").status_code == 200
    r = client.post(f"/gemeente-bijdrage/{token}/evenement", data={
        "titel": "Buitenspeeldag", "beschrijving": "Spelen op straat.",
        "datum_start": "2027-04-21", "age_min": "3", "age_max": "12",
        "gratis": "1"})
    assert r.status_code == 302
    with app.app_context():
        ev = Event.query.filter_by(title="Buitenspeeldag").first()
        assert ev is not None
        assert ev.pending is True and ev.source == "gemeente"
        assert ev.gemeente == "Brussel" and ev.is_permanent is False
        assert "doorgegeven door" in (ev.attribution or "")
        # En hij staat dus in de gewone nazichtwachtrij.
        assert Event.query.filter_by(pending=True).count() == 1


def test_evenement_validatie_bewaart_invoer(client, app):
    """Einddatum vóór start → fout tonen mét de ingevulde velden."""
    token = _token(app, client)
    h = client.post(f"/gemeente-bijdrage/{token}/evenement", data={
        "titel": "Kindermarkt", "datum_start": "2027-05-10",
        "datum_eind": "2027-05-01"}).get_data(as_text=True)
    assert "einddatum" in h.lower()
    assert 'value="Kindermarkt"' in h                # invoer niet kwijt
    with app.app_context():
        assert Event.query.filter_by(title="Kindermarkt").count() == 0


def test_honeypot_vangt_bots(client, app):
    """Een bot die het onzichtbare veld invult, krijgt een bevestiging maar
    er wordt niets aangemaakt."""
    token = _token(app, client)
    r = client.post(f"/gemeente-bijdrage/{token}/evenement", data={
        "titel": "Spam Event", "datum_start": "2027-04-21",
        "website2": "http://spam.example"})
    assert r.status_code == 302
    with app.app_context():
        assert Event.query.filter_by(title="Spam Event").count() == 0


# ── 5. Gemeentevoorrang & crawlbescherming (patch 266) ──────────────────────

def test_gemeentestem_is_anker_maar_geen_dictaat(client, app):
    """Eén gezin kan de gemeente nooit overrulen (VERTROUWEN_MAX 2.5 < anker
    3.5); genoeg gezinnen samen wél — als iets niet klopt, zeggen ze het.
    Trekt de dienst zijn antwoord in, dan herneemt het gewone telwerk."""
    from app import stemmen as _stemmen
    # Structurele garantie: het anker ligt boven wat één stemmer ooit kan wegen.
    assert _stemmen.GEMEENTE_GEWICHT > _stemmen.VERTROUWEN_MAX
    token = _token(app, client)
    eid = _event_id(app, "gs265-1")
    client.post(f"/gemeente-bijdrage/{token}/stem/{eid}/parking/nee")
    with app.app_context():
        # Zes anonieme tegenstemmen (6 × 0.5 = 3.0 < 3.5): anker houdt stand.
        for i in range(6):
            _stemmen.leg_stem_vast(eid, "parking", True, anon_id=f"g267-{i}")
        db.session.commit()
        st = _stemmen.veld_status(eid, "parking")
        assert st["waarde"] is False and st["herkomst"] == "gemeente"
        # Twee erbij (8 × 0.5 = 4.0 > 3.5): de terreinwaarheid kantelt het.
        for i in range(6, 8):
            _stemmen.leg_stem_vast(eid, "parking", True, anon_id=f"g267-{i}")
        db.session.commit()
        assert _stemmen.veld_status(eid, "parking")["waarde"] is True
    # Intrekken kan nog altijd; daarna tellen enkel de bezoekersstemmen.
    client.post(f"/gemeente-bijdrage/{token}/stem/{eid}/parking/nee")
    with app.app_context():
        assert _stemmen.veld_status(eid, "parking")["waarde"] is True
        assert VeldStem.query.filter_by(event_id=eid, veld="parking",
                                        stemmer="gemeente:brussel").count() == 0


def test_tegenspraak_zichtbaar_voor_de_dienst(client, app):
    """Kantelen de bezoekers een gemeente-antwoord, dan ziet de dienst dat op
    zijn pagina als open punt met een herbekijk-waarschuwing."""
    from app import stemmen as _stemmen
    token = _token(app, client)
    eid = _event_id(app, "gs265-1")
    client.post(f"/gemeente-bijdrage/{token}/stem/{eid}/parking/nee")
    with app.app_context():
        for i in range(8):
            _stemmen.leg_stem_vast(eid, "parking", True, anon_id=f"t267-{i}")
        db.session.commit()
    h = client.get(f"/gemeente-bijdrage/{token}").get_data(as_text=True)
    assert "herbekijken" in h


def test_beantwoord_veld_blijft_wijzigbaar_op_de_pagina(client, app):
    """Na een antwoord verdwijnt de vraag niet: de dienst ziet zijn eigen
    antwoord staan en kan het wijzigen of intrekken."""
    token = _token(app, client)
    eid = _event_id(app, "gs265-1")
    client.post(f"/gemeente-bijdrage/{token}/stem/{eid}/toilet/ja")
    h = client.get(f"/gemeente-bijdrage/{token}").get_data(as_text=True)
    assert "u antwoordde" in h and "in te trekken" in h


def test_gemeentelink_niet_crawlbaar(client, app):
    """Drie sloten: meta-noindex, X-Robots-Tag én robots.txt — ook voor de
    AI-bots die verder overal welkom zijn."""
    token = _token(app, client)
    r = client.get(f"/gemeente-bijdrage/{token}")
    assert "noindex" in r.get_data(as_text=True)                 # meta
    assert "noindex" in (r.headers.get("X-Robots-Tag") or "")    # header
    r2 = client.get(f"/gemeente-bijdrage/{token}/evenement")
    assert "noindex" in (r2.headers.get("X-Robots-Tag") or "")
    robots = client.get("/robots.txt").get_data(as_text=True)
    assert robots.count("Disallow: /gemeente-bijdrage/") >= 5    # elke UA-sectie
    # En hij staat vanzelfsprekend in geen enkele sitemap.
    for pad in ("/sitemap.xml", "/sitemap-gemeenten.xml"):
        s = client.get(pad)
        if s.status_code == 200:
            assert "gemeente-bijdrage" not in s.get_data(as_text=True)


# ── 6. Dashboardblok "Gemeenten werken mee" (patch 268) ─────────────────────

def test_dashboard_toont_meewerkende_gemeenten(client, app):
    """Na een veldantwoord en een doorgegeven evenement toont het dashboard
    Brussel als meewerkende gemeente, met de juiste tellers."""
    token = _token(app, client)
    eid = _event_id(app, "gs265-1")
    client.post(f"/gemeente-bijdrage/{token}/stem/{eid}/toilet/ja")
    client.post(f"/gemeente-bijdrage/{token}/evenement", data={
        "titel": "Speelstraat", "datum_start": "2027-07-01", "gratis": "1"})
    h = client.get("/beheer/").get_data(as_text=True)
    assert "Gemeenten werken mee" in h
    assert "Brussel" in h                            # in de meewerk-tabel
    # Tellers: 1 link actief, 1 werkt mee, 1 veldantwoord, 1 evenement.
    import re as _re
    blok = h[h.index("Gemeenten werken mee"):]
    cijfers = _re.findall(r'stat-cijfer">(\d+)<', blok)[:6]
    assert cijfers[:5] == ["1", "1", "1", "0", "1"]  # links/mee/stemmen/fotos/events


def test_gemeentestem_vervuilt_gezinspols_niet(client, app):
    """Het blok 'Gezinnen vullen aan' blijft zuiver: een gemeentestem telt
    daar niet mee (die heeft zijn eigen blok)."""
    token = _token(app, client)
    eid = _event_id(app, "gs265-1")
    client.post(f"/gemeente-bijdrage/{token}/stem/{eid}/toilet/ja")
    h = client.get("/beheer/").get_data(as_text=True)
    gezinsblok = h[h.index("Gezinnen vullen aan"):h.index("Gemeenten werken mee")]
    import re as _re
    cijfers = _re.findall(r'stat-cijfer">(\d+)<', gezinsblok)
    assert cijfers[0] == "0" and cijfers[1] == "0"   # vandaag/week: geen gezinsstem


# ── 7. Mailtekst in het contactdetail (patch 269) ───────────────────────────

def test_mailtekst_dekt_alle_bijdragemogelijkheden(client, app):
    """De klaargezette mail noemt de vier dingen die de link nu kan — en de
    oude 'we vragen bewust niet meer'-belofte (die evenementen uitsloot) is
    weg, want die klopt niet meer."""
    _token(app, client)                     # zet admin-sessie + contact klaar
    h = client.get("/beheer/gemeentecontacten/brussel").get_data(as_text=True)
    assert "korte vragen beantwoorden" in h
    assert "evenementen voor gezinnen doorgeven" in h
    assert "foto's toevoegen" in h and "korte tekst schrijven" in h
    assert "weegt bij ons het zwaarst" in h          # ankerbelofte, eerlijk
    assert "zet hem niet op uw website" in h         # privélink-waarschuwing
    assert "We vragen bewust niet meer dan dat" not in h
    assert "Evenementen halen we al uit UiTdatabank" not in h
    # Het open-vragencijfer staat in de kop (2 speelplekken → vragen open).
    assert "open vragen" in h


# ── 8. Rijker evenementformulier (patch 270) ────────────────────────────────

def test_evenement_met_uren_locatie_organisator_en_foto(client, app):
    """Uren komen in start/eind, locatienaam vóór het adres, organisator in
    de beschrijving, en de foto landt pending in de fotowachtrij."""
    import io
    from app.models import Photo
    token = _token(app, client)
    png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00"
           b"\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9c"
           b"c\x00\x01\x00\x00\x05\x00\x01\r\n\x2d\xb4\x00\x00\x00\x00IEND"
           b"\xaeB`\x82")
    r = client.post(f"/gemeente-bijdrage/{token}/evenement", data={
        "titel": "Voorleesuurtje", "beschrijving": "Verhalen voor kleuters.",
        "datum_start": "2027-09-04", "uur_start": "14:00", "uur_eind": "16:30",
        "locatienaam": "Bibliotheek Muntpunt", "adres": "Munt 6",
        "organisator": "Dienst Vrije Tijd", "gratis": "1",
        "foto_akkoord": "1",
        "fotos": (io.BytesIO(png), "affiche.png")},
        content_type="multipart/form-data")
    assert r.status_code == 302
    with app.app_context():
        ev = Event.query.filter_by(title="Voorleesuurtje").first()
        assert ev is not None and ev.pending is True
        assert ev.start.hour == 14 and ev.end.hour == 16 and ev.end.minute == 30
        assert ev.adres == "Bibliotheek Muntpunt, Munt 6"
        assert "Georganiseerd door Dienst Vrije Tijd." in ev.description
        f = Photo.query.filter_by(event_id=ev.id).first()
        assert f is not None and f.status == "pending" and f.soort == "gemeente"


def test_evenement_zonder_uren_blijft_hele_dag(client, app):
    """Geen uren ingevuld → 00:00–23:59, zoals voorheen."""
    token = _token(app, client)
    client.post(f"/gemeente-bijdrage/{token}/evenement", data={
        "titel": "Kindermarkt", "datum_start": "2027-09-05", "gratis": "1"})
    with app.app_context():
        ev = Event.query.filter_by(title="Kindermarkt").first()
        assert ev.start.hour == 0 and ev.end.hour == 23 and ev.end.minute == 59


# ── 9. Foutieve fiche melden (patch 271) ────────────────────────────────────

def test_gemeente_meldt_fiche_en_die_gaat_naar_nazicht(client, app):
    """Eén gemeentemelding volstaat om een gecureerde fiche terug naar het
    nazicht te sturen (gezinnen hebben er drie nodig) — verwijderen blijft
    een redactiebeslissing."""
    from app.models import Report
    token = _token(app, client)
    eid = _event_id(app, "gs265-1")
    with app.app_context():
        ev = db.session.get(Event, eid)
        ev.curated = True
        db.session.commit()
    r = client.post(f"/gemeente-bijdrage/{token}/meld/{eid}", data={
        "reason": "gesloten", "note": "Afgebroken in 2025"})
    assert r.status_code == 302
    with app.app_context():
        rap = Report.query.filter_by(event_id=eid, handled=False).first()
        assert rap is not None and rap.reason == "gesloten"
        assert rap.note.startswith("[Gemeente Brussel]")
        assert "Afgebroken in 2025" in rap.note
        assert db.session.get(Event, eid).curated is False   # terug naar nazicht
    # De pagina toont nu "doorgestuurd" in plaats van nogmaals de meldknop.
    h = client.get(f"/gemeente-bijdrage/{token}").get_data(as_text=True)
    assert "Uw melding over deze plek is doorgestuurd" in h


def test_melden_buiten_eigen_gemeente_geweigerd(client, app):
    token = _token(app, client)
    eid = _event_id(app, "gs265-3")                  # Gent
    assert client.post(f"/gemeente-bijdrage/{token}/meld/{eid}",
                       data={"reason": "gesloten"}).status_code == 403


# ── 10. Ontbrekende plek toevoegen (patch 272) ──────────────────────────────

def test_plek_toevoegen_landt_pending_met_type(client, app):
    token = _token(app, client)
    assert client.get(f"/gemeente-bijdrage/{token}/plek").status_code == 200
    r = client.post(f"/gemeente-bijdrage/{token}/plek", data={
        "titel": "Speeltuin De Vlindertuin", "soort": "playground",
        "postcode": "1000", "gratis": "1", "age_min": "2", "age_max": "10"})
    assert r.status_code == 302
    with app.app_context():
        ev = Event.query.filter_by(title="Speeltuin De Vlindertuin").first()
        assert ev is not None
        assert ev.pending is True and ev.source == "gemeente"
        assert ev.is_permanent is True and ev.subtype == "playground"
        assert ev.gemeente == "Brussel"
        assert ev.lat is not None                    # via postcode_coord
        assert "doorgegeven door" in (ev.attribution or "")


def test_plek_zonder_soort_bewaart_invoer(client, app):
    token = _token(app, client)
    h = client.post(f"/gemeente-bijdrage/{token}/plek", data={
        "titel": "Naamloos Park"}).get_data(as_text=True)
    assert "Kies wat voor plek" in h
    assert 'value="Naamloos Park"' in h              # invoer niet kwijt
    with app.app_context():
        assert Event.query.filter_by(title="Naamloos Park").count() == 0


def test_mail_noemt_plek_toevoegen_en_melden(client, app):
    _token(app, client)
    h = client.get("/beheer/gemeentecontacten/brussel").get_data(as_text=True)
    assert "gezinsplek toevoegen die nog niet op Ravot staat" in h
    assert "melden wanneer een plek niet meer bestaat" in h
