"""Patch 210: dashboardpols op de crowdsourcing — hoeveel parameters vullen
gezinnen aan (vandaag/week), op hoeveel fiches, door hoeveel bijdragers."""
from datetime import timedelta

from argon2 import PasswordHasher

from app.extensions import db
from app.models import Admin, Event, Family, Photo, VeldStem, utcnow


def test_dashboard_toont_aanvulbeweging(client, app):
    import re
    with app.app_context():
        db.session.add(Admin(email="dsh@r.be", pw_hash=PasswordHasher().hash("x"),
                             role="admin", totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        ev = Event(title="P", slug="dsh-1", source="osm", ext_id="dsh-1",
                   is_permanent=True, pending=False, hidden=False, lat=51.0,
                   lng=3.5, subtype="playground")
        ev2 = Event(title="Q", slug="dsh-2", source="osm", ext_id="dsh-2",
                    is_permanent=True, pending=False, hidden=False, lat=51.0,
                    lng=3.5, subtype="playground")
        fam = Family(email="dsh@t.be", postcode="8800")
        db.session.add_all([ev, ev2, fam])
        db.session.flush()
        nu = utcnow().replace(tzinfo=None)
        for veld, stemmer in (("toilet", "1"), ("picknick", "1"),
                              ("toilet", "anon:x")):
            db.session.add(VeldStem(event_id=ev.id, veld=veld, stemmer=stemmer,
                                    waarde=True, gewicht=1.0, created_at=nu))
        # bronstem telt NIET mee: dit gaat over wat mensen aanvullen
        db.session.add(VeldStem(event_id=ev.id, veld="parking", stemmer="bron",
                                waarde=True, gewicht=1.0, created_at=nu))
        db.session.add(VeldStem(event_id=ev2.id, veld="toilet", stemmer="1",
                                waarde=True, gewicht=1.0,
                                created_at=nu - timedelta(days=3)))
        db.session.add(Photo(event_id=ev.id, family_id=fam.id, soort="gezin",
                             filename="p.jpg", status="approved",
                             created_at=nu))
        db.session.commit()
        aid = Admin.query.filter_by(email="dsh@r.be").first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    h = client.get("/beheer/", follow_redirects=True).get_data(as_text=True)
    assert "Gezinnen vullen aan" in h
    blok = h[h.index("Gezinnen vullen aan"):][:2000]
    # p231 voegde twee tegels toe (gezin/anoniem), dus lezen we op label:
    labels = re.findall(r'stat-label">([^<]+)<', blok)
    cijfers = re.findall(r'stat-cijfer">(\d+)<', blok)
    per_label = dict(zip(labels, cijfers))
    assert per_label["Antwoorden vandaag"] == "3"
    assert per_label["Deze week"] == "4"
    assert per_label["Bijdragers"] == "2"      # stemmer "1" en "anon:x"
    assert per_label["Foto's deze week"] == "1"
    assert "op 2 fiches" in blok
    labels = re.findall(r'badge">([^<]+)</span>', blok)
    assert any("toilet" in x for x in labels)       # leesbare veldnamen
