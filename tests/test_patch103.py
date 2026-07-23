"""Patch 103: zelfherstel van vastgelopen syncstatussen.

Een run die urenlang op "running" staat, hoort bij een gesneuveld proces
(herstart/deploy tijdens de sync). Die blokkeerde voorheen de
Synchroniseer-knop voor eeuwig.
"""
from datetime import datetime, timedelta

from app.extensions import db
from app.models import SyncStatus
from app.services.sources import is_sync_running, get_statuses


def _run(source, state, uren_oud):
    row = SyncStatus(source=source, state=state)
    db.session.add(row)
    db.session.commit()
    # updated_at expliciet terugzetten (onupdate zou hem anders verversen)
    db.session.execute(db.text(
        "UPDATE sync_status SET updated_at = :t WHERE source = :s"),
        {"t": datetime.utcnow() - timedelta(hours=uren_oud), "s": source})
    db.session.commit()
    return row


def test_oude_running_wordt_hersteld(app):
    with app.app_context():
        _run("osm", "running", uren_oud=120)   # 5 dagen "bezig"
        assert is_sync_running() is False       # geneest én blokkeert niet meer
        st = get_statuses()["osm"]
        assert st.state == "error"
        assert "automatisch hersteld" in st.last_error


def test_verse_running_blijft_gewoon_bezig(app):
    with app.app_context():
        _run("uit", "running", uren_oud=1)      # legitiem bezig
        assert is_sync_running() is True
        assert get_statuses()["uit"].state == "running"
