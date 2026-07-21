"""Mollie-betalingen voor Ravot Partner.

Beveiligingsmodel (Mollie-standaard): de webhook bevat enkel een betaal-id.
We vertrouwen NOOIT de webhook-body; we halen de status altijd zelf op bij
Mollie met onze API-key. Enkel wat Mollie zelf bevestigt, telt.

Prijzen staan in instellingen zodat je ze zonder deploy kunt aanpassen.
"""
from datetime import datetime, timedelta

import requests
from flask import current_app, url_for

MOLLIE_API = "https://api.mollie.com/v2"

PLAN_DAGEN = {"maand": 31, "jaar": 366, "founding": 366}


def _key():
    return current_app.config.get("MOLLIE_API_KEY") or ""


def actief():
    return bool(_key())


def prijs(plan):
    from .models import get_setting
    # Enkel jaarabonnement. 'maand' bestaat historisch nog in oude records maar
    # wordt niet meer aangeboden; alles valt terug op de jaarprijs.
    return (get_setting("partner_prijs_jaar") or "100.00").strip()


def btw_pct():
    from .models import get_setting
    try:
        return float(get_setting("partner_btw_pct") or "21")
    except ValueError:
        return 21.0


def prijs_incl(plan):
    """Prijs inclusief btw — wat Mollie effectief aanrekent."""
    excl = float(prijs(plan))
    return f"{excl * (1 + btw_pct() / 100):.2f}"


def start_betaling(payment, http_post=None):
    """Maak de betaling aan bij Mollie en geef de checkout-URL terug.
    'payment' is een reeds bewaard PartnerPayment (voor het interne id)."""
    post = http_post or requests.post
    r = post(f"{MOLLIE_API}/payments",
             headers={"Authorization": f"Bearer {_key()}"},
             json={
                 "amount": {"currency": "EUR", "value": payment.amount},
                 "description": f"Ravot Partner ({payment.plan}) — fiche #{payment.event_id}",
                 "redirectUrl": url_for("uitbater.partner_klaar",
                                        pid=payment.id, _external=True),
                 "webhookUrl": url_for("uitbater.mollie_webhook", _external=True),
                 "metadata": {"partner_payment_id": payment.id},
             }, timeout=20)
    r.raise_for_status()
    data = r.json()
    payment.mollie_id = data["id"]
    return (data.get("_links") or {}).get("checkout", {}).get("href")


def haal_status_op(mollie_id, http_get=None):
    """Vraag de status van een betaling op BIJ MOLLIE ZELF (de verificatie)."""
    get = http_get or requests.get
    r = get(f"{MOLLIE_API}/payments/{mollie_id}",
            headers={"Authorization": f"Bearer {_key()}"}, timeout=20)
    r.raise_for_status()
    return r.json()


def verwerk_webhook(mollie_id, http_get=None):
    """Webhook-verwerking: status ophalen bij Mollie en pas dan toepassen.
    Idempotent: een al-verwerkte betaling wordt niet dubbel geteld."""
    from .extensions import db
    from .models import PartnerPayment
    p = PartnerPayment.query.filter_by(mollie_id=mollie_id).first()
    if not p:
        return False               # onbekend id: negeren (niets aannemen)
    data = haal_status_op(mollie_id, http_get=http_get)
    status = data.get("status", "open")
    p.status = status
    net_betaald = status == "paid" and p.paid_at is None
    if net_betaald:
        p.paid_at = datetime.utcnow()
        if p.event:
            basis = p.event.partner_until or datetime.utcnow()
            if basis < datetime.utcnow():
                basis = datetime.utcnow()
            p.event.partner_until = basis + timedelta(days=PLAN_DAGEN.get(p.plan, 31))
    db.session.commit()
    if net_betaald:
        # Peppol-conforme factuur klaarzetten in Odoo (faalt stil; activatie
        # van Partner mag nooit sneuvelen op een boekhoudfout).
        from .odoo import factureer_betaling
        factureer_betaling(p)
    return True


def is_partner(event, now=None):
    now = now or datetime.utcnow()
    return bool(event and event.partner_until and event.partner_until > now)
