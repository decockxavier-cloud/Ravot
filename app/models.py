"""Ravot datamodel — privacy by design.

Bewuste keuzes (zie strategienota §9):
- Kinderen: enkel geboortejaar. Geen naam, geen geboortedatum.
- Gezin: e-mail is het enige verplichte identificerende veld; postcode+straal
  i.p.v. adres; weergavenaam optioneel (enkel voor vriendenlijst).
- Reviews: uniek per gezin+event; publiek getoond zonder identiteit.
- Delen van interesse: per event, standaard UIT.
"""
from datetime import datetime, timezone

from .extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class Family(db.Model):
    __tablename__ = "families"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(80))            # optioneel, enkel vrienden
    postcode = db.Column(db.String(4), nullable=False)
    radius_km = db.Column(db.Integer, default=25, nullable=False)
    budget_pref = db.Column(db.String(10), default="all")  # all|free|low
    newsletter_opt_in = db.Column(db.Boolean, default=False, nullable=False)
    monday_opt_in = db.Column(db.Boolean, default=True, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)  # admin kan deactiveren
    # Eénmalige nakijk-vraag na de overstap naar geboortejaren. Nieuwe gezinnen
    # krijgen True (zij vulden meteen jaren in); bestaande rijen krijgen via de
    # migratie FALSE en zien één keer de banner tot ze bevestigen of opslaan.
    gegevens_nagekeken = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    last_seen = db.Column(db.DateTime, default=utcnow)

    children = db.relationship("Child", backref="family", cascade="all, delete-orphan")
    interests = db.relationship("Interest", backref="family", cascade="all, delete-orphan")

    def child_ages(self, year=None):
        year = year or utcnow().year
        return sorted(year - c.birth_year for c in self.children)


class Child(db.Model):
    __tablename__ = "children"
    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey("families.id"), nullable=False, index=True)
    birth_year = db.Column(db.Integer, nullable=False)  # bewust géén dag/maand


class Interest(db.Model):
    __tablename__ = "interests"
    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey("families.id"), nullable=False, index=True)
    category = db.Column(db.String(40), nullable=False)
    weight = db.Column(db.Float, default=1.0, nullable=False)
    __table_args__ = (db.UniqueConstraint("family_id", "category"),)


CATEGORIES = ["buiten", "creatief", "sport", "cultuur", "natuur", "leren"]


class Organizer(db.Model):
    __tablename__ = "organizers"
    id = db.Column(db.Integer, primary_key=True)
    uit_id = db.Column(db.String(64), unique=True, index=True)
    name = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(255), unique=True, index=True)


class Venue(db.Model):
    __tablename__ = "venues"
    id = db.Column(db.Integer, primary_key=True)
    uit_id = db.Column(db.String(64), unique=True, index=True)
    name = db.Column(db.String(255), nullable=False)
    gemeente = db.Column(db.String(80), index=True)
    postcode = db.Column(db.String(4), index=True)
    lat = db.Column(db.Float)
    lng = db.Column(db.Float)


class EditionSeries(db.Model):
    """Permanente reekspagina voor jaarlijks terugkerende events (SEO §2.3)."""
    __tablename__ = "edition_series"
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    organizer_id = db.Column(db.Integer, db.ForeignKey("organizers.id"))
    venue_id = db.Column(db.Integer, db.ForeignKey("venues.id"))
    organizer = db.relationship("Organizer")
    venue = db.relationship("Venue")


class Event(db.Model):
    __tablename__ = "events"
    id = db.Column(db.Integer, primary_key=True)
    uit_id = db.Column(db.String(64), unique=True, index=True)  # ENKEL publiq (UiTinVlaanderen-link)
    # Meerdere bronnen. source: uit | tm (Ticketmaster) | tv (Toerisme Vlaanderen) | osm
    source = db.Column(db.String(16), default="uit", nullable=False, index=True)
    ext_id = db.Column(db.String(120), index=True)   # externe id binnen de bron (app-uniek per bron)
    source_url = db.Column(db.String(500))           # canonieke "meer info & tickets"-link (niet-UiT)
    telefoon = db.Column(db.String(40))              # contact (bv. via Overture-verrijking)
    attribution = db.Column(db.String(120))          # korte bronvermelding (licentie-compliance)
    is_permanent = db.Column(db.Boolean, default=False, nullable=False, index=True)  # POI zonder vaste datum
    hidden = db.Column(db.Boolean, default=False, nullable=False, index=True)  # dubbel: verborgen in lijsten
    dupe_of = db.Column(db.Integer, index=True)   # id van het canonieke event waar dit een dubbel van is
    pending = db.Column(db.Boolean, default=False, nullable=False, index=True)  # door gebruiker ingediend, wacht op review
    # Werkvoorraad voor de beheerder: geïmporteerde/gecureerde fiches die nog
    # niet met eigen ogen zijn nagekeken (beschrijving, foto, voorzieningen).
    nagekeken = db.Column(db.Boolean, default=False, nullable=False)
    partner_until = db.Column(db.DateTime, index=True)   # Ravot Partner actief tot (betaald, nooit invloed op score)
    quality = db.Column(db.Integer, index=True)         # 0-100 volledigheid van de fiche (app/kwaliteit.py)
    subtype = db.Column(db.String(40), index=True)      # fijn OSM-type: playground, park, zoo, museum…
    curated = db.Column(db.Boolean, default=False, nullable=False, index=True)  # 'Ravot-waardig': mens keurde deze fiche goed
    curated_by = db.Column(db.Integer)                  # admin/curator-id die goedkeurde
    curated_at = db.Column(db.DateTime)
    submitted_by = db.Column(db.Integer, db.ForeignKey("families.id"))  # wie het toevoegde (gebruikersbijdrage)
    slug = db.Column(db.String(300), unique=True, index=True)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    start = db.Column(db.DateTime, index=True)
    end = db.Column(db.DateTime, index=True)
    gemeente = db.Column(db.String(80), index=True)
    postcode = db.Column(db.String(4), index=True)
    adres = db.Column(db.String(255))   # straat + huisnummer (bv. uit OSM addr:*)
    lat = db.Column(db.Float)
    lng = db.Column(db.Float)
    age_min = db.Column(db.Integer, default=0)
    age_max = db.Column(db.Integer, default=99)
    categories = db.Column(db.JSON, default=list)
    indoor = db.Column(db.Boolean, default=False)
    has_vlieg = db.Column(db.Boolean, default=False)  # UiT met Vlieg-label (publiq)
    is_free = db.Column(db.Boolean, default=False, index=True)
    price_info = db.Column(db.JSON, default=list)  # [{name, price, min_age, max_age}]
    image_url = db.Column(db.String(500))
    # Ouder-filters (nullable = onbekend; enkel True filtert positief):
    omheind = db.Column(db.Boolean)            # afgesloten speelterrein (peuters!)
    verzorgingstafel = db.Column(db.Boolean)   # verschoontafel aanwezig
    buggy_ok = db.Column(db.Boolean)           # vlot met de wandelwagen
    # Kindvriendelijk is meetbaar: een café IS kindvriendelijk zodra er échte
    # voorzieningen zijn. Deze drie maken dat concreet en filterbaar:
    kinderstoel = db.Column(db.Boolean)        # kinderstoelen aanwezig
    speelhoek = db.Column(db.Boolean)          # speelhoek of speeltuin bij de zaak
    kindermenu = db.Column(db.Boolean)         # kindermenu beschikbaar
    # Verjaardagsfeestjes: biedt deze plek feestjes aan, en wat precies?
    feest = db.Column(db.Boolean, default=False, nullable=False, index=True)
    feest_soorten = db.Column(db.JSON, default=list)   # subset van FEEST_SOORTEN
    # Partner-upsell: een betalende partner kan ervoor kiezen extra opgenomen te
    # worden in de feestpartner-lijst (zichtbaar bij offerteaanvragen). Enkel
    # zinvol met een actieve partner_until.
    in_feestlijst = db.Column(db.Boolean, default=False)
    feest_contact = db.Column(db.String(255))          # e-mail voor offertes
    organizer_id = db.Column(db.Integer, db.ForeignKey("organizers.id"))
    venue_id = db.Column(db.Integer, db.ForeignKey("venues.id"))
    series_id = db.Column(db.Integer, db.ForeignKey("edition_series.id"), index=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    organizer = db.relationship("Organizer")
    venue = db.relationship("Venue")
    series = db.relationship("EditionSeries", backref="events")


class Interaction(db.Model):
    """Instrumentatie vanaf dag één (strategienota §11) — incl. nulresultaten."""
    __tablename__ = "interactions"
    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey("families.id"), index=True)  # None = anoniem
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), index=True)
    type = db.Column(db.String(24), nullable=False, index=True)
    # view | click | save | dismiss | like | search | zero_result | mail_open | mail_click
    meta = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime, default=utcnow, index=True)


class SavedEvent(db.Model):
    __tablename__ = "saved_events"
    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey("families.id"), nullable=False, index=True)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False, index=True)
    wil_heen = db.Column(db.Boolean, default=True, nullable=False)   # "we willen hierheen"
    geweest = db.Column(db.Boolean, default=False, nullable=False)   # bevestigd bezoek
    gevraagd_geweest = db.Column(db.Boolean, default=False, nullable=False)  # al eens "waren jullie er?" getoond
    created_at = db.Column(db.DateTime, default=utcnow)
    __table_args__ = (db.UniqueConstraint("family_id", "event_id"),)
    event = db.relationship("Event")


REVIEW_TAGS = [
    "mooie natuur", "veel te doen", "top voor kleuters", "lekker eten",
    "dichtbij", "goed bij regen", "gratis parking", "verzorgingstafel",
    "vlot met de wandelwagen", "leuk voor tieners", "afgesloten speelterrein",
]

# Oude tag-bewoording -> nieuwe (migratie herschrijft bestaande reviews).
OUDE_TAGS = {"vlot met buggy": "vlot met de wandelwagen",
             "omheind terrein": "afgesloten speelterrein"}

# Review-tags die (bij voldoende bevestigingen) een fiche-veld aanzetten,
# zodat de community de ouder-filters vanzelf vult.
TAG_NAAR_VELD = {"verzorgingstafel": "verzorgingstafel",
                 "vlot met de wandelwagen": "buggy_ok",
                 "afgesloten speelterrein": "omheind"}
COST_RANGES = ["0", "<20", "20-50", "50-100", ">100"]


class Review(db.Model):
    """Ravotscore — anoniem publiek, één per gezin per event."""
    __tablename__ = "reviews"
    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey("families.id"), nullable=True, index=True)  # NULL na accountverwijdering
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False, index=True)
    kid_score = db.Column(db.Integer, nullable=False)      # 1..5 (smileys)
    parent_score = db.Column(db.Integer, nullable=False)   # 1=gedoe 2=oké 3=vlot
    cost_range = db.Column(db.String(8))                   # zie COST_RANGES
    # Karakter-schuifjes (1..5, neutraal — geen goed/fout):
    sfeer_rustig_actief = db.Column(db.Integer)            # 1=rustig .. 5=actief
    sfeer_prijs = db.Column(db.Integer)                    # 1=betaalbaar .. 5=prijzig
    sfeer_leeftijd = db.Column(db.Integer)                 # 1=kleuters .. 5=tieners
    tags = db.Column(db.JSON, default=list)
    child_ages = db.Column(db.JSON, default=list)          # snapshot leeftijden, anoniem
    created_at = db.Column(db.DateTime, default=utcnow)
    __table_args__ = (
        db.UniqueConstraint("family_id", "event_id", name="uq_review_family_event"),
    )
    event = db.relationship("Event", backref="reviews")

    def public_dict(self):
        """Wat naar buiten mag — bewust ZONDER family_id of identiteit."""
        ages = ", ".join(str(a) for a in (self.child_ages or []))
        return {
            "afzender": f"gezin met kinderen van {ages}" if ages else "een gezin",
            "kid_score": self.kid_score,
            "parent_score": self.parent_score,
            "cost_range": self.cost_range,
            "tags": self.tags or [],
        }


class Connection(db.Model):
    """Koppeling tussen gezinnen — enkel via uitnodiging + wederzijds akkoord."""
    __tablename__ = "connections"
    id = db.Column(db.Integer, primary_key=True)
    family_a = db.Column(db.Integer, db.ForeignKey("families.id"), nullable=False, index=True)
    family_b = db.Column(db.Integer, db.ForeignKey("families.id"), nullable=False, index=True)
    status = db.Column(db.String(12), default="pending")   # pending -> accepted
    requested_by = db.Column(db.Integer, db.ForeignKey("families.id"))  # wie stuurde de aanvraag
    created_at = db.Column(db.DateTime, default=utcnow)
    __table_args__ = (db.UniqueConstraint("family_a", "family_b"),)


class FriendInvite(db.Model):
    """Vervallende uitnodigingscode. Enkel de hash wordt bewaard."""
    __tablename__ = "friend_invites"
    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey("families.id"), nullable=False, index=True)
    code_hash = db.Column(db.String(64), nullable=False, unique=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow)


class Share(db.Model):
    """Interesse delen: per event, opt-in, standaard bestaat er geen rij (= uit)."""
    __tablename__ = "shares"
    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey("families.id"), nullable=False, index=True)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    __table_args__ = (db.UniqueConstraint("family_id", "event_id"),)


class MagicToken(db.Model):
    __tablename__ = "magic_tokens"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)  # sha256
    purpose = db.Column(db.String(16), default="login")
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime)
    attempts = db.Column(db.Integer, default=0, nullable=False)  # brute-force-slot voor codes
    created_at = db.Column(db.DateTime, default=utcnow)


class Admin(db.Model):
    __tablename__ = "admins"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    pw_hash = db.Column(db.String(255), nullable=False)   # scrypt (werkzeug)
    totp_secret = db.Column(db.String(64), nullable=False)  # verplichte 2FA
    totp_confirmed = db.Column(db.Boolean, default=False, nullable=False)  # QR gescand + code bevestigd
    role = db.Column(db.String(12), default="admin", nullable=False)  # admin | reviewer


class AuditLog(db.Model):
    """Alle adminacties gelogd (strategienota §8.1)."""
    __tablename__ = "audit_log"
    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, db.ForeignKey("admins.id"))
    action = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)


class Setting(db.Model):
    """Niet-geheime, in-app aanpasbare configuratie (key-value).
    BEWUST GEEN secrets hier: API-keys en SMTP-wachtwoorden blijven in .env.
    """
    __tablename__ = "settings"
    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


# Toegestane, niet-geheime instellingen met hun default + omschrijving.
# (key: (default, label, type)) — type: 'bool' | 'int' | 'text' | 'choice:a,b,c'
SETTING_DEFS = {
    "uit_query": ("typicalAgeRange:[0 TO 12]", "UiT-zoekquery (Search API q-parameter)", "text"),
    "sync_max_pages": ("200", "Max. pagina's per sync (×50 events)", "int"),
    "weekendmail_aan": ("1", "Weekendmail (donderdag) versturen", "bool"),
    "maandagmail_aan": ("1", "Maandagvraag-mail versturen", "bool"),
    "weer_aan": ("1", "Weerkoppeling gebruiken (regen → binnen omhoog)", "bool"),
    "default_radius": ("25", "Standaard actieradius voor nieuwe bezoekers (km)", "int"),
    # Tijdvenster: hoe ver vooruit tonen we activiteiten
    "toon_maanden_vooruit": ("24", "Toon activiteiten tot X maanden vooruit", "int"),
    # Weer-drempels
    "regen_drempel": ("50", "Regenkans (%) vanaf wanneer binnen-activiteiten voorrang krijgen", "int"),
    "zon_drempel": ("20", "Regenkans (%) waaronder buiten-activiteiten voorrang krijgen", "int"),
    # Ravotscore (Bayesiaans gewogen gemiddelde — wraak-preventie)
    "score_prior_n": ("3", "Score-demping: aantal 'onzichtbare' basisreviews", "int"),
    "score_prior_waarde": ("3.0", "Score-demping: basiswaarde (1-5)", "text"),
    # Beveiliging / limieten
    "codes_per_uur": ("3", "Max. inlogcodes per e-mailadres per uur", "int"),
    "ontdek_per_pagina": ("24", "Activiteiten per pagina op Ontdek", "int"),
    # Bronnen (welke datastromen syncen we?) — enkel kindvriendelijk aanbod
    # LET OP: publiq staat standaard UIT tot de live-koppeling is aangevraagd.
    # Deze schakelaar stuurt zowel het syncen ALS alle UiT/UiTinVlaanderen-
    # vermeldingen op de site (publiq-voorwaarden: attributie enkel bij live data).
    "bron_uit_aan": ("0", "Bron: UiTdatabank (publiq) — pas AAN zetten bij go-live", "bool"),
    "bron_osm_aan": ("0", "Bron: OpenStreetMap — speeltuinen, zoo, pretpark, musea", "bool"),
    "osm_tags": ("playground,park,nature_reserve,water_park,swimming_area,miniature_golf,"
                 "theme_park,zoo,aquarium,museum,viewpoint,attraction,castle,horeca",
                 "OSM: soorten plekken (komma-gescheiden; 'horeca' = enkel zaken met "
                 "kindvriendelijke voorzieningen)", "text"),
    "osm_horeca_aan": ("1", "OSM: kindvriendelijke horeca mee importeren "
                       "(enkel zaken met speelhoek/kinderstoel/verschoontafel)", "bool"),
    "osm_regios": ("vlaanderen",
                   "OSM: regio's (vlaanderen,brussel,wallonie,nederland,fr-nord)", "text"),
    "verrijk_backend": ("ollama", "AI-verrijking: backend (ollama | cloud)", "text"),
    "ollama_model": ("qwen2.5:7b", "AI-verrijking: lokaal model (Ollama)", "text"),
    "cloud_model": ("claude-haiku-4-5-20251001", "AI-verrijking: cloud-model (indien backend=cloud)", "text"),
    "partner_prijs_maand": ("19.00", "Ravot Partner: prijs per maand (EUR)", "text"),
    "partner_prijs_jaar": ("190.00", "Ravot Partner: prijs per jaar (EUR, excl. btw)", "text"),
    "partner_btw_pct": ("21", "Ravot Partner: btw-percentage", "text"),
    "odoo_product_id": ("", "Odoo: product-id voor Partner-facturen (aanbevolen: product met 21% btw)", "text"),
    "odoo_factuur_auto": ("0", "Odoo: factuur meteen valideren (1) of als concept klaarzetten (0)", "bool"),
    "founding_aan": ("1", "Founding partners: gratis eerste jaar aanbieden", "bool"),
    "founding_max": ("20", "Founding partners: maximum aantal plaatsen", "int"),
    "kwaliteit_min_lijst": ("30", "Kwaliteit: minimumscore om in lijsten/gemeentepagina's te staan (kaart toont alles)", "int"),
    "kwaliteit_hoog": ("60", "Kwaliteit: score vanaf wanneer een fiche voorrang krijgt", "int"),
    "verborgen_types": ("", "Types die publiek verborgen zijn (komma-gescheiden codes)", "text"),
    "enkel_gecureerd": ("0", "Toon publiek enkel door mensen goedgekeurde ('Ravot-waardige') plekken", "bool"),
    "report_drempel": ("3", "Aantal meldingen waarna een fiche automatisch naar nazicht springt", "int"),
    # Onderhoud: publieke site offline; /beheer en ingelogde admins blijven werken
    "onderhoud_aan": ("0", "Onderhoudsmodus: publieke site offline (beheer blijft bereikbaar)", "bool"),
    # Verjaardagsfeestjes
    "feestjes_aan": ("0", "Feestjesmodule: gezinnen kunnen offertes aanvragen (pas aanzetten met genoeg partners)", "bool"),
    "feest_straal_km": ("20", "Feestjes: standaard zoekstraal (km)", "int"),
    "feest_enkel_partners": ("0", "Feestjes: enkel betalende partners tonen (uit = ook gratis claims, partners bovenaan)", "bool"),
    "feest_max_aanvragen": ("6", "Feestjes: max. partners per offerteronde", "int"),
    # Ravotscore × Partner (commerciële plekken): score is en blijft van de
    # community; een actieve Partner mag ze tonen + laten meetellen in de
    # volgorde. Commercieel zonder Partner krijgt een lichte demping.
    "partner_score_bonus": ("1.10", "Commercieel mét Partner: rankingfactor (bv. 1.10)", "text"),
    "geen_partner_malus": ("0.90", "Commercieel zonder Partner: rankingfactor (bv. 0.90)", "text"),
    "foto_malus": ("0.92", "Fiches zonder foto: rankingfactor (bv. 0.92)", "text"),
    # Beloningen (Ravotpas): promomateriaal en partner-goodies
    "beloningen_aan": ("1", "Beloningenwinkel: punten inwisselen voor goodies", "bool"),
    "punt_waarde_eur": ("0.05", "Richtwaarde van 1 ravotpunt in euro (prijs = euro x 20)", "text"),
    "punten_geldig_maanden": ("6", "Punten vervallen na X maanden (0 = nooit; niveau en badges vervallen nooit)", "int"),
    # Anti-misbruik (puntenfarming): plafonds per gezin per dag + wisseldrempel
    "punten_dag_max": ("60", "Max. ravotpunten per gezin per dag (± 2 uitstappen)", "int"),
    "geweest_dag_max": ("3", "Max. beloonde bezoeken ('geweest') per gezin per dag", "int"),
    "wissel_min_dagen": ("7", "Inwisselen kan pas vanaf een account van X dagen oud", "int"),
    # Community-filters: vanaf hoeveel gezinnen met dezelfde tag zetten we het
    # bijhorende fiche-veld (verzorgingstafel, buggy, omheind) automatisch aan?
    "tag_drempel": ("2", "Ouder-filters: aantal reviews met tag vóór auto-aanzetten", "int"),
}


def get_setting(key):
    """Waarde uit DB, of de default. Nooit een exception."""
    default = SETTING_DEFS.get(key, ("", "", "text"))[0]
    try:
        row = db.session.get(Setting, key)
        return row.value if row and row.value is not None else default
    except Exception:
        return default


def get_bool(key):
    return str(get_setting(key)).lower() in ("1", "true", "on", "yes")


def get_int(key, fallback=0):
    try:
        return int(get_setting(key))
    except (ValueError, TypeError):
        return fallback


class ContentPage(db.Model):
    """Bewerkbare inhoudspagina (privacy, over-ons, voorwaarden, hoe-werkt-het).
    Inhoud als Markdown — veilig, geen rauwe HTML van de gebruiker."""
    __tablename__ = "content_pages"
    slug = db.Column(db.String(40), primary_key=True)   # 'privacy', 'over', ...
    titel = db.Column(db.String(120), nullable=False)
    inhoud_md = db.Column(db.Text, default="")          # Markdown
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


# Standaardpagina's die in de admin verschijnen (slug: titel).
CONTENT_PAGES = {
    "over": "Over Ravot",
    "privacy": "Privacyverklaring",
    "cookies": "Cookiebeleid",
    "voorwaarden": "Gebruiksvoorwaarden",
    "contact": "Contact",
    "hoe": "Zo werkt Ravot",
}


class MailTemplate(db.Model):
    """Bewerkbare mailtekst. {placeholders} blijven behouden."""
    __tablename__ = "mail_templates"
    slug = db.Column(db.String(40), primary_key=True)   # 'inlogcode', 'weekend', 'maandag'
    naam = db.Column(db.String(120), nullable=False)
    onderwerp = db.Column(db.String(200), default="")
    inhoud_md = db.Column(db.Text, default="")
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


# Mailsjablonen die in de admin verschijnen, met hun beschikbare placeholders.
MAIL_TEMPLATES = {
    "inlogcode": ("Inlogcode", "{code}"),
    "weekend": ("Weekendmail", "{naam}, {activiteiten}"),
    "maandag": ("Maandagvraag", "{naam}"),
    "feestje_offerte": ("Feestje: offerteaanvraag naar partner",
                        "{plek}, {datum}, {leeftijd}, {aantal}, {gemeente}, {budget}, {wensen}"),
}


class PostcodeCentroid(db.Model):
    """Zwaartepunt per postcode, afgeleid uit gesynchroniseerde events —
    zo hebben we afstandsberekening zonder externe geocoding of adresdata."""
    __tablename__ = "postcode_centroids"
    postcode = db.Column(db.String(4), primary_key=True)
    gemeente = db.Column(db.String(80))
    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)
    n_events = db.Column(db.Integer, default=0)


class SyncStatus(db.Model):
    """Status van elke databron, zodat sync/verwijderen vanuit de admin kan
    en de beheerder de laatste run + resultaat ziet."""
    __tablename__ = "sync_status"
    source = db.Column(db.String(16), primary_key=True)   # uit|tm|tv|osm
    state = db.Column(db.String(12), default="idle", nullable=False)  # idle|running|done|error
    last_run = db.Column(db.DateTime)
    last_result = db.Column(db.String(200))
    last_error = db.Column(db.String(300))
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


class Report(db.Model):
    """Gebruikersmelding over een plek: gesloten, foute info, ... -> naar admin."""
    __tablename__ = "reports"
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False, index=True)
    family_id = db.Column(db.Integer, db.ForeignKey("families.id"))   # None = anoniem
    reason = db.Column(db.String(24), nullable=False)   # gesloten | fout | ongepast | anders
    note = db.Column(db.String(500))
    handled = db.Column(db.Boolean, default=False, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, index=True)
    event = db.relationship("Event")


REPORT_REASONS = {
    "gesloten": "Deze plek bestaat niet meer / is gesloten",
    "fout": "De informatie klopt niet",
    "ongepast": "Niet geschikt voor kinderen",
    "anders": "Iets anders",
}


class Photo(db.Model):
    """Door een gebruiker geuploade foto van een plek. Staat standaard in de
    moderatiewachtrij (pending) en is pas publiek zichtbaar na goedkeuring."""
    __tablename__ = "photos"
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False, index=True)
    family_id = db.Column(db.Integer, db.ForeignKey("families.id"))   # uploader
    filename = db.Column(db.String(120), nullable=False)   # veilige, zelfgekozen bestandsnaam
    # gezin (galerij) | kindermenu | kinderhoek — de laatste twee zijn
    # uitbater-uploads en krijgen een eigen blok op de fiche.
    soort = db.Column(db.String(12), default="gezin", nullable=False)
    status = db.Column(db.String(12), default="pending", nullable=False, index=True)  # pending|approved|rejected
    created_at = db.Column(db.DateTime, default=utcnow, index=True)
    event = db.relationship("Event")


class EnrichProposal(db.Model):
    """Een AI-voorstel tot verrijking van een plek. Wacht op menselijke
    goedkeuring; pas dan worden de velden op het event toegepast."""
    __tablename__ = "enrich_proposals"
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False, index=True)
    beschrijving = db.Column(db.Text)
    categorie = db.Column(db.String(32))
    leeftijd_min = db.Column(db.Integer)
    leeftijd_max = db.Column(db.Integer)
    binnen = db.Column(db.Boolean)
    status = db.Column(db.String(12), default="pending", nullable=False, index=True)  # pending|approved|rejected
    created_at = db.Column(db.DateTime, default=utcnow, index=True)
    event = db.relationship("Event")


class Operator(db.Model):
    """Uitbater (horeca, museum, speeltuin, ...) die zijn zaak claimt op Ravot.
    Wachtwoordloos: inloggen via e-mailcode, net zoals gezinnen."""
    __tablename__ = "operators"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(120))
    # Facturatiegegevens (verplicht vóór Partner-aankoop; B2B/Peppol via Odoo).
    bedrijfsnaam = db.Column(db.String(160))
    btw_nummer = db.Column(db.String(20))     # BE0123456789
    straat = db.Column(db.String(160))
    postcode = db.Column(db.String(8))
    gemeente = db.Column(db.String(80))
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)


class OperatorClaim(db.Model):
    """Claim van een uitbater op een plek. Pas na goedkeuring (Nazicht) mag de
    uitbater fichewijzigingen voorstellen voor die plek."""
    __tablename__ = "operator_claims"
    id = db.Column(db.Integer, primary_key=True)
    operator_id = db.Column(db.Integer, db.ForeignKey("operators.id"), nullable=False, index=True)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False, index=True)
    note = db.Column(db.String(500))   # hoe toont de uitbater dat de zaak van hem is
    status = db.Column(db.String(12), default="pending", nullable=False, index=True)  # pending|approved|rejected
    created_at = db.Column(db.DateTime, default=utcnow)
    operator = db.relationship("Operator")
    event = db.relationship("Event")


# Velden die een uitbater mag voorstellen te wijzigen (whitelist).
EDIT_VELDEN = ("description", "adres", "postcode", "gemeente", "source_url",
               "indoor", "is_free", "feest", "feest_soorten", "feest_contact")


class EditProposal(db.Model):
    """Voorgestelde fichewijziging door een uitbater -> Nazicht -> toepassen."""
    __tablename__ = "edit_proposals"
    id = db.Column(db.Integer, primary_key=True)
    operator_id = db.Column(db.Integer, db.ForeignKey("operators.id"), nullable=False, index=True)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False, index=True)
    changes = db.Column(db.JSON, default=dict)   # {veld: nieuwe waarde} (enkel EDIT_VELDEN)
    status = db.Column(db.String(12), default="pending", nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    operator = db.relationship("Operator")
    event = db.relationship("Event")


class PartnerPayment(db.Model):
    """Mollie-betaling voor het Ravot Partner-niveau van één zaak.
    Status wordt UITSLUITEND gezet na verificatie bij Mollie zelf (nooit op
    basis van de webhook-body) — dat is Mollies beveiligingsmodel."""
    __tablename__ = "partner_payments"
    id = db.Column(db.Integer, primary_key=True)
    operator_id = db.Column(db.Integer, db.ForeignKey("operators.id"), nullable=False, index=True)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False, index=True)
    mollie_id = db.Column(db.String(64), unique=True, index=True)   # tr_...
    plan = db.Column(db.String(8), nullable=False)                  # maand | jaar
    amount = db.Column(db.String(12), nullable=False)               # "19.00"
    status = db.Column(db.String(16), default="open", nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, index=True)
    paid_at = db.Column(db.DateTime)
    odoo_invoice_id = db.Column(db.Integer)          # account.move-id in Odoo
    odoo_invoice_ref = db.Column(db.String(40))      # bv. INV/2026/0042 of DRAFT
    operator = db.relationship("Operator")
    event = db.relationship("Event")


class Feed(db.Model):
    """Een vertrouwde agenda-feed (iCal of RSS) van bv. een cultuurcentrum of
    toeristische dienst. Beheerder voegt ze toe; de feeds-adapter haalt ze op.
    'trusted' = events van deze feed komen meteen live (anders in de wachtrij)."""
    __tablename__ = "feeds"
    id = db.Column(db.Integer, primary_key=True)
    naam = db.Column(db.String(160), nullable=False)      # "CC De Spil, Roeselare"
    url = db.Column(db.String(500), nullable=False)
    kind = db.Column(db.String(8), default="ical", nullable=False)  # ical | rss
    gemeente = db.Column(db.String(80))                   # standaardgemeente als de feed er geen geeft
    postcode = db.Column(db.String(8))
    categorie = db.Column(db.String(40))                  # standaardcategorie (bv. "cultuur")
    trusted = db.Column(db.Boolean, default=True, nullable=False)
    actief = db.Column(db.Boolean, default=True, nullable=False)
    last_run = db.Column(db.DateTime)
    last_result = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=utcnow)


class DagUitstap(db.Model):
    """Zelf samengestelde daguitstap van een gezin: een geordend lijstje
    plekken/activiteiten, opslaanbaar en (optioneel) deelbaar via link."""
    __tablename__ = "daguitstappen"
    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey("families.id"), nullable=False, index=True)
    titel = db.Column(db.String(120), nullable=False)
    datum = db.Column(db.Date)                      # optioneel: geplande dag
    share_token = db.Column(db.String(24), unique=True, index=True)  # None = niet gedeeld
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    items = db.relationship("DagUitstapItem", backref="daguitstap",
                            order_by="DagUitstapItem.volgorde",
                            cascade="all, delete-orphan")


class DagUitstapItem(db.Model):
    __tablename__ = "daguitstap_items"
    id = db.Column(db.Integer, primary_key=True)
    daguitstap_id = db.Column(db.Integer, db.ForeignKey("daguitstappen.id"),
                              nullable=False, index=True)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)
    volgorde = db.Column(db.Integer, default=0, nullable=False)
    nota = db.Column(db.String(200))                # "hier lunchen", "reserveren!"
    event = db.relationship("Event")
    __table_args__ = (db.UniqueConstraint("daguitstap_id", "event_id"),)


# Wat een feestpartner kan aanbieden (code -> label).
FEEST_SOORTEN = {
    "horeca": "Eten & drinken (horeca)",
    "zaal": "Zaalverhuur",
    "materiaal": "Materiaalverhuur (springkasteel, ...)",
    "animatie": "Activiteit of animatie",
}


FEEST_AANLEIDINGEN = {
    "verjaardag": ("🎂", "Verjaardagsfeestje"),
    "communie": ("🕊️", "Eerste communie"),
    "lentefeest": ("🌸", "Lentefeest / plechtige communie"),
    "ander": ("🎉", "Ander kinderfeest"),
}


class Feestje(db.Model):
    """Kinderfeest in voorbereiding (verjaardag, communie, lentefeest, ...).
    Privacy: geen kindnaam — enkel de leeftijd op de feestdag."""
    __tablename__ = "feestjes"
    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey("families.id"), nullable=False, index=True)
    aanleiding = db.Column(db.String(12), default="verjaardag", nullable=False)
    leeftijd = db.Column(db.Integer)                # leeftijd op de feestdag
    datum = db.Column(db.Date, nullable=False)
    aantal_kinderen = db.Column(db.Integer, default=8, nullable=False)
    postcode = db.Column(db.String(4))              # zoekcentrum
    gemeente = db.Column(db.String(80))
    straal_km = db.Column(db.Integer, default=20, nullable=False)
    budget = db.Column(db.String(12))               # vrije indicatie, bv. "150-250"
    wensen = db.Column(db.String(600))              # thema, allergieën, ...
    status = db.Column(db.String(12), default="open", nullable=False, index=True)  # open|geregeld|geannuleerd
    created_at = db.Column(db.DateTime, default=utcnow)
    aanvragen = db.relationship("FeestjeAanvraag", backref="feestje",
                                cascade="all, delete-orphan")


class FeestjeAanvraag(db.Model):
    """Eén offerteaanvraag: feestje -> feestpartner (plek). De mail vertrekt
    met reply-to naar het gezin; het gezin volgt de status zelf op."""
    __tablename__ = "feestje_aanvragen"
    id = db.Column(db.Integer, primary_key=True)
    feestje_id = db.Column(db.Integer, db.ForeignKey("feestjes.id"), nullable=False, index=True)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False, index=True)
    status = db.Column(db.String(12), default="verstuurd", nullable=False)  # verstuurd|beantwoord|bevestigd|afgewezen
    verzonden_at = db.Column(db.DateTime, default=utcnow)
    event = db.relationship("Event")
    __table_args__ = (db.UniqueConstraint("feestje_id", "event_id"),)


FEESTJE_STATUSSEN = {"verstuurd": "📨 verstuurd", "beantwoord": "💬 beantwoord",
                     "bevestigd": "✅ bevestigd", "afgewezen": "❌ niet gelukt"}


# --- Ravotpas (gamification) --------------------------------------------------
# Punten per actie. Dedupe: één keer per (gezin, reden, event).
PUNT_REDENEN = {
    "review": 10,          # Ravotscore gegeven
    "foto": 15,            # foto goedgekeurd
    "eerste_foto": 10,     # extra: de állereerste foto van die plek
    "geweest": 5,          # bezoek bevestigd
    "daguitstap": 5,       # daguitstap samengesteld
    "feestje": 10,         # verjaardagsfeestje georganiseerd via Ravot
    "plek": 15,            # zelf een plek toegevoegd die live ging
}


class RavotPunt(db.Model):
    """Puntengrootboek van de Ravotpas. Bewust een logboek (geen teller op
    Family): uitlegbaar, auditeerbaar en dubbeltellingen zijn uitgesloten."""
    __tablename__ = "ravot_punten"
    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey("families.id"), nullable=False, index=True)
    punten = db.Column(db.Integer, nullable=False)
    reden = db.Column(db.String(20), nullable=False)
    # Generieke referentie: event-id, daguitstap-id of feestje-id — bewust geen
    # FK, zodat de dedupe-sleutel voor alle soorten acties werkt.
    ref_id = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, index=True)
    __table_args__ = (db.UniqueConstraint("family_id", "reden", "ref_id",
                                          name="uq_punt_family_reden_ref"),)


class HorecaKandidaat(db.Model):
    """Staging voor de Horeca-verkenner: kandidaten uit Overture Maps.
    Nog géén fiches — de beheerder kiest wat Ravot-waardig is."""
    __tablename__ = "horeca_kandidaten"
    id = db.Column(db.Integer, primary_key=True)
    ext_id = db.Column(db.String(64), unique=True, nullable=False, index=True)
    naam = db.Column(db.String(200), nullable=False)
    categorie = db.Column(db.String(80))
    adres = db.Column(db.String(200))
    gemeente = db.Column(db.String(80), index=True)
    postcode = db.Column(db.String(8))
    lat = db.Column(db.Float, index=True)
    lng = db.Column(db.Float, index=True)
    website = db.Column(db.String(300))
    telefoon = db.Column(db.String(40))
    email = db.Column(db.String(255))
    zomerbar_hint = db.Column(db.Boolean, default=False)
    winterbar_hint = db.Column(db.Boolean, default=False)
    confidence = db.Column(db.Float)
    # AI-voorsortering: 'ja' | 'nee' | 'twijfel' (None = nog niet beoordeeld).
    # Eén keer beoordeeld = voor altijd bewaard, mét korte motivatie zodat de
    # beheerder kan zien HOE het model tot zijn oordeel kwam.
    ai_advies = db.Column(db.String(8))
    ai_uitleg = db.Column(db.String(200))
    # Door de beheerder gemarkeerd als "bestaat niet meer": nooit meer tonen.
    gesloten = db.Column(db.Boolean, default=False)
    # 'gezin' = kaart-flow (kindvriendelijke horeca) · 'feest' = feestprospect
    # (traiteur, feestzaal, cateraar — niet op de kaart, wel voor werving).
    # Een zaak kan beide zijn (restaurant dat ook communies doet): dan staat ze
    # als gezin op de kaart én als feestprospect in de lijst.
    doel = db.Column(db.String(8), default="gezin", index=True)
    is_feest = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utcnow)


class Beloning(db.Model):
    """Catalogus van beloningen: Ravot-promomateriaal en partner-goodies.
    Vuistregel voor een gerechtvaardigde ravotwaarde: punten = euro x 20
    (1 punt = €0,05 — een actieve uitstap van ~30 punten is zo ~€1,50 waard)."""
    __tablename__ = "beloningen"
    id = db.Column(db.Integer, primary_key=True)
    emoji = db.Column(db.String(8), default="🎁")
    naam = db.Column(db.String(120), nullable=False)
    beschrijving = db.Column(db.String(300))
    soort = db.Column(db.String(10), default="ravot", nullable=False)  # ravot|partner
    partner_event_id = db.Column(db.Integer, db.ForeignKey("events.id"))
    punten = db.Column(db.Integer, nullable=False)
    waarde_eur = db.Column(db.Float, nullable=False, default=0)
    voorraad = db.Column(db.Integer)          # None = onbeperkt
    actief = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    partner = db.relationship("Event")


class Inwissel(db.Model):
    """Eén inwisseling: gezin ruilt punten voor een beloning. De code toont
    het gezin bij afhaling/verzending; de beheerder volgt de status op."""
    __tablename__ = "inwisselingen"
    id = db.Column(db.Integer, primary_key=True)
    family_id = db.Column(db.Integer, db.ForeignKey("families.id"),
                          nullable=False, index=True)
    beloning_id = db.Column(db.Integer, db.ForeignKey("beloningen.id"),
                            nullable=False)
    punten = db.Column(db.Integer, nullable=False)   # prijs op moment van wissel
    # Bezorgadres: enkel gevraagd bij fysieke Ravot-goodies, op het moment van
    # inwisselen (privacy by design: geen adressen "voor het geval dat").
    bezorg_adres = db.Column(db.String(300))
    code = db.Column(db.String(16), unique=True, nullable=False)
    status = db.Column(db.String(12), default="aangevraagd", nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    beloning = db.relationship("Beloning")


INWISSEL_STATUSSEN = {"aangevraagd": "⏳ aangevraagd", "verzonden": "📦 onderweg",
                      "opgehaald": "✅ ontvangen", "geannuleerd": "❌ geannuleerd"}


class GeoCache(db.Model):
    """Gecachte geocoding (plaatsnaam -> coördinaten), zodat we per plaats maar
    één keer een externe geocoder bevragen."""
    __tablename__ = "geo_cache"
    term = db.Column(db.String(120), primary_key=True)
    lat = db.Column(db.Float)
    lng = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=utcnow)
