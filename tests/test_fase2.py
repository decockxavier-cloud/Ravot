"""Fase 2/3: maandagmail, vakantiemodus, ICS-export, weerbonus."""
from datetime import datetime, timedelta, date

from app.extensions import db
from app.models import SavedEvent, Review
from app.services.maandagmail import te_scoren_events, afgelopen_weekend
from app.vakantie import actieve_vakantie, komende_vakantie, is_kamp, vakantiecontext


def test_maandagmail_alleen_bewaarde_weekend_events(client, seed):
    """Alleen events die dit weekend vielen én bewaard zijn, zonder review."""
    fam = seed["fam_a"]
    ev_weekend = seed["events"][0]
    # zet het event in het afgelopen weekend
    sat, maandag = afgelopen_weekend(datetime(2026, 7, 6))  # een maandag
    ev_weekend.start = sat + timedelta(hours=5)
    ev_weekend.end = sat + timedelta(hours=8)
    db.session.add(SavedEvent(family_id=fam.id, event_id=ev_weekend.id))
    db.session.commit()
    events = te_scoren_events(fam, now=datetime(2026, 7, 6))
    assert ev_weekend in events
    # na een review verdwijnt hij uit de vraag
    db.session.add(Review(family_id=fam.id, event_id=ev_weekend.id,
                          kid_score=5, parent_score=3, child_ages=[4, 7]))
    db.session.commit()
    assert ev_weekend not in te_scoren_events(fam, now=datetime(2026, 7, 6))


def test_vakantie_detectie():
    # zomervakantie 2026 loopt 1 juli - 31 aug
    assert actieve_vakantie(date(2026, 7, 15)) == "zomervakantie"
    assert actieve_vakantie(date(2026, 6, 1)) is None
    # krokusvakantie 2026 start 16 feb → 10 feb valt binnen 'komende'
    komt = komende_vakantie(date(2026, 2, 10))
    assert komt and komt[0] == "krokusvakantie"


def test_vakantiecontext_banner():
    ctx = vakantiecontext(date(2026, 7, 15))
    assert ctx["actief"] and "zomervakantie" in ctx["banner"]


def test_is_kamp():
    class E:
        title = "Sportkamp zomer"
        start = end = None
    assert is_kamp(E())
    class E2:
        title = "Gewone activiteit"
        start = datetime(2026, 7, 1, 10)
        end = datetime(2026, 7, 3, 16)  # meerdaags
    assert is_kamp(E2())


def test_ics_export(client, seed):
    ev = seed["events"][0]
    resp = client.get(f"/e/{ev.slug}.ics")
    assert resp.status_code == 200
    assert "text/calendar" in resp.content_type
    body = resp.get_data(as_text=True)
    assert "BEGIN:VCALENDAR" in body and "SUMMARY:" in body
    assert ev.title.split()[0] in body
