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
    """Zoek de klant op btw-nummer (dé sleutel voor B2B/Peppol); anders aanmaken.
    Geeft (klant_id, naam_waarschuwing) terug: wijkt de bestaande Odoo-naam af
    van wat de partner opgaf, dan waarschuwen we — nooit stil een bestaande
    boekhoudklant hergebruiken die eigenlijk iemand anders is (het
    'Dronedepot'-geval: verkeerd btw-nummer op een oude klant)."""
    vat = _norm_btw(operator.btw_nummer)
    ids = _execute(uid, "res.partner", "search",
                   [[["vat", "=", vat]]], {"limit": 1}, http_post)
    if ids:
        waarschuwing = None
        try:
            rij = _execute(uid, "res.partner", "read",
                           [ids, ["name"]], None, http_post)
            odoo_naam = (rij[0].get("name") or "").strip() if rij else ""
            opgegeven = (operator.bedrijfsnaam or "").strip()
            if odoo_naam and opgegeven and odoo_naam.lower() != opgegeven.lower():
                waarschuwing = (f"Odoo-klant heet '{odoo_naam}', partner gaf "
                                f"'{opgegeven}' op — controleer of dit btw-nummer "
                                f"bij de juiste klant staat")
        except Exception:
            pass
        return ids[0], waarschuwing
    return _execute(uid, "res.partner", "create", [{
        "name": operator.bedrijfsnaam or operator.email,
        "vat": vat,
        "email": (operator.factuur_email or operator.email),
        "phone": operator.telefoon or "",
        "street": operator.straat or "",
        "zip": operator.postcode or "",
        "city": operator.gemeente or "",
        "is_company": True,
    }], None, http_post), None


def maak_factuur(payment, http_post=None):
    """Maak in Odoo een verkoopfactuur voor deze betaalde Partner-betaling.
    Geeft (invoice_id, referentie) terug. Idempotent afgedwongen door de caller
    (enkel aanroepen als payment.odoo_invoice_id nog leeg is)."""
    from .models import get_setting, get_bool
    from .mollie import prijs, btw_pct
    uid = _login(http_post)
    klant_id, naam_waarschuwing = _vind_of_maak_klant(uid, payment.operator, http_post)

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

    move = {
        "move_type": "out_invoice",
        "partner_id": klant_id,
        "invoice_origin": f"Ravot betaling #{payment.id} ({payment.mollie_id})",
        "invoice_line_ids": [(0, 0, regel)],
    }
    # Vast dagboek (bv. "Verkopen Ravot"): zonder dit pakt Odoo zijn
    # standaard-verkoopdagboek — dat van K'Bouter, dus verkeerd geboekt.
    try:
        journal_id = int(get_setting("odoo_journal_id") or 0)
    except ValueError:
        journal_id = 0
    if journal_id:
        move["journal_id"] = journal_id
    if naam_waarschuwing:
        from flask import current_app
        current_app.logger.warning("Odoo-facturatie: %s", naam_waarschuwing)
        move["narration"] = f"⚠️ {naam_waarschuwing}"
    invoice_id = _execute(uid, "account.move", "create", [move], None, http_post)

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


def ververs_refs(betalingen, http_post=None):
    """Haal voor betalingen met een Odoo-factuur de actuele status op.
    Geboekt in Odoo? Dan vervangt het echte factuurnummer (bv.
    Ravot/25-26/0001) het bewaarde 'CONCEPT'. Geeft het aantal bijgewerkte
    rijen terug. Leest alleen — wijzigt niets in Odoo."""
    from .extensions import db
    te_doen = [b for b in betalingen
               if b.odoo_invoice_id and (b.odoo_invoice_ref or "") == "CONCEPT"]
    if not te_doen:
        return 0
    uid = _login(http_post)
    ids = [int(b.odoo_invoice_id) for b in te_doen]
    rijen = _execute(uid, "account.move", "read",
                     [ids, ["name", "state"]], None, http_post) or []
    per_id = {r["id"]: r for r in rijen}
    n = 0
    for b in te_doen:
        r = per_id.get(int(b.odoo_invoice_id))
        if r and r.get("state") == "posted" and r.get("name"):
            b.odoo_invoice_ref = r["name"]
            n += 1
    if n:
        db.session.commit()
    return n
