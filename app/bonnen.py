"""Cadeaubonnen (patch 174).

Een bon vervangt een fysieke goodie: geen verzending, geen voorraadgedoe.
Bij inwisseling genereren we een unieke code, sturen het gezin een mail in
huisstijl (met het logo van Ravot én van de webshop) en verwittigen we de
webshop zodat die de code kan aanmaken. De bon is bewust pas na 24 uur
geldig — dat is de verwerkingstijd bij de webshop — en daarna één jaar.
"""
import secrets
from datetime import timedelta

from flask import current_app, url_for

from .extensions import db
from .models import Inwissel, utcnow
from .services.magic import send_mail

VERWERKING_UREN = 24
GELDIG_MAANDEN = 12
ALFABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # zonder I/O/0/1: voorleesbaar


def maak_code(prefix="RAVOT", lengte=8):
    """Unieke bonnencode van minstens 8 tekens, met dubbelcontrole tegen de
    databank. Verwarrende tekens (I/O/0/1) zitten niet in het alfabet."""
    for _ in range(50):
        kern = "".join(secrets.choice(ALFABET) for _ in range(lengte))
        code = f"{prefix}-{kern}"
        if not Inwissel.query.filter_by(code=code).first():
            return code
    # Extreem onwaarschijnlijk; dan maar langer
    return f"{prefix}-{secrets.token_hex(8).upper()}"


def _logo_url(bestandsnaam):
    basis = current_app.config["SITE_URL"].rstrip("/")
    return f"{basis}/static/img/{bestandsnaam}"


def _bon_html(fam, beloning, inwissel):
    site = current_app.config["SITE_URL"].rstrip("/")
    winkel = beloning.bon_winkel or "onze webshop"
    winkel_url = beloning.bon_url or site
    vanaf = inwissel.geldig_vanaf.strftime("%d/%m/%Y om %H:%M")
    tot = inwissel.geldig_tot.strftime("%d/%m/%Y")
    logo_winkel = (f'<img src="{_logo_url(beloning.bon_logo)}" alt="{winkel}" '
                   f'height="46" style="height:46px">'
                   if beloning.bon_logo else f"<strong>{winkel}</strong>")
    return f"""\
<div style="font-family:ui-rounded,'Nunito',Arial,sans-serif;background:#FAF7F0;
     padding:22px;color:#1F3A2A">
  <div style="max-width:520px;margin:0 auto;background:#fff;border-radius:18px;
       overflow:hidden;box-shadow:0 6px 20px rgba(0,0,0,.08)">
    <div style="background:#4CA362;padding:16px 20px;text-align:center">
      <img src="{_logo_url('vosje.png')}" alt="Ravot" height="42"
           style="height:42px;vertical-align:middle">
      <span style="color:#fff;font-size:22px;font-weight:800;
            vertical-align:middle;margin-left:8px">Ravot</span>
    </div>
    <div style="padding:22px 20px;text-align:center">
      <p style="margin:0 0 6px;font-size:15px">Hoera! Jullie ravotpunten zijn
        omgezet in een cadeaubon 🎉</p>
      <h1 style="margin:6px 0 2px;font-size:26px">{beloning.naam}</h1>
      <p style="margin:0;color:#5d6b57">{beloning.beschrijving or ''}</p>

      <div style="margin:20px 0;padding:18px;border:2px dashed #EE8035;
           border-radius:14px;background:#FFF7EC">
        <div style="font-size:12px;letter-spacing:.08em;color:#8a8272">JOUW CODE</div>
        <div style="font-size:27px;font-weight:800;letter-spacing:.06em;
             color:#EE8035;margin:6px 0">{inwissel.code}</div>
        <div style="font-size:13px;color:#5d6b57">
          t.w.v. &euro;{beloning.waarde_eur:.2f}</div>
      </div>

      <div style="margin:16px 0">{logo_winkel}</div>
      <p style="margin:0 0 14px;font-size:14px">
        In te wisselen op <a href="{winkel_url}"
        style="color:#2E7D46;font-weight:700">{winkel}</a>.</p>
      <a href="{winkel_url}" style="display:inline-block;background:#EE8035;
         color:#fff;text-decoration:none;font-weight:700;padding:12px 22px;
         border-radius:999px">Naar de webshop</a>

      <div style="margin-top:22px;padding-top:14px;border-top:1px solid #E7E0D2;
           font-size:12.5px;color:#77836f;text-align:left">
        <p style="margin:4px 0"><strong>Belangrijk:</strong> je code wordt
          klaargezet bij de webshop en is geldig <strong>vanaf {vanaf}</strong>
          (24 uur verwerkingstijd).</p>
        <p style="margin:4px 0">Geldig tot <strong>{tot}</strong> — één jaar.</p>
        <p style="margin:4px 0">Eén bon per bestelling, niet combineerbaar met
          andere bonnen. Niet inwisselbaar tegen geld.</p>
      </div>
    </div>
  </div>
  <p style="text-align:center;color:#8a8272;font-size:12px;margin:14px 0 0">
    Deze mail hoort bij jullie Ravotpas · <a href="{site}"
    style="color:#77836f">ravot.be</a></p>
</div>"""


def _bon_tekst(fam, beloning, inwissel):
    return (f"Jullie cadeaubon: {inwissel.code}\n"
            f"{beloning.naam} — t.w.v. EUR {beloning.waarde_eur:.2f}\n"
            f"In te wisselen op {beloning.bon_url or ''}\n"
            f"Geldig vanaf {inwissel.geldig_vanaf.strftime('%d/%m/%Y %H:%M')} "
            f"(24 u verwerkingstijd) tot "
            f"{inwissel.geldig_tot.strftime('%d/%m/%Y')}.\n"
            "Eén bon per bestelling, niet combineerbaar.")


def verwerk_bon(fam, beloning, inwissel):
    """Zet het geldigheidsvenster, mailt het gezin en verwittigt de webshop.
    Faalt nooit hard: de inwisseling zelf is al opgeslagen."""
    inwissel.geldig_vanaf = utcnow() + timedelta(hours=VERWERKING_UREN)
    inwissel.geldig_tot = inwissel.geldig_vanaf + timedelta(days=365)
    db.session.commit()

    try:
        send_mail(fam.email, f"Jullie cadeaubon: {inwissel.code} 🦊",
                  _bon_html(fam, beloning, inwissel),
                  _bon_tekst(fam, beloning, inwissel))
    except Exception:
        current_app.logger.exception("Bonmail naar gezin mislukt (%s)", inwissel.code)

    if beloning.bon_mail:
        vanaf = inwissel.geldig_vanaf.strftime("%d/%m/%Y %H:%M")
        tot = inwissel.geldig_tot.strftime("%d/%m/%Y")
        html = (f"<p>Nieuwe Ravot-cadeaubon aan te maken in de webshop:</p>"
                f"<ul><li><strong>Code:</strong> {inwissel.code}</li>"
                f"<li><strong>Waarde:</strong> &euro;{beloning.waarde_eur:.2f}</li>"
                f"<li><strong>Geldig vanaf:</strong> {vanaf} (24 u)</li>"
                f"<li><strong>Geldig tot:</strong> {tot}</li>"
                f"<li><strong>Voorwaarde:</strong> één bon per bestelling, "
                f"niet combineerbaar</li></ul>"
                f"<p>Het gezin kreeg deze code al toegestuurd met de melding dat "
                f"ze na 24 uur bruikbaar is.</p>")
        try:
            send_mail(beloning.bon_mail,
                      f"[Ravot] Cadeaubon aanmaken: {inwissel.code} "
                      f"(€{beloning.waarde_eur:.2f})", html)
        except Exception:
            current_app.logger.exception("Bonmail naar webshop mislukt (%s)",
                                         inwissel.code)
