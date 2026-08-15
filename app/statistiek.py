"""Bezoek- en actietellers (patch 238).

Geaggregeerd per maand, zonder persoonsgegevens. Faalt altijd stil: meten mag
nooit een bezoeker in de weg zitten.
"""
from datetime import date

from .extensions import db


def _maand():
    v = date.today()
    return v.year * 100 + v.month


def tel_fiche(event_id):
    """Eén bezoek aan een fiche."""
    if not event_id:
        return
    try:
        from .models import FicheBezoek
        m = _maand()
        rij = db.session.get(FicheBezoek, (event_id, m))
        if rij is None:
            rij = FicheBezoek(event_id=event_id, maand=m, aantal=0)
            db.session.add(rij)
        rij.aantal = (rij.aantal or 0) + 1
        db.session.commit()
    except Exception:
        db.session.rollback()


def tel_route(route_id, soort):
    """Actie op een route: bekeken, gpx, print of bingo."""
    if not route_id:
        return
    try:
        from .models import RouteActie
        m = _maand()
        rij = db.session.get(RouteActie, (route_id, m, soort))
        if rij is None:
            rij = RouteActie(route_id=route_id, maand=m, soort=soort, aantal=0)
            db.session.add(rij)
        rij.aantal = (rij.aantal or 0) + 1
        db.session.commit()
    except Exception:
        db.session.rollback()


def per_gemeente(maanden=3):
    """Fichebezoeken opgeteld per gemeente — het cijfer voor je verkoopgesprek."""
    from .models import Event, FicheBezoek
    vanaf = _maand() - maanden + 1
    rijen = (db.session.query(Event.gemeente,
                              db.func.sum(FicheBezoek.aantal),
                              db.func.count(db.distinct(FicheBezoek.event_id)))
             .join(FicheBezoek, FicheBezoek.event_id == Event.id)
             .filter(FicheBezoek.maand >= vanaf, Event.gemeente.isnot(None))
             .group_by(Event.gemeente).all())
    uit = []
    for gemeente, bezoeken, fiches in rijen:
        uit.append({"gemeente": gemeente, "bezoeken": int(bezoeken or 0),
                    "fiches": int(fiches or 0)})
    uit.sort(key=lambda r: -r["bezoeken"])
    return uit


def per_streek(maanden=3):
    """Zelfde cijfers, opgeteld per toeristische streek."""
    from .regios import streek_van_gemeente
    tel = {}
    for r in per_gemeente(maanden):
        streek = streek_van_gemeente(r["gemeente"]) or "Overige"
        d = tel.setdefault(streek, {"streek": streek, "bezoeken": 0,
                                    "fiches": 0, "gemeenten": 0})
        d["bezoeken"] += r["bezoeken"]
        d["fiches"] += r["fiches"]
        d["gemeenten"] += 1
    return sorted(tel.values(), key=lambda r: -r["bezoeken"])


def route_cijfers(maanden=3):
    """Per route: bekeken en gedownload."""
    from .models import FietsRoute, RouteActie
    vanaf = _maand() - maanden + 1
    ruw = {}
    for rid, soort, n in (db.session.query(RouteActie.route_id,
                                           RouteActie.soort,
                                           db.func.sum(RouteActie.aantal))
                          .filter(RouteActie.maand >= vanaf)
                          .group_by(RouteActie.route_id, RouteActie.soort).all()):
        ruw.setdefault(rid, {})[soort] = int(n or 0)
    uit = []
    for r in FietsRoute.query.filter_by(hidden=False).all():
        d = ruw.get(r.id, {})
        uit.append({"route": r, "bekeken": d.get("bekeken", 0),
                    "gpx": d.get("gpx", 0), "print": d.get("print", 0),
                    "bingo": d.get("bingo", 0),
                    "meenames": d.get("gpx", 0) + d.get("print", 0)
                    + d.get("bingo", 0)})
    uit.sort(key=lambda r: (-r["meenames"], -r["bekeken"]))
    return uit
