"""Verjaardagsfeestjes — partners zoeken + automatische offerteaanvragen.

Werking:
- Feestpartners zijn plekken (Event) met feest=True. Voor de OFFERTEFLOW is
  bovendien een actieve Ravot Partner-status vereist (partner_until in de
  toekomst) OF een expliciet feest_contact door de beheerder — zo blijft de
  module een echt Partner-voordeel, maar kan Xavier zaken handmatig toelaten.
- Het contactadres is (in volgorde): feest_contact op de fiche, anders het
  e-mailadres van de uitbater met een goedgekeurde claim op die plek.
- De mail vertrekt vanuit Ravot met Reply-To van het gezin: antwoorden gaan
  rechtstreeks naar de ouder, Ravot zit niet tussen de conversatie.
"""
from datetime import datetime

from flask import current_app, render_template, url_for

from ..content import render_markdown
from ..extensions import db
from ..geo import postcode_coord
from ..models import (Event, FEEST_SOORTEN, MailTemplate, OperatorClaim,
                      get_int)
from ..scoring import haversine_km
from .magic import send_mail


def contact_email(event):
    """Offerte-adres van een feestpartner, of None."""
    if event.feest_contact:
        return event.feest_contact
    claim = OperatorClaim.query.filter_by(event_id=event.id,
                                          status="approved").first()
    return claim.operator.email if claim and claim.operator else None


def partner_actief(event, now=None):
    now = now or datetime.utcnow()
    return bool(event.partner_until and event.partner_until > now)


def zoek_partners(postcode, straal_km=None, soorten=None):
    """Feestpartners binnen de straal, gesorteerd: actieve Partners eerst,
    dan op afstand. Retourneert [{event, km, soorten, contact}]."""
    straal = straal_km or get_int("feest_straal_km", 20) or 20
    centrum = postcode_coord(postcode) if postcode else None
    q = Event.query.filter(Event.feest.is_(True), Event.hidden.is_(False),
                           Event.pending.is_(False))
    rows = []
    for ev in q.limit(500).all():
        mail = contact_email(ev)
        if not mail:
            continue          # zonder contactadres kan er geen offerte vertrekken
        ev_soorten = [s for s in (ev.feest_soorten or []) if s in FEEST_SOORTEN]
        if soorten and not (set(soorten) & set(ev_soorten)):
            continue
        km = None
        if centrum and ev.lat is not None:
            km = haversine_km(centrum[0], centrum[1], ev.lat, ev.lng)
            if km > straal:
                continue
        rows.append({"event": ev, "km": round(km, 1) if km is not None else None,
                     "soorten": ev_soorten, "contact": mail,
                     "partner": partner_actief(ev)})
    rows.sort(key=lambda r: (not r["partner"], r["km"] if r["km"] is not None else 999))
    return rows


def stuur_offerte(feestje, event, family):
    """Eén offertemail naar één partner. Retourneert True bij verzending."""
    mail = contact_email(event)
    if not mail:
        return False
    from ..models import FEEST_AANLEIDINGEN
    aanleiding = FEEST_AANLEIDINGEN.get(
        getattr(feestje, "aanleiding", None) or "verjaardag",
        FEEST_AANLEIDINGEN["verjaardag"])[1].lower()
    mt = db.session.get(MailTemplate, "feestje_offerte")
    onderwerp = (mt.onderwerp if mt and mt.onderwerp else
                 "Offerteaanvraag {aanleiding} via Ravot ({datum})")
    inhoud = (mt.inhoud_md if mt and (mt.inhoud_md or "").strip() else
              _STANDAARD_MAIL)
    velden = {
        "plek": event.title,
        "datum": feestje.datum.strftime("%d/%m/%Y"),
        "leeftijd": str(feestje.leeftijd or "?"),
        "aantal": str(feestje.aantal_kinderen),
        "gemeente": feestje.gemeente or feestje.postcode or "",
        "budget": feestje.budget or "geen voorkeur opgegeven",
        "wensen": feestje.wensen or "geen bijzondere wensen",
        "aanleiding": aanleiding,
    }
    for k, v in velden.items():
        onderwerp = onderwerp.replace("{%s}" % k, v)
        inhoud = inhoud.replace("{%s}" % k, v)
    html = render_template("mail/feestje_offerte.html",
                           inhoud_html=render_markdown(inhoud),
                           plek=event.title)
    return send_mail(mail, onderwerp, html, text=inhoud,
                     headers={"Reply-To": family.email})


_STANDAARD_MAIL = """Dag {plek},

Een gezin uit de buurt van {gemeente} plant via **Ravot.be** een
{aanleiding} en vraagt jullie graag een vrijblijvende offerte.

- **Datum:** {datum}
- **Feestvarken:** het kind wordt/is {leeftijd} jaar
- **Aantal kinderen:** {aantal}
- **Budget (indicatie):** {budget}
- **Wensen:** {wensen}

Antwoord gewoon op deze mail — je antwoord komt rechtstreeks bij het gezin
terecht.

Veel groetjes van het vosje 🦊
Ravot.be — dé uitstap-gids voor Vlaamse gezinnen
"""
