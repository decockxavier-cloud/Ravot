"""Transactionele mails naar uitbaters.

Toon: warm, ondersteunend, nooit als kritiek. De feedback-mail bij herhaalde
review-signalen is uitdrukkelijk bedoeld als hulp om bij te sturen — niet als
aanval. Dat zit bewust in de bewoording.
"""
from flask import url_for

from .magic import send_mail


def _wrap(titel, body_html):
    """Eenvoudige, huisstijl-conforme HTML-wrapper."""
    return f"""\
<div style="font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
            max-width:560px;margin:0 auto;color:#2E3A2B;line-height:1.5">
  <div style="font-size:26px;font-weight:800;color:#EE8035;margin-bottom:4px">Ravot</div>
  <h1 style="font-size:20px;color:#4CA362;margin:12px 0">{titel}</h1>
  {body_html}
  <hr style="border:none;border-top:1px solid #e3e6ea;margin:22px 0 10px">
  <p style="font-size:12px;color:#999">
    Je krijgt deze mail omdat je een zaak beheert op Ravot.
    Vragen? Antwoord gerust op deze mail of schrijf naar info@ravot.be.
  </p>
</div>"""


def claim_goedgekeurd(operator_email, zaaknaam, fiche_url):
    """Bevestiging dat een claim is goedgekeurd — de uitbater kan nu beheren."""
    titel = "Je zaak is bevestigd! 🎉"
    body = f"""\
<p>Goed nieuws — <strong>{zaaknaam}</strong> is nu aan jou toegewezen op Ravot.</p>
<p>Je kunt je fiche vanaf nu beheren: de beschrijving aanvullen, je
kindvriendelijke voorzieningen aanvinken, foto's toevoegen en aangeven of je
feestjes organiseert. Hoe vollediger je fiche, hoe makkelijker gezinnen je
vinden.</p>
<p style="margin:20px 0">
  <a href="{fiche_url}" style="background:#4CA362;color:#fff;text-decoration:none;
     padding:11px 20px;border-radius:8px;font-weight:700;display:inline-block">
     Naar mijn fiche →</a>
</p>
<p>Welkom bij Ravot — we helpen graag gezinnen de weg naar jouw zaak te vinden.</p>"""
    return send_mail(operator_email, "Je zaak is bevestigd op Ravot",
                     _wrap(titel, body),
                     text=f"{zaaknaam} is aan jou toegewezen op Ravot. "
                          f"Beheer je fiche: {fiche_url}")


def zaak_toegevoegd(operator_email, zaaknaam):
    """Bevestiging dat een nieuw ingediende zaak in de wachtrij zit."""
    titel = "We hebben je zaak ontvangen 👍"
    body = f"""\
<p>Bedankt om <strong>{zaaknaam}</strong> toe te voegen aan Ravot.</p>
<p>Onze redactie kijkt je zaak na — dat is een korte kwaliteitscontrole zodat
gezinnen op Ravot altijd betrouwbare info vinden. Zodra ze is goedgekeurd, laten
we het je weten en kun je de fiche verder aanvullen.</p>"""
    return send_mail(operator_email, "We hebben je zaak ontvangen — Ravot",
                     _wrap(titel, body),
                     text=f"Bedankt om {zaaknaam} toe te voegen. Onze redactie "
                          f"kijkt ze na en laat je weten zodra ze online staat.")


def voorziening_feedback(operator_email, zaaknaam, voorziening_label,
                         fiche_url, aantal):
    """Melding wanneer meerdere gezinnen aangeven dat een voorziening ontbreekt.

    Uitdrukkelijk als HULP geformuleerd, niet als kritiek: er kan een reden
    achter zitten (slecht vindbaar, tijdelijk buiten dienst, ...) die de
    uitbater met een kleine ingreep kan oplossen.
    """
    titel = "Een tip om je fiche nog beter te maken 💡"
    body = f"""\
<p>Bij <strong>{zaaknaam}</strong> gaven de voorbije tijd {aantal} gezinnen aan
dat ze <strong>{voorziening_label}</strong> niet terugvonden, terwijl jij die
wel hebt aangevinkt.</p>
<p>Dat hoeft niets te betekenen — vaak zit er een simpele verklaring achter:
misschien is het niet goed zichtbaar, stond het net even niet klaar, of wisten
de gezinnen niet dat het er was. We geven je dit door zodat je er, als je wil,
iets mee kunt doen:</p>
<ul>
  <li>Is het duidelijk aangeduid of makkelijk te vinden in je zaak?</li>
  <li>Klopt de aanduiding op je fiche nog?</li>
  <li>Wil je het misschien in je beschrijving vermelden?</li>
</ul>
<p>Je hoeft niets te doen — beschouw het als vrijblijvende feedback die je kan
helpen om nog meer gezinnen tevreden te ontvangen.</p>
<p style="margin:20px 0">
  <a href="{fiche_url}" style="background:#4CA362;color:#fff;text-decoration:none;
     padding:11px 20px;border-radius:8px;font-weight:700;display:inline-block">
     Mijn fiche bekijken →</a>
</p>"""
    return send_mail(operator_email,
                     f"Tip voor je Ravot-fiche: {voorziening_label}",
                     _wrap(titel, body),
                     text=f"{aantal} gezinnen vonden '{voorziening_label}' niet "
                          f"terug bij {zaaknaam}. Vrijblijvende tip om je fiche "
                          f"te verfijnen: {fiche_url}")
