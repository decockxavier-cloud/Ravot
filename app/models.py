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
    attribution = db.Column(db.String(120))          # korte bronvermelding (licentie-compliance)
    is_permanent = db.Column(db.Boolean, default=False, nullable=False, index=True)  # POI zonder vaste datum
    hidden = db.Column(db.Boolean, default=False, nullable=False, index=True)  # dubbel: verborgen in lijsten
    dupe_of = db.Column(db.Integer, index=True)   # id van het canonieke event waar dit een dubbel van is
    pending = db.Column(db.Boolean, default=False, nullable=False, index=True)  # door gebruiker ingediend, wacht op review
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
    "vlot met buggy", "leuk voor tieners",
]
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
    "bron_tm_aan": ("0", "Bron: Ticketmaster — enkel Family-segment (BE)", "bool"),
    "bron_tv_aan": ("0", "Bron: Toerisme Vlaanderen — kindvriendelijke attracties", "bool"),
    "bron_osm_aan": ("0", "Bron: OpenStreetMap — speeltuinen, zoo, pretpark, musea", "bool"),
    "bron_feed_aan": ("0", "Bron: agenda-feeds (iCal/RSS)", "bool"),
    "bron_wd_aan": ("0", "Bron: Wikidata — musea/attracties met officiële foto's", "bool"),
    "tv_max": ("2000", "Toerisme Vlaanderen: max. attracties per sync", "int"),
    "osm_tags": ("playground,park,nature_reserve,water_park,swimming_area,miniature_golf,"
                 "theme_park,zoo,aquarium,museum,viewpoint,attraction,castle",
                 "OSM: soorten plekken (komma-gescheiden)", "text"),
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
               "indoor", "is_free")


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


class GeoCache(db.Model):
    """Gecachte geocoding (plaatsnaam -> coördinaten), zodat we per plaats maar
    één keer een externe geocoder bevragen."""
    __tablename__ = "geo_cache"
    term = db.Column(db.String(120), primary_key=True)
    lat = db.Column(db.Float)
    lng = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=utcnow)
