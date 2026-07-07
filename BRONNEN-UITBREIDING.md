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

## Terugdraaien

Niets aan de bestaande data wijzigt. Wil je een bron terug weg: zet ze uit in de
instellingen en verwijder haar rijen met `DELETE FROM events WHERE source='tm';`
(of `tv`/`osm`). De nieuwe kolommen mogen blijven staan; ze zijn leeg voor
UiT-events.
