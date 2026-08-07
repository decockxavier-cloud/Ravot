"""Patch 202: één waarheid overal — gezinsfoto's in een begrensde galerij,
het beheerformulier zonder verouderde dubbele blokken, en de leeftijdsgrens
(0-99) overal identiek, met server-side klem."""
from app.extensions import db
from app.models import Admin, Event, Family, Photo


def _opzet(app):
    from argon2 import PasswordHasher
    with app.app_context():
        db.session.add(Admin(email="ff@r.be", pw_hash=PasswordHasher().hash("x"),
                             role="admin", totp_secret="JBSWY3DPEHPK3PXP",
                             totp_confirmed=True))
        ev = Event(title="Club North", slug="ff-cn", source="user",
                   ext_id="ff-cn", is_permanent=True, pending=False,
                   hidden=False, lat=51.33, lng=3.18, subtype="horeca",
                   indoor=True)
        fam = Family(email="ff@t.be", postcode="8380")
        db.session.add_all([ev, fam])
        db.session.flush()
        db.session.add(Photo(event_id=ev.id, family_id=fam.id, soort="gezin",
                             filename="x.jpg", status="approved"))
        db.session.commit()
        return ev.id, Admin.query.filter_by(email="ff@r.be").first().id


def test_gezinsfotos_in_begrensde_galerij(client, app):
    _opzet(app)
    h = client.get("/e/ff-cn").get_data(as_text=True)
    assert 'class="foto-galerij"' in h
    assert "foto-raster" not in h          # ongestylede klasse is weg


def test_beheerformulier_zonder_dubbels(client, app):
    eid, aid = _opzet(app)
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    h = client.get(f"/beheer/activiteiten/{eid}").get_data(as_text=True)
    assert h.count('sectie-kop">Openingsuren') == 1     # was 2
    assert h.count('name="kinderstoel"') == 1           # was 2 (2e won stil)
    assert 'max="99"' in h and 'max="18"' not in h


def test_leeftijd_klem_overal_gelijk(client, app):
    eid, aid = _opzet(app)
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
        s["admin_rol"] = "admin"
    client.post(f"/beheer/activiteiten/{eid}", data={
        "actie": "bewerk", "titel": "Club North", "age_min": "6",
        "age_max": "150", "kinderstoel": "1", "categorie": "smullen"},
        follow_redirects=True)
    with app.app_context():
        ev = db.session.get(Event, eid)
        assert (ev.age_min, ev.age_max) == (6, 99)      # klem, zoals publiek
        assert ev.kinderstoel is True                   # raster slaat op
