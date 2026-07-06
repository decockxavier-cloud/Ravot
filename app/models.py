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
    uit_id = db.Column(db.String(64), unique=True, index=True)
    source = db.Column(db.String(16), default="uit", nullable=False)  # 2e bron later
    slug = db.Column(db.String(300), unique=True, index=True)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    start = db.Column(db.DateTime, index=True)
    end = db.Column(db.DateTime, index=True)
    gemeente = db.Column(db.String(80), index=True)
    postcode = db.Column(db.String(4), index=True)
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


class PostcodeCentroid(db.Model):
    """Zwaartepunt per postcode, afgeleid uit gesynchroniseerde events —
    zo hebben we afstandsberekening zonder externe geocoding of adresdata."""
    __tablename__ = "postcode_centroids"
    postcode = db.Column(db.String(4), primary_key=True)
    gemeente = db.Column(db.String(80))
    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)
    n_events = db.Column(db.Integer, default=0)
