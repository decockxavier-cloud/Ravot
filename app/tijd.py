"""Belgische lokale tijd.

De container draait op UTC. Alles wat de gebruiker als klok ervaart — is deze
zaak nu open, welke dag is het vandaag — moet in Europe/Brussels gerekend
worden, anders staat een zaak die om 22:00 sluit 's zomers tot 00:00 nog als
"open" (UTC loopt twee uur achter).

Opslag blijft bewust UTC (utcnow in de modellen); enkel de weergave en de
open/dicht-beoordeling gebruiken lokale tijd.
"""
from datetime import datetime, timedelta, timezone

BRUSSEL = "Europe/Brussels"


def _zone():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(BRUSSEL)
    except Exception:      # tzdata ontbreekt in een kale image
        return None


def _eu_zomertijd(dt_utc):
    """Terugvalregel als de tijdzonedatabank ontbreekt: EU-zomertijd loopt van
    de laatste zondag van maart 01:00 UTC tot de laatste zondag van oktober
    01:00 UTC."""
    def laatste_zondag(jaar, maand):
        d = datetime(jaar, maand, 31) if maand == 3 else datetime(jaar, maand, 31)
        while d.weekday() != 6:
            d -= timedelta(days=1)
        return d.replace(hour=1)
    jaar = dt_utc.year
    return laatste_zondag(jaar, 3) <= dt_utc < laatste_zondag(jaar, 10)


def nu_lokaal():
    """Huidige tijd in België, als naïeve datetime (lokale wandklok)."""
    zone = _zone()
    if zone is not None:
        return datetime.now(zone).replace(tzinfo=None)
    nu_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    return nu_utc + timedelta(hours=2 if _eu_zomertijd(nu_utc) else 1)


def naar_lokaal(dt_utc):
    """Zet een in UTC bewaarde datetime om naar de Belgische wandklok."""
    if dt_utc is None:
        return None
    zone = _zone()
    if zone is not None:
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        return dt_utc.astimezone(zone).replace(tzinfo=None)
    naief = dt_utc.replace(tzinfo=None) if dt_utc.tzinfo else dt_utc
    return naief + timedelta(hours=2 if _eu_zomertijd(naief) else 1)
