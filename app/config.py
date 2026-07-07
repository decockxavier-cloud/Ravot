import os
from datetime import timedelta


class Config:
    # -- Kern --------------------------------------------------------------
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-verander-mij")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///ravot_dev.sqlite3"
    )
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # -- Cookies / sessies ---------------------------------------------------
    SESSION_COOKIE_SECURE = os.environ.get("FLASK_ENV") != "development"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(days=365)  # "onthoud mij" standaard

    # -- Mail ----------------------------------------------------------------
    MAIL_FROM = os.environ.get("MAIL_FROM", "Ravot <hallo@ravot.be>")
    SMTP_HOST = os.environ.get("SMTP_HOST", "")  # leeg = console-mailer (dev)
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASS = os.environ.get("SMTP_PASS", "")

    # -- UiTdatabank Search API ----------------------------------------------
    UIT_API_KEY = os.environ.get("UIT_API_KEY", "")
    UIT_SEARCH_URL = os.environ.get(
        "UIT_SEARCH_URL", "https://search-test.uitdatabank.be"
    )  # productie: https://search.uitdatabank.be

    # -- Extra bronnen (allemaal optioneel; enkel kindvriendelijk aanbod) -----
    # Ticketmaster Discovery API — enkel Family-segment, landcode BE.
    TICKETMASTER_API_KEY = os.environ.get("TICKETMASTER_API_KEY", "")
    TICKETMASTER_URL = os.environ.get(
        "TICKETMASTER_URL", "https://app.ticketmaster.com/discovery/v2")
    # Toerisme Vlaanderen Linked Open Data — JSON:API, geen key nodig.
    TOERISME_URL = os.environ.get(
        "TOERISME_URL", "https://linked.toerismevlaanderen.be")
    # OpenStreetMap Overpass — geen key nodig.
    OVERPASS_URL = os.environ.get(
        "OVERPASS_URL", "https://overpass-api.de/api/interpreter")
    # Wikidata SPARQL — geen key nodig (wel een nette User-Agent).
    WIKIDATA_SPARQL_URL = os.environ.get(
        "WIKIDATA_SPARQL_URL", "https://query.wikidata.org/sparql")
    # Nominatim (OSM-geocoder) voor plaatsnaam -> coördinaten. Geen key nodig.
    NOMINATIM_URL = os.environ.get(
        "NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")

    # -- Site ------------------------------------------------------------------
    SITE_URL = os.environ.get("SITE_URL", "http://localhost:5000")
    MAGIC_LINK_MINUTES = 15
    MAGIC_REQUESTS_PER_HOUR = 3
    NOINDEX_MIN_EVENTS = 3          # SEO-drempel tegen thin content
    K_ANONYMITY = 20                # geen statistiekcel onder deze drempel


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    WTF_CSRF_ENABLED = False
    SESSION_COOKIE_SECURE = False
    RATELIMIT_ENABLED = False
