# Ravot.be — MVP

Gezinsuitstappen voor Vlaanderen: gepersonaliseerd op kinderleeftijd, afstand en budget.
App = kern (Vandaag / Weekend / Kaart), donderdagmail = retentiekanaal.
Zie `Ravot_Strategienota.docx` en `Ravot_SEO_GEO_Plan.docx` voor het volledige plan.

## Snel starten (lokaal)

```bash
pip install -r requirements.txt
export FLASK_APP=run.py FLASK_ENV=development
flask init-db
flask seed-demo          # 40 demo-events in 5 gemeenten (geen UiT-key nodig)
flask run --port 8000
```

Open http://localhost:8000 — probeer de anonieme modus (postcode 8800, leeftijden 4 en 7).
Mails (magic links, weekendmail) verschijnen in de console zolang SMTP_HOST leeg is.

## Productie (Hostinger VPS + Coolify)

1. `.env` aanmaken op basis van `.env.example` (sterke SECRET_KEY!).
2. Coolify: nieuw project op deze repo → docker-compose wordt herkend.
   **Belangrijk:** het volume `ravot_pgdata` nooit hernoemen of wissen; back-up vóór elke compose-wijziging.
3. Eerste keer, in de web-container:
   ```bash
   flask init-db
   flask create-admin jij@ravot.be     # toont de TOTP-secret voor je authenticator-app
   ```
4. Cron (host of Coolify scheduled tasks):
   ```
   0 3 * * *   docker compose exec -T web flask sync-uit          # nachtelijke UiT-sync
   0 17 * * 4  docker compose exec -T web flask send-weekendmail  # donderdag 17u
   0 2 * * *   pg_dump ... | gpg ... > backup  # versleutelde nachtelijke back-up
   ```

## publiq / UiTdatabank

- Key aanvragen via platform.publiq.be (UiTiD-account). Test-URL staat al in `.env.example`;
  na goedkeuring `UIT_SEARCH_URL` op productie zetten.
- De sync haalt enkel gezinsrelevante events (Vlieg-label of leeftijd 0–12), filtert
  contactgegevens van organisatoren weg (voorwaarde publiq) en synct 1×/nacht —
  ruim binnen de limiet van 60.000 calls/dag.
- Bronvermelding UiTdatabank staat in de footer.

## Beveiliging & privacy (samengevat)

- Gebruikers: magic links (15 min, eenmalig, enkel sha256-hash opgeslagen), geen 2FA (bewust).
- Admin (/beheer): Argon2id-wachtwoord + **verplichte** TOTP-2FA, audit log.
- CSRF op elk formulier, security headers + CSP, rate limits op alle schrijf-endpoints.
- Dataminimalisatie: kinderen = enkel geboortejaar; postcode i.p.v. adres; reviews anoniem;
  delen van interesse per event en standaard uit; export + verwijderen self-service in /mijn/profiel.
- Enkel functionele cookies → geen cookiebanner nodig.

## Testen

```bash
python -m pytest tests/ -q     # 32 tests: scoring, magic links, reviews, privacy, SEO, sync
```

De privacytests zijn de belangrijkste: gezin A kan nooit data van gezin B zien,
delen staat standaard uit (ook voor vrienden), en accountverwijdering is volledig.

## Structuur

```
app/
  models.py        alle tabellen (GDPR-minimaal by design)
  scoring.py       scoringsengine: leeftijd × afstand × interesse × budget
  pricing.py       gezinsprijs, €-indicator, Ravotscore-aggregatie per leeftijd
  seo.py           JSON-LD, meta-templates, antwoordblokken (AI-citeerbaar)
  routes/          public (incl. programmatic gemeentepagina's), auth, account, admin
  services/        uit_sync (nachtelijk), magic (links+mail), weekendmail
  templates/       SSR — Jinja2, mobile-first
  static/          huisstijl-CSS, PWA (manifest + service worker)
tests/             pytest-suite
```

## Bewuste beperkingen MVP (zie strategienota)

Fase 2+: maandagvraag-mail, vakantiemodus, weerkoppeling, horeca-laag, Insights.
De tabellen en instrumentatie daarvoor zitten er al in (interactions logt alles vanaf dag één).
