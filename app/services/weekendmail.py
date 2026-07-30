"""De donderdagmail (strategienota §3.3) — retentiekanaal, geen kern.

- Top 5 op maat via dezelfde scoringsengine als de app.
- One-click uitschrijflink (signed, geen login) + List-Unsubscribe header.
- Uitschrijven raakt het account nooit.
"""
from datetime import datetime, timedelta

from flask import current_app, render_template, url_for
from itsdangerous import URLSafeSerializer

from ..extensions import db
from ..models import Family, Event, Interaction, PostcodeCentroid
from ..scoring import Profile, score_event
from ..pricing import family_price


def unsubscribe_token(family_id, kind="newsletter"):
    s = URLSafeSerializer(current_app.config["SECRET_KEY"], salt="unsub")
    return s.dumps({"f": family_id, "k": kind})


def parse_unsubscribe_token(token):
    s = URLSafeSerializer(current_app.config["SECRET_KEY"], salt="unsub")
    try:
        return s.loads(token)
    except Exception:
        return None


def weekend_range(now=None):
    now = now or datetime.utcnow()
    days_to_sat = (5 - now.weekday()) % 7
    sat = (now + timedelta(days=days_to_sat)).replace(hour=0, minute=0, second=0, microsecond=0)
    return sat, sat + timedelta(days=2)


def top_events_for(family, limit=5):
    sat, end = weekend_range()
    centroid = db.session.get(PostcodeCentroid, family.postcode)
    profile = Profile(
        child_ages=family.child_ages(),
        lat=centroid.lat if centroid else None,
        lng=centroid.lng if centroid else None,
        radius_km=family.radius_km,
        budget_pref=family.budget_pref,
        interest_weights={i.category: i.weight for i in family.interests},
    )
    from ..routes.public import bron_filter
    candidates = bron_filter(Event.query).filter(
        Event.start >= sat, Event.start < end,
        Event.hidden.is_(False), Event.pending.is_(False)).all()
    scored = [(score_event(e, profile), e) for e in candidates]
    scored = [t for t in scored if t[0] > 0]
    scored.sort(key=lambda t: t[0], reverse=True)
    # Te weinig gedateerde weekend-events (bv. zolang de UiT-laag verborgen
    # is)? Vul aan met de beste permanente plekken in de buurt — zelfde
    # terugval als de Vandaag/Weekend-feed, zodat de mail nooit leeg is.
    if len(scored) < limit:
        al = {e.id for _, e in scored}
        perm = bron_filter(Event.query).filter(
            Event.is_permanent.is_(True), Event.hidden.is_(False),
            Event.pending.is_(False)).limit(2000).all()
        perm_scored = [(score_event(e, profile), e) for e in perm
                       if e.id not in al]
        perm_scored = [t for t in perm_scored if t[0] > 0]
        perm_scored.sort(key=lambda t: t[0], reverse=True)
        scored += perm_scored[:limit - len(scored)]
    out = []
    for _, e in scored[:limit]:
        total, _free = family_price(e.price_info, profile.child_ages)
        out.append({"event": e, "family_total": total})
    return out


def bouw_weekendmail(family):
    """Bouw de weekendmail voor één gezin: (html, text, picks) of (None, None, []).
    Gedeeld door de echte verzending én de redactie-preview, zodat wat je in
    de preview ziet exact is wat er vertrekt."""
    picks = top_events_for(family)
    if not picks:
        return None, None, []
    token = unsubscribe_token(family.id)
    unsub_url = current_app.config["SITE_URL"] + url_for("auth.unsubscribe", token=token)
    # Vers van de blog: het recentste artikel meesturen (blog ↔ nieuwsbrief).
    from ..models import Artikel
    blogartikel = (Artikel.query.filter_by(gepubliceerd=True)
                   .order_by(Artikel.publicatie_datum.desc()).first())
    html = render_template("mail/weekendmail.html", family=family, picks=picks,
                           blogartikel=blogartikel,
                           unsub_url=unsub_url, site=current_app.config["SITE_URL"])
    text = "\n".join(
        f"- {p['event'].title} ({p['event'].gemeente})" for p in picks
    ) + f"\n\nUitschrijven (account blijft bestaan): {unsub_url}"
    return html, text, picks


def send_weekend_mail(family, mailer):
    html, text, picks = bouw_weekendmail(family)
    if not picks:
        return False
    token = unsubscribe_token(family.id)
    unsub_url = current_app.config["SITE_URL"] + url_for("auth.unsubscribe", token=token)
    # Hoofdadres + bevestigde gezinsleden die de mails aan hebben staan.
    adressen = [family.email] + [m.email for m in family.members
                                 if m.bevestigd and m.mail_aan]
    for adres in adressen:
        mailer(
            adres,
            "Jullie weekend, geregeld — 5 Ravot-tips",
            html, text,
            headers={
                "List-Unsubscribe": f"<{unsub_url}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            },
        )
    db.session.add(Interaction(family_id=family.id, type="mail_sent", meta={"n": len(picks)}))
    db.session.commit()
    return True


def send_all(mailer):
    """Weekendmail naar alle opt-in-gezinnen. Idempotent per run: wie de
    voorbije 3 dagen al een weekendmail kreeg (Interaction 'mail_sent'),
    wordt overgeslagen — een dubbel gestarte cron mailt dus niemand dubbel."""
    from datetime import datetime, timedelta
    from ..models import Interaction
    grens = datetime.utcnow() - timedelta(days=3)
    al_gemaild = {fid for (fid,) in
                  db.session.query(Interaction.family_id)
                  .filter(Interaction.type == "mail_sent",
                          Interaction.created_at >= grens,
                          Interaction.family_id.isnot(None)).all()}
    n = 0
    for fam in Family.query.filter_by(newsletter_opt_in=True).all():
        if fam.id in al_gemaild:
            continue
        if send_weekend_mail(fam, mailer):
            n += 1
    return n
