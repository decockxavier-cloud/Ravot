"""Standaardteksten voor bewerkbare mails en inhoudspagina's.

Deze worden bij migrate-db ingeladen ALS ze nog niet bestaan. Zo staan de
admin-velden niet blanco en heb je meteen een vertrekpunt. Bewerk je ze in de
admin, dan blijft jouw versie staan (we overschrijven nooit bestaande teksten).
"""
from .extensions import db
from .models import MailTemplate, ContentPage

# ---- Mailteksten (de tekst rond de dynamische delen) ----
MAIL_SEED = {
    "inlogcode": {
        "onderwerp": "Jouw Ravot-inlogcode",
        "inhoud_md": (
            "## Jouw inlogcode\n\n"
            "Typ deze code in op de Ravot-website om je aan te melden:\n\n"
            "**{code}**\n\n"
            "De code is 15 minuten geldig en werkt één keer. "
            "Heb je dit niet aangevraagd? Dan mag je deze mail gewoon negeren."
        ),
    },
    "weekend": {
        "onderwerp": "Jullie weekend, geregeld 🎈",
        "inhoud_md": (
            "## Jullie weekend, geregeld 🎈\n\n"
            "Dag {naam}! Dit past dit weekend bij jullie gezin. "
            "Hieronder vind je onze suggesties — klik door voor alle details.\n\n"
            "*(De lijst met activiteiten wordt automatisch toegevoegd.)*"
        ),
    },
    "maandag": {
        "onderwerp": "Zijn jullie geweest? 😊",
        "inhoud_md": (
            "## Zijn jullie geweest? 😊\n\n"
            "Dag {naam}! Jullie hadden dit weekend iets bewaard. Hoe was het? "
            "Eén tik en jullie helpen andere gezinnen kiezen — helemaal anoniem.\n\n"
            "*(De lijst met bewaarde activiteiten wordt automatisch toegevoegd.)*"
        ),
    },
}


def _bedrijf():
    return {
        "naam": "YAMY BV",
        "adres": "Klaproosstraat 36, 8800 Roeselare",
        "btw": "BE 0505.624.079",
        "mail": "info@complemy.com",
    }


def seed_standaard_content():
    """Laad ontbrekende mail- en contentteksten in. Geeft aantal toegevoegd terug."""
    from .content_teksten import CONTENT_SEED
    n = 0
    for slug, data in MAIL_SEED.items():
        if db.session.get(MailTemplate, slug) is None:
            db.session.add(MailTemplate(slug=slug, naam=slug.capitalize(),
                                        onderwerp=data["onderwerp"],
                                        inhoud_md=data["inhoud_md"]))
            n += 1
    for slug, data in CONTENT_SEED.items():
        if db.session.get(ContentPage, slug) is None:
            db.session.add(ContentPage(slug=slug, titel=data["titel"],
                                       inhoud_md=data["inhoud_md"]))
            n += 1
    return n
