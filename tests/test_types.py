"""Activiteittypes: label/badge, en zichtbaarheid per type."""
from app.extensions import db
from app.models import Event, Setting, Admin
from app.types import activiteit_type, type_code, verborgen_type_codes
import pyotp
from argon2 import PasswordHasher


def _ev(**kw):
    d = dict(source="osm", slug=kw.pop("slug", "x"), title="X", is_permanent=True,
             gemeente="Gent", postcode="9000", lat=51.0, lng=3.7,
             age_min=0, age_max=12, categories=["buiten"], quality=50)
    d.update(kw)
    return Event(**d)


def test_type_speeltuin_vs_museum(app):
    with app.app_context():
        sp = _ev(subtype="playground")
        mu = _ev(subtype="museum", categories=["cultuur"])
        assert activiteit_type(sp)["label"] == "Speeltuin (openbaar)"
        assert activiteit_type(sp)["emoji"] == "🛝"
        assert activiteit_type(mu)["label"] == "Museum"


def test_type_event_uit_categorie(app):
    with app.app_context():
        ev = _ev(subtype=None, is_permanent=False, categories=["cultuur"])
        assert type_code(ev) == "ev_cultuur"
        assert activiteit_type(ev)["label"] == "Voorstelling"


def test_type_badge_op_kaart(client, app):
    with app.app_context():
        db.session.add(_ev(slug="sp", subtype="playground", title="Buurtspeeltuin"))
        db.session.commit()
    html = client.get("/gent").get_data(as_text=True)
    assert "badge-type" in html and "🛝" in html


def _admin(client, app):
    with app.app_context():
        a = Admin(email="a@r.be", pw_hash=PasswordHasher().hash("x"),
                  totp_secret=pyotp.random_base32(), totp_confirmed=True, role="admin")
        db.session.add(a); db.session.commit(); aid = a.id
    with client.session_transaction() as s:
        s["admin_id"] = aid; s["admin_2fa_ok"] = True


def test_verborgen_type_verdwijnt_publiek(client, app):
    with app.app_context():
        db.session.add(_ev(slug="sp", subtype="playground", title="OpenbareSpeeltuin"))
        db.session.add(_ev(slug="mu", subtype="museum", title="MooiMuseum", categories=["cultuur"]))
        db.session.commit()
    _admin(client, app)
    # enkel museum zichtbaar laten
    r = client.post("/beheer/types", data={"csrf_token": "x", "zichtbaar": ["museum"]})
    assert r.status_code == 302
    html = client.get("/gent").get_data(as_text=True)
    assert "MooiMuseum" in html and "OpenbareSpeeltuin" not in html
    # maar in het beheer blijft de speeltuin bestaan
    assert client.get("/beheer/activiteiten?q=OpenbareSpeeltuin").status_code == 200


def test_verborgen_type_ook_van_kaart(client, app):
    with app.app_context():
        db.session.add(_ev(slug="sp", subtype="playground", title="KaartSpeeltuin"))
        db.session.merge(Setting(key="verborgen_types", value="playground"))
        db.session.commit()
    html = client.get("/verkennen").get_data(as_text=True)
    assert "KaartSpeeltuin" not in html


def test_uit_eventtype_geeft_fijn_type(app):
    """UiT-events krijgen een fijn subtype uit hun eventType (gelijkwaardig aan OSM)."""
    from app.services import uit_sync
    from app.types import activiteit_type
    def mk(naam, ettype):
        return {"@id": "https://x/e/" + naam, "name": {"nl": naam},
                "typicalAgeRange": "0-12",
                "terms": [{"label": {"nl": ettype}, "domain": "eventtype"}],
                "location": {"@id": "https://x/l/1", "name": {"nl": "Zaal"},
                             "address": {"addressLocality": "Gent", "postalCode": "9000"},
                             "geo": {"latitude": 51.0, "longitude": 3.7}},
                "labels": ["Vlieg"]}
    with app.app_context():
        theater = uit_sync.normalise(mk("Poppentheater", "Theatervoorstelling"))
        indoor = uit_sync.normalise(mk("Kiko", "Indoorspeeltuin"))
        assert theater["subtype"] == "uit_theater"
        assert indoor["subtype"] == "uit_indoorspeeltuin"
        # de indoor-speeltuin is een ander type dan een openbare speeltuin
        assert indoor["subtype"] != "playground"


def test_vlieg_geeft_kwaliteitsbonus_maar_geen_poort(app):
    from app.kwaliteit import bereken_kwaliteit
    from app.models import Event
    with app.app_context():
        zonder = Event(source="uit", slug="a", title="Iets leuks voor kinderen",
                       has_vlieg=False, categories=["cultuur"])
        met = Event(source="uit", slug="b", title="Iets leuks voor kinderen",
                    has_vlieg=True, categories=["cultuur"])
        assert bereken_kwaliteit(met, heeft_reviews=False) == \
               bereken_kwaliteit(zonder, heeft_reviews=False) + 8


def test_organisatoren_met_zelfde_naam_botsen_niet(app):
    """Regressie: twee organisatoren met dezelfde naam kregen dezelfde slug ->
    UniqueViolation -> events overgeslagen. Nu krijgen ze een unieke slug."""
    from app.services import uit_sync
    from app.models import Organizer, Event
    def mk(ev, orgnaam, oid):
        return {"@id": "https://x/e/" + ev, "name": {"nl": ev}, "typicalAgeRange": "0-12",
                "terms": [{"label": {"nl": "Theatervoorstelling"}, "domain": "eventtype"}],
                "organizer": {"@id": "https://x/o/" + oid, "name": {"nl": orgnaam}},
                "location": {"@id": "https://x/l/" + oid, "name": {"nl": "Zaal"},
                             "address": {"addressLocality": "Gent", "postalCode": "9000"},
                             "geo": {"latitude": 51.0, "longitude": 3.7}},
                "labels": []}
    with app.app_context():
        for ev, oid in [("A", "o1"), ("B", "o2"), ("C", "o3")]:
            uit_sync.upsert_event(uit_sync.normalise(mk(ev, "Cultuurhuis", oid)))
            db.session.commit()
        assert Organizer.query.count() == 3
        assert len({o.slug for o in Organizer.query.all()}) == 3   # allemaal uniek
        assert Event.query.count() == 3                            # niets overgeslagen
