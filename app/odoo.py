"""Odoo-koppeling voor Partner-facturatie (Peppol-conform via Odoo).

Ravot maakt zelf GEEN facturen (een PDF is niet Peppol-conform): bij een
betaalde Partner-betaling zet Ravot via JSON-RPC een verkoopfactuur klaar in
je Odoo-boekhouding. Odoo doet de rest: nummering, btw, boeking en het
versturen als UBL over het Peppol-netwerk (gecertificeerd access point).

Standaard komt de factuur als CONCEPT (instelling odoo_factuur_auto=0), zodat
je de klantgegevens kunt nakijken vóór iets het Peppol-netwerk op gaat.
"""
import re

import requests
from flask import current_app

TIMEOUT = 25


def _cfg():
    c = current_app.config
    return (c.get("ODOO_URL") or "", c.get("ODOO_DB") or "",
            c.get("ODOO_USER") or "", c.get("ODOO_API_KEY") or "")


def actief():
    return all(_cfg())


def _rpc(payload, http_post=None):
    url, *_ = _cfg()
    post = http_post or requests.post
    r = post(f"{url.rstrip('/')}/jsonrpc", json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise RuntimeError(str(data["error"])[:300])
    return data.get("result")


def _login(http_post=None):
    url, db_, user, key = _cfg()
    uid = _rpc({"jsonrpc": "2.0", "method": "call",
                "params": {"service": "common", "method": "authenticate",
                           "args": [db_, user, key, {}]}}, http_post)
    if not uid:
        raise RuntimeError("Odoo-login geweigerd (controleer gebruiker/API-key).")
    return uid


def _execute(uid, model, method, args, kwargs=None, http_post=None):
    _, db_, _, key = _cfg()
    return _rpc({"jsonrpc": "2.0", "method": "call",
                 "params": {"service": "object", "method": "execute_kw",
                            "args": [db_, uid, key, model, method,
                                     args, kwargs or {}]}}, http_post)


def _norm_btw(btw):
    return re.sub(r"[^A-Z0-9]", "", (btw or "").upper())


def _vind_of_maak_klant(uid, operator, http_post=None):
    """Zoek de klant op btw-nummer (dé sleutel voor B2B/Peppol); anders aanmaken."""
    vat = _norm_btw(operator.btw_nummer)
    ids = _execute(uid, "res.partner", "search",
                   [[["vat", "=", vat]]], {"limit": 1}, http_post)
    if ids:
        return ids[0]
    return _execute(uid, "res.partner", "create", [{
        "name": operator.bedrijfsnaam or operator.email,
        "vat": vat,
        "email": operator.email,
        "street": operator.straat or "",
        "zip": operator.postcode or "",
        "city": operator.gemeente or "",
        "is_company": True,
    }], None, http_post)


def maak_factuur(payment, http_post=None):
    """Maak in Odoo een verkoopfactuur voor deze betaalde Partner-betaling.
    Geeft (invoice_id, referentie) terug. Idempotent afgedwongen door de caller
    (enkel aanroepen als payment.odoo_invoice_id nog leeg is)."""
    from .models import get_setting, get_bool
    from .mollie import prijs, btw_pct
    uid = _login(http_post)
    klant_id = _vind_of_maak_klant(uid, payment.operator, http_post)

    plan_label = "jaar" if payment.plan == "jaar" else "maand"
    zaak = payment.event.title if payment.event else f"fiche #{payment.event_id}"
    regel = {
        "name": f"Ravot Partner ({plan_label}) — {zaak}",
        "quantity": 1,
        "price_unit": float(prijs(payment.plan)),   # excl. btw; Odoo rekent btw
    }
    try:
        product_id = int(get_setting("odoo_product_id") or 0)
    except ValueError:
        product_id = 0
    if product_id:
        regel["product_id"] = product_id            # product draagt de 21%-btw-config

    invoice_id = _execute(uid, "account.move", "create", [{
        "move_type": "out_invoice",
        "partner_id": klant_id,
        "invoice_origin": f"Ravot betaling #{payment.id} ({payment.mollie_id})",
        "invoice_line_ids": [(0, 0, regel)],
    }], None, http_post)

    ref = "CONCEPT"
    if get_bool("odoo_factuur_auto"):
        _execute(uid, "account.move", "action_post", [[invoice_id]], None, http_post)
        gelezen = _execute(uid, "account.move", "read",
                           [[invoice_id], ["name"]], None, http_post)
        if gelezen:
            ref = gelezen[0].get("name") or "GEBOEKT"
    return invoice_id, ref


def factureer_betaling(payment, http_post=None):
    """Veilige wrapper: maakt de factuur hoogstens één keer, faalt stil
    (partner-activatie mag nooit sneuvelen op een boekhoudfout)."""
    from .extensions import db
    if not actief() or payment.odoo_invoice_id:
        return False
    try:
        invoice_id, ref = maak_factuur(payment, http_post=http_post)
        payment.odoo_invoice_id = invoice_id
        payment.odoo_invoice_ref = ref
        db.session.commit()
        return True
    except Exception as exc:
        current_app.logger.warning("odoo-facturatie faalde voor betaling %s: %s",
                                   payment.id, str(exc)[:200])
        db.session.rollback()
        return False
