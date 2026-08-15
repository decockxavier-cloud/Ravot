"""Proef: hoeveel speeltuinen hebben bruikbaar Mapillary-straatbeeld?

Draaien op de VPS, met een gratis Mapillary-token:

    docker compose exec -e MAPILLARY_TOKEN=MLY|... web \
        python scripts/mapillary_proef.py

Doel: wéten of deze route de moeite is vóór we iets bouwen. Het script wijzigt
niets in de databank — het kijkt alleen.

Waarom Mapillary en niet Street View: Mapillary-beelden zijn CC-BY-SA, dus we
mogen ze downloaden, cachen en zelf serveren mits bronvermelding. Street View
mag dat niet (per weergave ophalen, betaald) en zou de factuur laten meegroeien
met het bereik.
"""
import os
import random
import sys
import time

import requests

TOKEN = os.environ.get("MAPILLARY_TOKEN", "").strip()
STEEKPROEF = int(os.environ.get("STEEKPROEF", "100"))
STRAAL_M = int(os.environ.get("STRAAL_M", "50"))
API = "https://graph.mapillary.com/images"


def beeld_dichtbij(lat, lng, straal_m=STRAAL_M):
    """Eén Mapillary-beeld binnen straal_m van dit punt, of None."""
    # bbox in graden: 1 breedtegraad ~111 km, lengtegraad ~63% daarvan in BE
    d_lat = straal_m / 111_000
    d_lng = straal_m / 70_000
    bbox = f"{lng - d_lng},{lat - d_lat},{lng + d_lng},{lat + d_lat}"
    try:
        antw = requests.get(API, timeout=20, params={
            "access_token": TOKEN,
            "fields": "id,captured_at,thumb_1024_url,compass_angle",
            "bbox": bbox,
            "limit": 3,
        })
        if antw.status_code == 401:
            print("FOUT: token geweigerd. Klopt MAPILLARY_TOKEN?")
            sys.exit(1)
        antw.raise_for_status()
        data = (antw.json() or {}).get("data") or []
    except Exception as fout:
        print(f"  (fout bij ophalen: {fout})")
        return None
    if not data:
        return None
    # nieuwste eerst: een beeld van 2015 helpt een ouder niet
    data.sort(key=lambda d: d.get("captured_at") or 0, reverse=True)
    return data[0]


def main():
    if not TOKEN:
        print("Geen MAPILLARY_TOKEN meegegeven.\n"
              "Maak er gratis een op mapillary.com (Developers > nieuwe app)\n"
              "en draai dan:\n"
              "  docker compose exec -e MAPILLARY_TOKEN='MLY|...' web \\\n"
              "      python scripts/mapillary_proef.py")
        sys.exit(1)

    from app import create_app
    from app.config import Config
    from app.models import Event

    app = create_app(Config)
    with app.app_context():
        alle = (Event.query
                .filter(Event.subtype == "playground",
                        Event.pending.is_(False), Event.hidden.is_(False),
                        Event.image_url.is_(None),
                        Event.lat.isnot(None))
                .all())
    print(f"{len(alle)} speeltuinen zonder foto in totaal.")
    random.seed(42)                      # herhaalbare steekproef
    proef = random.sample(alle, min(STEEKPROEF, len(alle)))
    print(f"Steekproef van {len(proef)}, straal {STRAAL_M} m.\n")

    raak = 0
    recent = 0
    nu_jaar = time.gmtime().tm_year
    for n, ev in enumerate(proef, 1):
        beeld = beeld_dichtbij(ev.lat, ev.lng)
        if beeld:
            raak += 1
            ms = beeld.get("captured_at") or 0
            jaar = time.gmtime(ms / 1000).tm_year if ms else 0
            if jaar and nu_jaar - jaar <= 4:
                recent += 1
            merk = "✔" if jaar and nu_jaar - jaar <= 4 else "≈"
            print(f"  {merk} {ev.title[:44]:46s} {jaar or '?'}")
        else:
            print(f"  ✕ {ev.title[:44]:46s} geen beeld")
        time.sleep(0.2)                  # hoffelijk tegenover een gratis API
        if n % 25 == 0:
            print(f"  ... {n}/{len(proef)}")

    pct = round(100 * raak / len(proef))
    pct_recent = round(100 * recent / len(proef))
    print("\n=== RESULTAAT ===")
    print(f"  beeld gevonden:            {raak}/{len(proef)}  ({pct}%)")
    print(f"  waarvan max 4 jaar oud:    {recent}/{len(proef)}  ({pct_recent}%)")
    print(f"\n  Geschat voor alle {len(alle)} speeltuinen: "
          f"~{round(len(alle) * pct_recent / 100)} bruikbare beelden.")
    if pct_recent < 25:
        print("\n  Advies: te dun. Dit lost het fotoprobleem niet op.")
    elif pct_recent < 50:
        print("\n  Advies: gedeeltelijke dekking. Zinvol als aanvulling, maar "
              "reken op veel fiches die alsnog zonder foto blijven.")
    else:
        print("\n  Advies: ruime dekking — de moeite om te bouwen.")


if __name__ == "__main__":
    main()
