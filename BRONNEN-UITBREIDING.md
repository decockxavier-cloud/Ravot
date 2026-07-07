# Uitbreiding: meerdere databronnen (naast publiq)

Ravot haalde tot nu enkel events uit UiTdatabank (publiq). Deze uitbreiding voegt
drie extra bronnen toe, elk met een **harde kindvriendelijk-poort**: aanbod dat
niet duidelijk voor gezinnen met kinderen is, wordt bij het syncen weggegooid en
komt de databank nooit in. publiq blijft ongewijzigd werken.

## Wat is toegevoegd

| Bron | code | Type | Kindvriendelijk-poort |
|------|------|------|-----------------------|
| UiTdatabank (publiq) | `uit` | gedateerde events | ongewijzigd (Vlieg / leeftijd) |
| Ticketmaster | `tm` | gedateerde events | enkel het **Family**-segment (BE) |
| Toerisme Vlaanderen | `tv` | permanente attracties | strikte whitelist (speeltuin, zoo, museum…) |
| OpenStreetMap | `osm` | permanente POI's | enkel kindvriendelijke tags (playground, zoo, theme_park, water_park) |

Permanente POI's (attracties, speeltuinen) verschijnen op **Kaart / Ontdek /
gemeentepagina's**. De gedateerde **Vandaag / Weekend**-feeds blijven bewust puur
gedateerd, zodat duizenden vaste plekken je agenda niet overspoelen.

Vlieg-label en de UiTinVlaanderen-link blijven exclusief voor publiq. Andere
bronnen krijgen hun eigen "meer info"-link en een correcte bronvermelding
(Ticketmaster, Toerisme Vlaanderen "Gratis Hergebruik", OpenStreetMap ODbL).

## Deploy-stappen (VPS)

1. **Backup eerst** (DB-regel): `bash scripts/backup-db.sh`
2. Code binnenhalen en herbouwen:
   ```
   cd /srv/ravot && git pull && docker compose up -d --build
   ```
3. **Veilige migratie** (voegt enkel kolommen toe, wist niets):
   ```
   docker compose exec web flask migrate-db
   ```
   Nieuw op `events`: `ext_id`, `source_url`, `attribution`, `is_permanent`.
4. (Optioneel) Ticketmaster-key in `.env` zetten — gratis via
   developer.ticketmaster.com:
   ```
   TICKETMASTER_API_KEY=...
   ```
5. Bronnen aanzetten in **/beheer → Instellingen** (`bron_tm_aan`,
   `bron_tv_aan`, `bron_osm_aan`). Ze staan standaard **uit**.
6. Eerste sync:
   ```
   docker compose exec web flask sync-all        # alle ingeschakelde bronnen
   # of per bron: flask sync-bron tv
   ```
7. Cron: vervang `sync-uit` door `sync-all` (of laat beide, naar keuze).

## Status & test

Bekijk per bron de status en aantallen in **/beheer → Verbindingen**; daar zit
ook een "Test"-knop voor Ticketmaster.

## Publiq (UiTdatabank) staat standaard UIT tot go-live

Zolang je op de **testdatabank** zit, hoort publiq nergens te verschijnen — niet
als data en niet als verplichte UiTinVlaanderen/UiTdatabank-vermelding. Daarom:

- `bron_uit_aan` staat **standaard uit**. Eén schakelaar stuurt zowel het syncen
  als álle publiq-vermeldingen op de site (footer, landing, over, voorwaarden,
  gemeentepagina's, llms.txt, Vlieg-labels, de "meer info"-link).
- Zet `bron_uit_aan` pas AAN in **/beheer → Instellingen** wanneer je live-toegang
  van publiq hebt én `UIT_SEARCH_URL` in `.env` op productie staat. Dan komen alle
  verwijzingen vanzelf terug — precies zoals publiq het vraagt.
- De bronvermelding onderaan toont enkel de bronnen die effectief aanstaan.
  Staat alleen Toerisme Vlaanderen aan, dan lees je "in samenwerking met Toerisme
  Vlaanderen" — zonder publiq.

## De UiT-testdata verwijderen

De events uit de testdatabank haal je er in één keer uit:

```
docker compose exec web flask purge-bron uit --ja
```

Dit verwijdert alle events met bron `uit` plus verweesde locaties/reeksen en
herberekent de postcode-zwaartepunten. Werkt ook voor `tm`/`tv`/`osm`. Zonder
`--ja` vraagt het commando eerst een bevestiging.

## Aanbevolen volgorde voor NU (nog niet live bij publiq)

1. Backup → `git pull && docker compose up -d --build` → `flask migrate-db`
2. Testdata weg: `flask purge-bron uit --ja`
3. Toegelaten bronnen aanzetten in **/beheer → Instellingen**:
   `bron_tv_aan` en `bron_osm_aan` (en `bron_tm_aan` als je een Ticketmaster-key
   in `.env` zet). Publiq laat je **uit**.
4. `flask sync-all` → enkel het toegelaten, kindvriendelijke aanbod verschijnt.
5. Later, bij go-live publiq: `bron_uit_aan` aan + `UIT_SEARCH_URL` op productie +
   `flask sync-bron uit`. Alle UiT-vermeldingen komen dan automatisch terug.


Niets aan de bestaande data wijzigt. Wil je een bron terug weg: zet ze uit in de
instellingen en verwijder haar rijen met `DELETE FROM events WHERE source='tm';`
(of `tv`/`osm`). De nieuwe kolommen mogen blijven staan; ze zijn leeg voor
UiT-events.
