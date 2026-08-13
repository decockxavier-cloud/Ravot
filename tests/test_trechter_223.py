"""Patch 223: conversietrechter — waar haken bezoekers af op weg naar een
gezinsprofiel? Bewust minimaal: dag, stap, aantal. Geen persoonsgegevens."""
from app.extensions import db
from app.models import Admin, Event, TrechterTeller


def _plekken(app):
    with app.app_context():
        for n in range(4):
            db.session.add(Event(title=f"Plek {n}", slug=f"tr-{n}",
                                 source="osm", ext_id=f"tr-{n}",
                                 is_permanent=True, pending=False,
                                 hidden=False, lat=51.0, lng=3.5,
                                 subtype="playground", categories=["buiten"]))
        db.session.commit()


def _tellers(app):
    with app.app_context():
        return {r.stap: r.aantal for r in TrechterTeller.query.all()}


def test_fiches_en_verdieping_tellen_per_sessie(client, app):
    _plekken(app)
    for n in range(3):
        client.get(f"/e/tr-{n}")
    t = _tellers(app)
    assert t.get("bezoek") == 1          # één keer per sessie, niet per fiche
    assert t.get("verdieping") == 1      # 3+ fiches: aan het plannen


def test_login_stap_wordt_geteld(client, app):
    _plekken(app)
    client.get("/e/tr-0")
    client.get("/login")
    t = _tellers(app)
    assert t.get("login_gezien") == 1


def test_tweede_sessie_telt_apart(client, app):
    _plekken(app)
    for n in range(3):
        client.get(f"/e/tr-{n}")
    with client.session_transaction() as s:
        s.clear()                        # nieuwe bezoeker
    client.get("/e/tr-0")
    t = _tellers(app)
    assert t.get("bezoek") == 2
    assert t.get("verdieping") == 1      # tweede sessie ging niet diep


def test_dashboard_toont_trechter(client, app):
    from argon2 import PasswordHasher
    _plekken(app)
    client.get("/e/tr-0")
    with app.app_context():
        db.session.add(Admin(email="tr@r.be", pw_hash=PasswordHasher().hash("x"),
                             role="admin", totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        db.session.commit()
        aid = Admin.query.filter_by(email="tr@r.be").first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    h = client.get("/beheer/", follow_redirects=True).get_data(as_text=True)
    assert "Van bezoeker tot profiel" in h
    assert "Fiche bekeken" in h and "Profiel aangemaakt" in h
    assert "trechter-vul" in h


def test_meten_mag_nooit_de_bezoeker_hinderen(client, app, monkeypatch):
    """Gaat het tellen mis, dan moet de pagina gewoon laden."""
    _plekken(app)
    import app.trechter as T

    def stuk(*a, **kw):
        raise RuntimeError("databank plat")

    monkeypatch.setattr(T, "tel_stap", stuk)
    assert client.get("/e/tr-0").status_code == 200
