"""Patch 209: fiche-UX — 'Leuk onderweg' per groep, bingo bij Praktisch,
gezinsfoto als kopbeeld en vergrootbare foto's."""
from app.extensions import db
from app.models import Event, Family, FietsRoute, Photo, RouteBuurt


def test_onderweg_gesplitst_per_groep(client, app):
    with app.app_context():
        r = FietsRoute(titel="Lus", slug="ux-og", afstand_km=20, duur_min=120,
                       moeilijkheid="vlak", is_lus=True, pending=False,
                       hidden=False, gemeente="Gent", start_lat=51.05,
                       start_lng=3.72, gpx_bestand="x.gpx",
                       geometrie=[[51.05, 3.72], [51.06, 3.73]])
        db.session.add(r)
        db.session.flush()
        for n in range(9):
            st = "horeca" if n < 6 else ("playground" if n < 8 else "museum")
            ev = Event(title=f"Plek {n}", slug=f"ux-og{n}", source="osm",
                       ext_id=f"ux-og{n}", is_permanent=True, pending=False,
                       hidden=False, lat=51.05, lng=3.72, subtype=st,
                       indoor=st != "playground",
                       categories=["buiten"] if st == "playground" else [])
            db.session.add(ev)
            db.session.flush()
            db.session.add(RouteBuurt(route_id=r.id, event_id=ev.id,
                                      afstand_m=100 + n, route_km=n * 2))
        db.session.commit()
    h = client.get("/fietsroutes/ux-og").get_data(as_text=True)
    assert "🛝 Ravotten" in h and "🍦 Smullen" in h and "🎭 Beleven" in h
    assert "Toon alle 6" in h                  # lange horecalijst ingeklapt
    assert "onderweg-regel" in h               # compacte regels
    # bingo hoort bij Praktisch, niet bovenaan
    assert h.index("Praktisch") < h.index("Fietsbingo") < h.index("Leuk onderweg")


def test_gezinsfoto_vervangt_pictogram_en_is_vergrootbaar(client, app):
    with app.app_context():
        ev = Event(title="Zonder eigen foto", slug="ux-f1", source="user",
                   ext_id="ux-f1", is_permanent=True, pending=False,
                   hidden=False, lat=51.0, lng=3.5, subtype="playground",
                   categories=["buiten"])
        fam = Family(email="ux209@t.be", postcode="8800")
        db.session.add_all([ev, fam])
        db.session.flush()
        db.session.add(Photo(event_id=ev.id, family_id=fam.id, soort="gezin",
                             filename="g.jpg", status="approved"))
        db.session.commit()
    h = client.get("/e/ux-f1").get_data(as_text=True)
    kop = h.split("Voor de kinderen")[0] if "Voor de kinderen" in h else h
    assert "fiche-beeld-illustratie" not in kop      # geen pictogram meer
    assert "door een gezin" in kop                   # eerlijk gelabeld
    galerij = h.split("Foto's van gezinnen")[1][:500]
    assert "data-lightbox" in galerij                # vergrootbaar


def test_pending_gezinsfoto_telt_niet_als_kopbeeld(client, app):
    with app.app_context():
        ev = Event(title="Kaal", slug="ux-f2", source="user", ext_id="ux-f2",
                   is_permanent=True, pending=False, hidden=False, lat=51.0,
                   lng=3.5, subtype="playground", categories=["buiten"])
        fam = Family(email="ux209b@t.be", postcode="8800")
        db.session.add_all([ev, fam])
        db.session.flush()
        db.session.add(Photo(event_id=ev.id, family_id=fam.id, soort="gezin",
                             filename="h.jpg", status="pending"))
        db.session.commit()
    h = client.get("/e/ux-f2").get_data(as_text=True)
    assert "fiche-beeld-illustratie" in h            # moderatie eerst
