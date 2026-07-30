# Umami installeren (optioneel) — gedragsanalyse zonder cookiebanner

Ravot meet **herkomst** al zelf (zie /beheer/herkomst): kanalen, bronnen,
campagnes, landingspagina's. Wil je daarnaast ook **gedrag** zien
(paginapaden, verblijfsduur, apparaten, live bezoekers), dan is Umami de
privacyvriendelijke keuze: draait op je eigen VPS, cookieloos, dus géén
consentbanner — de belofte "geen pop-ups" blijft overeind.

> Dit is bewust een handleiding en geen wijziging aan docker-compose.yml:
> zo beslis je zelf óf en wanneer, en blijft de live compose onaangeroerd.

## Stap 1 — compose-snippet toevoegen

Voeg onderaan `services:` in /srv/ravot/docker-compose.yml toe
(let op de inspringing, gelijk met `web:`):

```yaml
  umami:
    image: ghcr.io/umami-software/umami:postgresql-latest
    environment:
      DATABASE_URL: postgresql://umami:KIES-EEN-WACHTWOORD@umami-db:5432/umami
      APP_SECRET: KIES-EEN-LANGE-RANDOM-STRING
    depends_on: [umami-db]
    networks: [intern, proxy]
    restart: unless-stopped

  umami-db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: umami
      POSTGRES_USER: umami
      POSTGRES_PASSWORD: KIES-EEN-WACHTWOORD
    volumes:
      - umami-data:/var/lib/postgresql/data
    networks: [intern]
    restart: unless-stopped
```

En onderaan bij `volumes:`:

```yaml
  umami-data:
```

Umami krijgt bewust een **eigen** database-container: je Ravot-pgdata blijft
volledig ongemoeid.

## Stap 2 — subdomein via je reverse proxy

Maak `stats.ravot.be` aan (DNS A-record naar de VPS) en laat je reverse
proxy dat subdomein naar de umami-container (poort 3000) sturen, zoals
ravot.be nu naar web gaat.

## Stap 3 — starten en inrichten

```
cd /srv/ravot && docker compose up -d umami umami-db
```

Log in op https://stats.ravot.be (standaard admin/umami — **meteen
wachtwoord wijzigen**), voeg website "ravot.be" toe en kopieer de
script-snippet die Umami toont.

## Stap 4 — script + CSP in Ravot

1. Plak de Umami-regel in `app/templates/base.html`, net vóór `</head>`:
   `<script defer src="https://stats.ravot.be/script.js" data-website-id="JOUW-ID"></script>`
2. Breid in `app/__init__.py` de CSP uit:
   `script-src 'self' https://unpkg.com https://stats.ravot.be; `
   en `connect-src 'self' https://stats.ravot.be; `
3. Commit + deploy zoals altijd.

## Verwijderen

`docker compose rm -sf umami umami-db`, het volume `umami-data` wissen,
de script-regel en CSP-aanpassing terugdraaien. Ravots eigen
herkomst-tracking blijft gewoon doorwerken.
