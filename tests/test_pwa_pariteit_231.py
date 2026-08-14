"""Patch 231: de app moet kunnen wat de desktopsite kan, en het dashboard
splitst bijdragen op tussen gezinnen met profiel en anonieme bezoekers."""
import re
from datetime import timedelta

from argon2 import PasswordHasher

from app.extensions import db
from app.models import Admin, Event, Setting, VeldStem, utcnow


def test_app_heeft_dezelfde_ingangen_als_desktop(client, app):
    """Feestjes, kampen en de uitbatersingang stonden alleen in de
    desktopbalk — in de PWA waren ze onvindbaar."""
    with app.app_context():
        for k in ("feestjes_aan", "kampen_aan", "routes_in_menu"):
            db.session.add(Setting(key=k, value="1"))
        db.session.commit()
    h = client.get("/").get_data(as_text=True)
    assert "tab-meer" in h                       # Meer-knop in de tabbar
    assert "Verjaardagsfeestje" in h
    assert "Vakantiekampen" in h
    assert "Ik ben uitbater" in h
    assert "onclick" not in h                    # puur CSS, geen inline JS


def test_beheerder_ziet_beheerlink_in_de_app(client, app):
    with app.app_context():
        db.session.add(Admin(email="pw@r.be", pw_hash=PasswordHasher().hash("x"),
                             role="admin", totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        db.session.commit()
        aid = Admin.query.filter_by(email="pw@r.be").first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    h = client.get("/").get_data(as_text=True)
    assert "⚙️ Beheer" in h


def test_dashboard_splitst_gezin_en_anoniem(client, app):
    with app.app_context():
        db.session.add(Admin(email="sp@r.be", pw_hash=PasswordHasher().hash("x"),
                             role="admin", totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        ev = Event(title="P", slug="vs-1", source="osm", ext_id="vs-1",
                   is_permanent=True, pending=False, hidden=False, lat=51.0,
                   lng=3.5, subtype="playground")
        db.session.add(ev)
        db.session.flush()
        nu = utcnow().replace(tzinfo=None)
        for veld, stemmer in (("toilet", "1"), ("picknick", "1"),
                              ("parking", "7"), ("terras", "anon:aa"),
                              ("speelhoek", "anon:bb"),
                              ("drinkwater", "bron")):
            db.session.add(VeldStem(event_id=ev.id, veld=veld, stemmer=stemmer,
                                    waarde=True, gewicht=1.0, created_at=nu))
        db.session.commit()
        aid = Admin.query.filter_by(email="sp@r.be").first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    h = client.get("/beheer/", follow_redirects=True).get_data(as_text=True)
    blok = h[h.index("Gezinnen vullen aan"):][:2500]
    labels = re.findall(r'stat-label">([^<]+)<', blok)
    cijfers = re.findall(r'stat-cijfer">(\d+)<', blok)
    assert "Door gezinnen" in labels and "Anoniem" in labels
    assert cijfers[2] == "3"          # drie stemmen van ingelogde gezinnen
    assert cijfers[3] == "2"          # twee anonieme
    # de bronstem van OSM telt in geen van beide mee
    assert cijfers[1] == "5"
