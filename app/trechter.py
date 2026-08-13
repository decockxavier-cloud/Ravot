"""Conversietrechter (patch 223).

Meet waar bezoekers afhaken op weg naar een gezinsprofiel. Vijf stappen:

    bezoek        -> iemand opende een fiche (echte interesse, geen bot-scroll)
    verdieping    -> drie of meer fiches in dezelfde sessie: aan het plannen
    login_gezien  -> de aanmeldpagina geopend
    code_gevraagd -> e-mailcode aangevraagd (drempel genomen)
    account       -> profiel effectief aangemaakt

Elke stap telt hoogstens één keer per sessie, zodat één enthousiaste bezoeker
de cijfers niet scheeftrekt. We bewaren enkel dag + stap + aantal: geen IP,
geen sessie-id, geen persoonsgegevens.
"""
from datetime import date

from flask import session

from .extensions import db

STAPPEN = [
    ("bezoek", "Fiche bekeken"),
    ("verdieping", "3+ fiches (aan het plannen)"),
    ("login_gezien", "Aanmeldpagina geopend"),
    ("code_gevraagd", "E-mailcode gevraagd"),
    ("account", "Profiel aangemaakt"),
]


def tel_stap(stap, eenmalig=True):
    """Tel een trechterstap voor vandaag. Faalt stil: meten mag nooit een
    bezoeker in de weg zitten."""
    try:
        if eenmalig:
            gezien = session.get("_trechter") or []
            if stap in gezien:
                return
            session["_trechter"] = gezien + [stap]
        from .models import TrechterTeller
        vandaag = date.today()
        rij = db.session.get(TrechterTeller, (vandaag, stap))
        if rij is None:
            rij = TrechterTeller(dag=vandaag, stap=stap, aantal=0)
            db.session.add(rij)
        rij.aantal = (rij.aantal or 0) + 1
        db.session.commit()
    except Exception:
        db.session.rollback()


def tel_fiche_bezoek():
    """Fiche geopend; bij de derde in één sessie ook 'verdieping'."""
    try:
        n = int(session.get("_fiches", 0)) + 1
        session["_fiches"] = n
        tel_stap("bezoek")
        if n >= 3:
            tel_stap("verdieping")
    except Exception:
        pass


def cijfers(dagen=14):
    """Trechter over de laatste N dagen: [(stap, label, aantal, %), ...]."""
    from datetime import timedelta
    from .models import TrechterTeller
    vanaf = date.today() - timedelta(days=dagen - 1)
    ruw = dict(db.session.query(TrechterTeller.stap,
                                db.func.sum(TrechterTeller.aantal))
               .filter(TrechterTeller.dag >= vanaf)
               .group_by(TrechterTeller.stap).all())
    basis = ruw.get("bezoek", 0) or 0
    uit = []
    for stap, label in STAPPEN:
        n = int(ruw.get(stap, 0) or 0)
        pct = round(100 * n / basis) if basis else 0
        uit.append((stap, label, n, pct))
    return uit
