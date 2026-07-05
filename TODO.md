# Ravot — roadmap / to-do's

## ✅ Gereed (live-baar)
- Kern-app: Vandaag/Weekend/Kaart, scoring, anonieme modus, accounts (magic links)
- Ravotscore (anoniem, leeftijdsgesplitst), echte-kost, foto's
- Zoekbalk + filters met resultaatteller, PWA, landingspagina + warme huisstijl
- Weekendmail + maandagvraag-mail
- Vakantiemodus (banner + kampdetectie)
- Weerkoppeling (regen → binnen), agenda-export (.ics)
- SEO/GEO (gemeentepagina's, editie-reeksen, JSON-LD, sitemap, llms.txt)
- publiq-compliance (UiTinVlaanderen-verwijzing, organisator-oproep, Vlieg-label)
- Admin: instellingen + verbindingsstatus (secrets veilig in .env)
- Deployment: VPS + Docker + NPM + SSL, backups, cron

## 🔜 Eerstvolgende echte mijlpaal
- [ ] **Live-activatie publiq** aanvragen → UIT_SEARCH_URL naar productie
      (dan echte, actuele events i.p.v. testdata)

## Fase 4 — Uitbatersportaal
- [ ] Fiche claimen/aanvullen door organisator/uitbater
- [ ] Eerste betaalde zichtbaarheid (horeca/attracties)

## Fase 5 — Ravot Insights
- [ ] Gap-index vraag/aanbod (k-anonimiteit ≥ 20), kwartaalrapport + dashboard
- [ ] Vooraf afstemmen met publiq over geaggregeerde aanbodstatistiek

## Fase 6 — Feest & verjaardag (leadgeneratie)
- [ ] suppliers-tabel: feestzalen, traiteurs, springkastelen, animatie
- [ ] Fichepagina's met Ravotscore
- [ ] "Vraag offerte aan"-knop (kort formulier)
- [ ] quote_requests met consent-vlag + timestamp; privacyverklaring aanvullen
- [ ] Levenscyclus-suggesties (kind wordt 6/12)
- [ ] Aanbiedersportaal: leads ontvangen, facturatie per lead/abonnement
- [ ] SEO: /feest/<gemeente>-pagina's

## Overige verbeteringen (later)
- [ ] Rijkere filters (rolstoelvriendelijk, verzorgingstafel, picknick) — komt uit review-tags
- [ ] Redis voor rate-limiting (nu in-memory; ~3× ruimer met 3 workers)
- [ ] db-wachtwoord verstevigen via ALTER USER

## Doorlopend
- [ ] Gezinsbarometer (kwartaalpublicatie echte-kostdata) zodra volume volstaat
