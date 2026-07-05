# Ravot.be — gezinsuitstappen voor Vlaanderen

Gepersonaliseerde uitstapsuggesties voor gezinnen met kinderen (2–12 jaar),
op maat van kinderleeftijd, afstand en budget. App = kern (Vandaag / Weekend /
Kaart), mails = retentiekanaal. Gratis, advertentievrij, privacy-by-design.

Staat volledig los van Cluma.

## Stack
Flask + SQLAlchemy + PostgreSQL, server-side gerenderde Jinja2-templates,
mobile-first (PWA). Docker Compose op Hostinger VPS, Nginx Proxy Manager ervoor.
Data uit de UiTdatabank Search API (publiq).

## Wat werkt (live)
- Kern-app: Vandaag / Weekend / Kaart, gepersonaliseerde scoring (leeftijd,
  afstand, budget), anonieme snelstart en accounts met magic links.
- Landingspagina (warme, speelse huisstijl) voor nieuwe bezoekers; wie een
  profiel/zoekopdracht heeft gaat direct naar de app. Logo -> altijd terug home.
- Ravotscore: anonieme reviews per gezin, gesplitst per leeftijd; echte-kost
  crowdsourcing. Privacy-by-design (enkel leeftijden, geen namen; postcode i.p.v. adres).
- Foto's uit UiTdatabank (mediaObject), filters (gratis/binnen/buiten/top),
  zoekbalk op naam/gemeente, resultaatteller, deel-knop, "zet in agenda" (.ics).
- publiq-compliance: UiTinVlaanderen-verwijzing (met UTM), organisator-oproep,
  UiT-met-Vlieg-label + legende.
- Mails: weekendmail (donderdag), maandagvraag-mail (maandag - scores binnenhalen).
- Vakantiemodus (Vlaamse schoolvakanties t/m 2027) + weerkoppeling
  (regen -> binnen-activiteiten omhoog, via Open-Meteo, gratis).
- Admin (/beheer): Argon2id + verplichte TOTP-2FA, dashboard, audit log,
  instellingen (niet-geheime config) en verbindingsstatus (UiT + SMTP testen).

## Configuratie (.env - NOOIT in git of database)
Secrets blijven bewust in .env: DATABASE_URL, SECRET_KEY, UIT_API_KEY,
UIT_SEARCH_URL (test vs productie), SITE_URL, SMTP_*, MAIL_FROM.
Niet-geheime instellingen (UiT-query, sync-omvang, mails/weer aan-uit, radius)
beheer je in /beheer/instellingen.

## CLI
  flask init-db            tabellen aanmaken (verse installatie)
  flask migrate-db         ontbrekende kolommen/tabellen toevoegen (veilig)
  flask create-admin <e>   admin aanmaken (toont TOTP-secret)
  flask sync-uit           events + foto's + Vlieg-label ophalen
  flask send-weekendmail   donderdagmail
  flask send-maandagmail   maandagvraag (scores binnenhalen)

## Deploy (VPS)
  cd /srv/ravot && git pull && docker compose up -d --build
  docker compose exec web flask migrate-db   # enkel bij nieuwe kolommen/tabellen
  docker compose exec web flask sync-uit      # enkel bij data-wijzigingen
KRITIEK: pgdata-volume nooit wissen; backup voor elke compose-wijziging.

## Cron (VPS)
  0 2 * * *    backup
  0 4 * * *    sync-uit
  0 17 * * 4   send-weekendmail
  0 10 * * 1   send-maandagmail

## Roadmap (nog te bouwen - aparte, doordachte ronde)
- Fase 4: uitbatersportaal + betaling.
- Fase 5: Ravot Insights (gap-index, k-anonimiteit >= 20) voor gemeenten/CC's.
- Fase 6: feest- & verjaardagsmodule met offerte-leadgeneratie (consent-gated).
Deze raken geld en/of persoonsgegevens van derden en worden pas echt bruikbaar
op live data. Eerstvolgende echte mijlpaal: live-activatie bij publiq aanvragen
en UIT_SEARCH_URL naar productie zetten.
