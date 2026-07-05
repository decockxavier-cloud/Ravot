"""De maandagvraag-mail (strategienota fase 2) — scores binnenhalen.

Op maandag krijgen gezinnen die dit voorbije weekend een activiteit hadden
bewaard, de vriendelijke vraag om een Ravotscore te geven. Dit is de motor
achter de unieke score- en echte-kostdata.

- Enkel naar gezinnen met monday_opt_in = True.
- Enkel voor bewaarde events waarvan het weekend net voorbij is én waar het
  gezin nog geen review voor gaf (geen dubbele vraag).
- One-click uitschrijven via dezelfde signed token (kind='monday').
"""
from datetime import datetime, timedelta

from flask import current_app, render_template, url_for

from ..extensions import db
from ..models import Family, SavedEvent, Review, Event
from .weekendmail import unsubscribe_token


def afgelopen_weekend(now=None):
    """(zaterdag 00:00, maandag 00:00) van het net voorbije weekend."""
    now = now or datetime.utcnow()
    # maandag = weekday 0; ga terug naar de voorbije zaterdag
    days_since_sat = (now.weekday() - 5) % 7
    sat = (now - timedelta(days=days_since_sat)).replace(hour=0, minute=0, second=0, microsecond=0)
    return sat, sat + timedelta(days=2)


def te_scoren_events(family, now=None):
    """Bewaarde events van het afgelopen weekend zonder review van dit gezin."""
    sat, maandag = afgelopen_weekend(now)
    reeds = {r.event_id for r in Review.query.filter_by(family_id=family.id)}
    uit = []
    for s in SavedEvent.query.filter_by(family_id=family.id):
        ev = s.event
        if ev and ev.start and sat <= ev.start < maandag and ev.id not in reeds:
            uit.append(ev)
    return uit


def send_monday_mail(family, mailer, now=None):
    events = te_scoren_events(family, now)
    if not events:
        return False
    token = unsubscribe_token(family.id, kind="monday")
    unsub_url = current_app.config["SITE_URL"] + url_for("auth.unsubscribe", token=token)
    site = current_app.config["SITE_URL"]
    html = render_template("mail/maandagmail.html", family=family, events=events,
                           unsub_url=unsub_url, site=site)
    text = ("Zijn jullie geweest? Geef een Ravotscore en help andere gezinnen:\n"
            + "\n".join(f"- {e.title}: {site}{url_for('account.review', event_id=e.id)}"
                        for e in events)
            + f"\n\nGeen maandagvraag meer: {unsub_url}")
    mailer(family.email, "Zijn jullie geweest? 😊 Geef een Ravotscore",
           html, text,
           headers={"List-Unsubscribe": f"<{unsub_url}>",
                    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"})
    return True


def send_all(mailer, now=None):
    n = 0
    for fam in Family.query.filter_by(monday_opt_in=True).all():
        if send_monday_mail(fam, mailer, now):
            n += 1
    return n
