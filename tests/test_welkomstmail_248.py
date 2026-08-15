"""Patch 248: welkomstmail één dag na registratie.

Een gezin dat registreert en daarna niets hoort, vergeet Ravot. Deze mail is
dienstgerelateerd (uitleg over het eigen profiel) en vertrekt dus ongeacht de
nieuwsbriefvoorkeur — met de uitnodiging voor de weekendmail als één klik,
wat wettelijk de juiste weg naar toestemming is.
"""
import re
from datetime import timedelta

from app.extensions import db
from app.models import Child, Event, Family, PostcodeCentroid, utcnow


def _opzet(app):
    with app.app_context():
        db.session.add(PostcodeCentroid(postcode="8800", gemeente="Roeselare",
                                        lat=50.9464, lng=3.1233))
        for n in range(5):
            db.session.add(Event(
                title=f"Speeltuin {n}", slug=f"wk248-{n}", source="osm",
                ext_id=f"wk248-{n}", is_permanent=True, pending=False,
                hidden=False, lat=50.95 + n * 0.01, lng=3.13,
                gemeente="Roeselare", subtype="playground", quality=70))
        nu = utcnow().replace(tzinfo=None)
        f1 = Family(email="gisteren@t.be", postcode="8800")
        db.session.add(f1)
        db.session.flush()
        f1.created_at = nu - timedelta(days=1)
        db.session.add(Child(family_id=f1.id, birth_year=2018))
        f2 = Family(email="vandaag@t.be", postcode="8800")
        db.session.add(f2)
        db.session.flush()
        f2.created_at = nu - timedelta(hours=2)
        f3 = Family(email="oud@t.be", postcode="8800")
        db.session.add(f3)
        db.session.flush()
        f3.created_at = nu - timedelta(days=8)
        f4 = Family(email="zonderpc@t.be")
        db.session.add(f4)
        db.session.flush()
        f4.created_at = nu - timedelta(days=1)
        db.session.commit()


def _verstuur(app):
    verstuurd = []

    def nep(naar, onderwerp, html, text=None):
        verstuurd.append({"naar": naar, "onderwerp": onderwerp, "html": html})

    with app.app_context():
        from app.services.welkomstmail import send_all
        with app.test_request_context(base_url="https://ravot.be"):
            n = send_all(nep)
    return n, verstuurd


def test_alleen_wie_gisteren_registreerde(app):
    """Niet meteen (dan verdrinkt hij naast de inlogcode) en niet te laat."""
    _opzet(app)
    n, verstuurd = _verstuur(app)
    adressen = sorted(v["naar"] for v in verstuurd)
    assert adressen == ["gisteren@t.be", "zonderpc@t.be"]
    assert n == 2


def test_inhoud_is_concreet_en_nodigt_uit_tot_meehelpen(app):
    _opzet(app)
    _, verstuurd = _verstuur(app)
    html = [v["html"] for v in verstuurd if v["naar"] == "gisteren@t.be"][0]
    assert "Speeltuin" in html                 # echte plekken dichtbij
    assert "+5" in html and "+15" in html      # punten uit de instellingen
    assert "completer maken" in html           # oproep tot verrijken
    assert "Profiel aanvullen" in html


def test_tekst_past_zich_aan_het_profiel_aan(app):
    _opzet(app)
    _, verstuurd = _verstuur(app)
    met = [v["html"] for v in verstuurd if v["naar"] == "gisteren@t.be"][0]
    zonder = [v["html"] for v in verstuurd if v["naar"] == "zonderpc@t.be"][0]
    assert "staan ingevuld" in met             # postcode + leeftijden bekend
    assert "Vul je postcode in" in zonder


def test_niet_twee_keer_versturen(app):
    _opzet(app)
    _verstuur(app)
    n2, _ = _verstuur(app)
    assert n2 == 0


def test_een_klik_zet_de_weekendmail_aan(client, app):
    """Eén actieve klik = geldige toestemming; een vervalst token niet."""
    _opzet(app)
    _, verstuurd = _verstuur(app)
    html = [v["html"] for v in verstuurd if v["naar"] == "gisteren@t.be"][0]
    link = re.search(r'href="(https://ravot\.be/weekendmail-aan/[^"]+)"',
                     html).group(1)
    client.get(link.replace("https://ravot.be", ""), follow_redirects=True)
    with app.app_context():
        fam = Family.query.filter_by(email="gisteren@t.be").first()
        assert fam.newsletter_opt_in is True
    r = client.get("/weekendmail-aan/vervalst", follow_redirects=True)
    assert "niet meer geldig" in r.get_data(as_text=True)
