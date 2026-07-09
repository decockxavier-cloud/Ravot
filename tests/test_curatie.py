"""Curatie: Ravot-waardig, curatie-modus, community-meldingen."""
import pyotp
from argon2 import PasswordHasher
from app.extensions import db
from app.models import Admin, Event, Setting, Report


def _ev(**kw):
    d = dict(source="osm", slug=kw.pop("slug", "x"), title="X", is_permanent=True,
             subtype="playground", gemeente="Gent", postcode="9000", lat=51.0, lng=3.7,
             age_min=0, age_max=12, categories=["buiten"], quality=50)
    d.update(kw)
    return Event(**d)


def _admin(client, app):
    with app.app_context():
        a = Admin(email="a@r.be", pw_hash=PasswordHasher().hash("x"),
                  totp_secret=pyotp.random_base32(), totp_confirmed=True, role="admin")
        db.session.add(a); db.session.commit(); aid = a.id
    with client.session_transaction() as s:
        s["admin_id"] = aid; s["admin_2fa_ok"] = True


def test_curatiemodus_toont_enkel_goedgekeurd(client, app):
    with app.app_context():
        db.session.add(_ev(slug="goed", title="Goedgekeurd", curated=True))
        db.session.add(_ev(slug="niet", title="NietBeoordeeld", curated=False, subtype="park"))
        db.session.merge(Setting(key="enkel_gecureerd", value="1"))
        db.session.commit()
    h = client.get("/gent").get_data(as_text=True)
    assert "Goedgekeurd" in h and "NietBeoordeeld" not in h
    # ontsnappingsklep
    h2 = client.get("/verkennen?alles_tonen=1").get_data(as_text=True)
    assert "NietBeoordeeld" in h2


def test_curatiemodus_uit_toont_alles(client, app):
    with app.app_context():
        db.session.add(_ev(slug="niet", title="NietBeoordeeld", curated=False))
        db.session.merge(Setting(key="enkel_gecureerd", value="0"))
        db.session.commit()
    assert "NietBeoordeeld" in client.get("/gent").get_data(as_text=True)


def test_admin_markeert_ravot_waardig(client, app):
    with app.app_context():
        db.session.add(_ev(slug="p", title="Plek"))
        db.session.commit()
        eid = Event.query.filter_by(slug="p").first().id
    _admin(client, app)
    r = client.post(f"/beheer/activiteiten/{eid}/waardig", data={"csrf_token": "x"})
    assert r.status_code == 302
    with app.app_context():
        ev = db.session.get(Event, eid)
        assert ev.curated is True and ev.curated_at is not None


def test_anoniem_melden_kan_maar_telt_als_een_stem(client, app):
    """Anoniem melden werkt, maar één persoon kan de gecureerde kern niet
    leegtrekken: alle anonieme meldingen samen tellen als één stem."""
    with app.app_context():
        db.session.add(_ev(slug="goed", title="Goed", curated=True))
        db.session.merge(Setting(key="report_drempel", value="3"))
        db.session.commit()
        eid = Event.query.filter_by(slug="goed").first().id
    for _ in range(3):
        assert client.post(f"/mijn/melden/{eid}", data={"reason": "gesloten"}).status_code == 302
    with app.app_context():
        ev = db.session.get(Event, eid)
        assert Report.query.filter_by(event_id=eid).count() == 3   # meldingen bewaard
        assert ev.curated is True    # maar ✓ blijft: 3x anoniem = 1 stem


def test_escalatie_bij_meldingen_van_verschillende_gezinnen(client, app):
    from app.models import Family
    with app.app_context():
        db.session.add(_ev(slug="goed2", title="Goed2", curated=True))
        db.session.merge(Setting(key="report_drempel", value="3"))
        for i in range(3):
            db.session.add(Family(email=f"f{i}@x.be", postcode="9000"))
        db.session.commit()
        eid = Event.query.filter_by(slug="goed2").first().id
        fids = [f.id for f in Family.query.all()]
    for fid in fids:
        with client.session_transaction() as s:
            s["family_id"] = fid
        client.post(f"/mijn/melden/{eid}", data={"reason": "gesloten"})
    with app.app_context():
        assert db.session.get(Event, eid).curated is False   # 3 gezinnen -> nazicht


def test_ssrf_bescherming_webtekst(app):
    """URL-verrijking mag geen interne diensten bereiken (SSRF)."""
    from app.enrich import _haal_webtekst
    for url in ("http://localhost:11434/api/tags", "http://127.0.0.1:8080/",
                "http://ollama:11434/", "http://169.254.169.254/latest/meta-data/",
                "http://10.0.0.5/", "ftp://x.be/", "file:///etc/passwd", None):
        assert _haal_webtekst(url) is None, url


def test_open_redirect_geblokkeerd(client, app):
    import pyotp as _p
    from argon2 import PasswordHasher as _PH
    with app.app_context():
        a = Admin(email="b@r.be", pw_hash=_PH().hash("x"),
                  totp_secret=_p.random_base32(), totp_confirmed=True, role="admin")
        db.session.add(a)
        db.session.add(_ev(slug="rd", title="Redir"))
        db.session.commit()
        aid, eid = a.id, Event.query.filter_by(slug="rd").first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid; s["admin_2fa_ok"] = True
    r = client.post(f"/beheer/activiteiten/{eid}/waardig",
                    data={"csrf_token": "x", "terug": "https://evil.example/phish"})
    assert r.status_code == 302
    assert "evil.example" not in (r.location or "")   # extern doel geweigerd
