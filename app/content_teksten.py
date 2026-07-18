"""Concept juridische teksten voor Ravot — CONCEPT, laten nakijken door een jurist.

Ingevuld met de gegevens van YAMY BV. Deze teksten worden bij migrate-db in de
database geladen zodat je ze meteen in de admin ziet en kunt bijwerken.

BELANGRIJK: dit zijn concepten, geen juridisch advies. Laat ze valideren voor je
er echt op vertrouwt, zeker omdat Ravot gegevens van gezinnen (en leeftijden van
kinderen) verwerkt.
"""

_BEDRIJF = "YAMY BV"
_ADRES = "Klaproosstraat 36, 8800 Roeselare"
_BTW = "BE 0505.624.079"
_MAIL = "info@complemy.com"


# Beloningsvoorwaarden: één bron voor zowel de seed (nieuwe installaties) als
# de migratie (bestaande, mogelijk bewerkte voorwaardenpagina's aanvullen).
BELONING_VOORWAARDEN = f"""### Ravotpunten en beloningen

Ravotpunten worden verdiend door acties op Ravot (bezoeken bevestigen, scores
geven, foto's delen, ...). Ze hebben geen geldwaarde, zijn niet inwisselbaar
voor geld en niet overdraagbaar. Punten blijven beperkt geldig (momenteel 6
maanden na het verdienen; de oudste punten worden bij het inwisselen eerst
gebruikt); niveaus en badges vervallen nooit. Beloningen zijn beschikbaar
zolang de voorraad strekt en kunnen worden vervangen door een gelijkwaardig
alternatief. Ravot ({_BEDRIJF}) kan het beloningsprogramma, de puntenwaarden en
de catalogus te allen tijde aanpassen en kan punten of inwisselingen schrappen
bij misbruik. Dit is een getrouwheidsprogramma, geen kansspel.

"""

CONTENT_SEED = {
    "privacy": {
        "titel": "Privacyverklaring",
        "inhoud_md": f"""## Privacyverklaring

*Laatst bijgewerkt: bij lancering. Dit is een conceptversie.*

Ravot hecht veel belang aan de bescherming van je persoonsgegevens en aan je
privacy. In deze verklaring leggen we duidelijk uit welke gegevens we verzamelen,
waarom, hoelang we ze bewaren en welke rechten je hebt.

### Wie zijn we?

Ravot is een initiatief van {_BEDRIJF}, {_ADRES}, met ondernemingsnummer/BTW
{_BTW}. Voor alle vragen over je privacy kan je ons bereiken via {_MAIL}.
{_BEDRIJF} is de verwerkingsverantwoordelijke voor je gegevens.

### Welke gegevens verwerken we?

We houden het bewust minimaal. Concreet verwerken we:

- Je **e-mailadres** — om je te laten inloggen en (als je dat wenst) mails te sturen.
- Je **postcode** — om activiteiten in jouw buurt te tonen.
- De **leeftijd of het geboortejaar** van je kinderen — om activiteiten op de
  juiste leeftijd te tonen. We vragen **geen namen van kinderen**.
- Je **bewaarde activiteiten en Ravotscores** — om je overzicht te bieden en om
  andere gezinnen anoniem te helpen kiezen.
- Beperkte **gebruiksgegevens** (zoals welke activiteiten je leuk vindt) om je
  betere suggesties te geven.

### Waarom verwerken we deze gegevens?

We verwerken je gegevens om je de dienst van Ravot te kunnen aanbieden: het tonen
van geschikte gezinsactiviteiten in jouw buurt, het bijhouden van je bewaarde
uitjes, en het (optioneel) versturen van suggesties per mail. De rechtsgrond
hiervoor is de uitvoering van onze dienst en, voor de mails, jouw toestemming.

### Gegevens van kinderen

Ravot is gericht op ouders. Het account hoort bij een volwassene. We verzamelen
geen namen van kinderen, enkel hun leeftijd, en uitsluitend om activiteiten op
maat te tonen. We verwerken deze gegevens met bijzondere zorg, in lijn met de
strengere bescherming die de wetgeving voorziet voor gegevens van minderjarigen.

### Hoelang bewaren we je gegevens?

We bewaren je gegevens zolang je een account hebt. Verwijder je je account, dan
wissen we je persoonsgegevens. Anonieme Ravotscores kunnen behouden blijven,
omdat ze niet naar jou herleidbaar zijn.

### Met wie delen we je gegevens?

We verkopen je gegevens nooit. We delen ze niet met derden voor commerciële
doeleinden. De activiteitengegevens zelf zijn afkomstig van UiTdatabank. Voor het
technisch draaien van de dienst (hosting, mailverzending) doen we mogelijk een
beroep op verwerkers die enkel in onze opdracht handelen.

### Je rechten

Je hebt het recht om je gegevens in te kijken, te verbeteren, te laten
verwijderen, en om je toestemming voor mails op elk moment in te trekken. Stuur
hiervoor een bericht naar {_MAIL}. Je hebt ook het recht om klacht in te dienen
bij de Gegevensbeschermingsautoriteit (www.gegevensbeschermingsautoriteit.be).

### Cookies

Ravot gebruikt enkel functionele cookies die nodig zijn om de site te laten
werken. Meer daarover lees je in ons [cookiebeleid](/cookies).
""",
    },
    "cookies": {
        "titel": "Cookiebeleid",
        "inhoud_md": f"""## Cookiebeleid

*Dit is een conceptversie.*

Een cookie is een klein tekstbestand dat een website op je toestel plaatst.
Cookies helpen een site correct te werken en te onthouden wat je koos.

### Welke cookies gebruikt Ravot?

We houden het eenvoudig en privacyvriendelijk.

- **Functionele cookies (altijd actief).** Deze zijn noodzakelijk om de website
  te laten werken — bijvoorbeeld om je aangemeld te houden en om je
  cookievoorkeur te onthouden. Hiervoor is geen toestemming nodig.
- **Analytische cookies (optioneel).** Deze zouden ons helpen te begrijpen hoe
  bezoekers Ravot gebruiken, zodat we de site kunnen verbeteren. Deze plaatsen we
  enkel als je daarvoor toestemming geeft via de cookiebanner.

### Je keuze beheren

Bij je eerste bezoek verschijnt een cookiebanner waarin je je voorkeuren instelt.
Je kan je keuze altijd wijzigen door je browsercookies te wissen; bij een volgend
bezoek verschijnt de banner dan opnieuw.

### Vragen?

Voor vragen over dit cookiebeleid kan je terecht bij {_BEDRIJF} via {_MAIL}.
""",
    },
    "voorwaarden": {
        "titel": "Gebruiksvoorwaarden",
        "inhoud_md": f"""## Gebruiksvoorwaarden

*Dit is een conceptversie.*

Welkom bij Ravot. Door gebruik te maken van deze website ga je akkoord met deze
voorwaarden. Lees ze dus even door.

### Wat is Ravot?

Ravot is een gratis platform dat gezinnen helpt om leuke, geschikte activiteiten
in Vlaanderen te ontdekken. Ravot is een initiatief van {_BEDRIJF}, {_ADRES},
BTW {_BTW}.

### Gebruik van de dienst

Ravot is gratis te gebruiken. Je gebruikt de dienst voor persoonlijke,
niet-commerciële doeleinden. Je gaat ermee akkoord geen misbruik te maken van het
platform, geen valse informatie te verspreiden en de Ravotscores eerlijk te
gebruiken.

### Activiteitengegevens

De informatie over activiteiten komt grotendeels van UiTdatabank en andere
bronnen. We doen ons best om correcte en actuele informatie te tonen, maar we
kunnen de juistheid, volledigheid of beschikbaarheid van een activiteit niet
garanderen. Controleer belangrijke details (uur, prijs, leeftijd) altijd bij de
organisator zelf.

### Aansprakelijkheid

Ravot en {_BEDRIJF} zijn niet aansprakelijk voor schade die zou voortvloeien uit
het gebruik van de informatie op deze website, noch voor de activiteiten zelf die
door derden worden georganiseerd. Je neemt zelf de beslissing om aan een
activiteit deel te nemen.

{BELONING_VOORWAARDEN}### Wijzigingen

We kunnen deze voorwaarden en de dienst zelf van tijd tot tijd aanpassen. De
meest recente versie vind je steeds op deze pagina.

### Contact

Vragen over deze voorwaarden? Contacteer ons via {_MAIL}.
""",
    },
    "contact": {
        "titel": "Contact",
        "inhoud_md": f"""## Contact

Heb je een vraag, een suggestie of een probleem gemerkt? We horen het graag.

Je bereikt ons het snelst via e-mail:

**{_MAIL}**

### Wie zit er achter Ravot?

Ravot is een initiatief van:

**{_BEDRIJF}**
{_ADRES}
BTW {_BTW}

We doen ons best om je bericht binnen enkele werkdagen te beantwoorden.
""",
    },
    "over": {
        "titel": "Over Ravot",
        "inhoud_md": """## Over Ravot

Ravot helpt gezinnen in Vlaanderen om leuke, geschikte activiteiten te ontdekken —
dichtbij, op de juiste leeftijd en zonder gedoe.

### Waarom Ravot?

Als ouder wil je graag leuke dingen doen met je kinderen, maar het aanbod is
versnipperd en niet altijd op maat. Ravot brengt alles samen op één plek, sorteert
op wat bij jouw gezin past, en laat gezinnen elkaar helpen met eerlijke,
anonieme Ravotscores.

### Gratis en zonder reclame

Ravot is en blijft gratis voor gezinnen, en we tonen geen reclame. De
activiteitengegevens komen van UiTdatabank en andere bronnen.

### Meer weten?

Lees [hoe Ravot precies werkt](/hoe-werkt-het) of neem [contact](/contact) met ons op.
""",
    },
}
