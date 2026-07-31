"""Patch 141: het AI-concept-opslaan-pad afdekken.

De productiecrash van 31/07 ontstond doordat ai_concept() het artikel zonder
slug flushte (NOT NULL op Postgres). De testomgeving heeft geen AI-backend,
waardoor dit pad nooit doorlopen werd. Deze test bootst de AI na en dekt het
volledige genereer-en-bewaar-pad, inclusief slug-uniciteit bij dubbele titels.
"""
from app.extensions import db


NEP_ANTWOORD = (
    "TITEL: Regenweer in Gent: 7 uitjes met kinderen\n"
    "SAMENVATTING: De leukste binnenuitjes voor gezinnen in Gent.\n"
    "\n"
    "## Binnenpret\n"
    "Ga naar het museum of het zwembad.\n")


def test_ai_concept_bewaart_met_slug(app, monkeypatch):
    import app.services.redactie as redactie
    monkeypatch.setattr("app.enrich._generate",
                        lambda prompt, system, max_tokens=1400: NEP_ANTWOORD)
    with app.app_context():
        a = redactie.ai_concept("Regenweer in Gent", "praktisch")
        assert a is not None
        assert a.slug == "regenweer-in-gent-7-uitjes-met-kinderen"
        assert a.titel.startswith("Regenweer in Gent")
        assert a.samenvatting.startswith("De leukste binnenuitjes")
        assert "## Binnenpret" in a.inhoud_md
        assert a.gepubliceerd is False

        # Tweede concept met dezelfde titel: slug krijgt een uniek achtervoegsel.
        b = redactie.ai_concept("Regenweer in Gent", "praktisch")
        assert b is not None and b.slug == a.slug + "-2"


def test_ai_concept_zonder_backend_geeft_none(app, monkeypatch):
    import app.services.redactie as redactie

    def kapot(prompt, system, max_tokens=1400):
        raise RuntimeError("geen backend")

    monkeypatch.setattr("app.enrich._generate", kapot)
    with app.app_context():
        assert redactie.ai_concept("Wat dan ook") is None
