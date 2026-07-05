"""Adminpaneel — afgeschermd pad, wachtwoord (Argon2id) + verplichte TOTP-2FA,
aparte sessievlag, alle acties in de audit log (strategienota §8.1)."""
from functools import wraps

import pyotp
from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   session, url_for)
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_ph = PasswordHasher()  # Argon2id (default)

from ..extensions import db, limiter
from ..models import Admin, AuditLog, Event, Family, Interaction, Review

bp = Blueprint("admin", __name__, url_prefix="/beheer")


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin_id") or not session.get("admin_2fa_ok"):
            return redirect(url_for("admin.login"))
        return f(*args, **kwargs)
    return wrapper


def audit(action):
    db.session.add(AuditLog(admin_id=session.get("admin_id"), action=action))
    db.session.commit()


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10/hour", methods=["POST"])
def login():
    if request.method == "POST":
        admin = Admin.query.filter_by(email=request.form.get("email", "").lower().strip()).first()
        ok = False
        if admin:
            try:
                ok = _ph.verify(admin.pw_hash, request.form.get("password", ""))
            except VerifyMismatchError:
                ok = False
        if ok:
            session["admin_id"] = admin.id
            session["admin_2fa_ok"] = False
            # Nog geen bevestigde 2FA → verplichte enrollment met QR-code.
            if not admin.totp_confirmed:
                return redirect(url_for("admin.tweefa_instellen"))
            return redirect(url_for("admin.otp"))
        flash("Onjuiste gegevens.", "error")
    return render_template("admin/login.html", title="Beheer", family=None, active=None)


@bp.route("/2fa-instellen", methods=["GET", "POST"])
@limiter.limit("15/hour")
def tweefa_instellen():
    """Verplichte 2FA-enrollment: toon QR, bevestig eerste code, dan pas toegang.
    Bereikbaar na wachtwoord-login, zolang totp_confirmed nog False is."""
    if not session.get("admin_id"):
        return redirect(url_for("admin.login"))
    admin = db.session.get(Admin, session["admin_id"])
    if admin is None:
        return redirect(url_for("admin.login"))
    if admin.totp_confirmed:  # al ingesteld → niets te doen hier
        return redirect(url_for("admin.otp"))

    if request.method == "POST":
        totp = pyotp.TOTP(admin.totp_secret)
        if totp.verify(request.form.get("code", ""), valid_window=1):
            admin.totp_confirmed = True
            db.session.commit()
            session["admin_2fa_ok"] = True
            audit("2fa ingesteld + login")
            flash("Tweestapsverificatie is ingesteld. 🎉", "ok")
            return redirect(url_for("admin.dashboard"))
        flash("Die code klopt niet. Scan de QR opnieuw en probeer een verse code.", "error")

    # QR-code (SVG) server-side genereren — niets externs, geen tracking.
    import segno
    uri = pyotp.TOTP(admin.totp_secret).provisioning_uri(
        name=admin.email, issuer_name="Ravot Beheer")
    qr = segno.make(uri, error="m")
    svg = qr.svg_inline(scale=5, border=2, dark="#1F3A2A", light="#ffffff")
    return render_template("admin/tweefa_instellen.html", qr_svg=svg,
                           secret=admin.totp_secret, title="Stel 2FA in",
                           family=None, active=None)


@bp.route("/otp", methods=["GET", "POST"])
@limiter.limit("10/hour", methods=["POST"])
def otp():
    if not session.get("admin_id"):
        return redirect(url_for("admin.login"))
    admin = db.session.get(Admin, session["admin_id"])
    if admin is None:
        return redirect(url_for("admin.login"))
    if not admin.totp_confirmed:  # nog niet ingeschreven → naar QR-flow
        return redirect(url_for("admin.tweefa_instellen"))
    if request.method == "POST":
        totp = pyotp.TOTP(admin.totp_secret)
        if totp.verify(request.form.get("code", ""), valid_window=1):
            session["admin_2fa_ok"] = True
            audit("login")
            return redirect(url_for("admin.dashboard"))
        flash("Onjuiste code.", "error")
    return render_template("admin/otp.html", title="Tweestapsverificatie",
                           family=None, active=None)


@bp.route("/")
@admin_required
def dashboard():
    stats = {
        "gezinnen": Family.query.count(),
        "events": Event.query.count(),
        "reviews": Review.query.count(),
        "interacties_vandaag": Interaction.query.filter(
            db.func.date(Interaction.created_at) == db.func.current_date()).count(),
        "nieuwsbrief": Family.query.filter_by(newsletter_opt_in=True).count(),
    }
    zero = db.session.query(Interaction.meta, db.func.count(Interaction.id)) \
        .filter(Interaction.type == "zero_result") \
        .group_by(Interaction.meta).limit(20).all()
    recent_reviews = Review.query.order_by(Review.created_at.desc()).limit(20).all()
    return render_template("admin/dashboard.html", stats=stats, zero=zero,
                           reviews=recent_reviews, title="Ravot Beheer",
                           family=None, active=None)


@bp.route("/review/<int:review_id>/verwijder", methods=["POST"])
@admin_required
def delete_review(review_id):
    rv = db.session.get(Review, review_id) or abort(404)
    audit(f"review {review_id} verwijderd (event {rv.event_id})")
    db.session.delete(rv)
    db.session.commit()
    flash("Review verwijderd.", "ok")
    return redirect(url_for("admin.dashboard"))


@bp.route("/instellingen", methods=["GET", "POST"])
@admin_required
def instellingen():
    """Niet-geheime configuratie beheren. Secrets staan bewust NIET hier."""
    from ..models import Setting, SETTING_DEFS, get_setting
    if request.method == "POST":
        gewijzigd = []
        for key, (default, label, typ) in SETTING_DEFS.items():
            if typ == "bool":
                nieuw = "1" if request.form.get(key) == "on" else "0"
            else:
                nieuw = (request.form.get(key) or "").strip()
                if typ == "int" and not nieuw.isdigit():
                    continue  # ongeldige int negeren
            row = db.session.get(Setting, key)
            if row is None:
                row = Setting(key=key)
                db.session.add(row)
            if row.value != nieuw:
                gewijzigd.append(key)
            row.value = nieuw
        db.session.commit()
        if gewijzigd:
            audit("instellingen gewijzigd: " + ", ".join(gewijzigd))
        flash("Instellingen bewaard.", "ok")
        return redirect(url_for("admin.instellingen"))
    waarden = {key: get_setting(key) for key in SETTING_DEFS}
    return render_template("admin/instellingen.html", defs=SETTING_DEFS,
                           waarden=waarden, title="Instellingen",
                           family=None, active=None)


@bp.route("/verbindingen")
@admin_required
def verbindingen():
    """Statusoverzicht van externe diensten. Toont GEEN secrets, enkel of ze
    geconfigureerd zijn en werken."""
    from flask import current_app
    cfg = current_app.config
    # UiT: is er een key en staat de URL op test of productie?
    uit_url = cfg.get("UIT_SEARCH_URL", "")
    status = {
        "uit": {
            "geconfigureerd": bool(cfg.get("UIT_API_KEY")),
            "omgeving": "productie" if "search.uitdatabank" in uit_url and "test" not in uit_url else "test",
            "url": uit_url,
            "laatste_event": None,
            "aantal_events": Event.query.count(),
        },
        "smtp": {
            "geconfigureerd": bool(cfg.get("SMTP_HOST")),
            "host": cfg.get("SMTP_HOST") or "(console-modus — mails naar log)",
            "afzender": cfg.get("MAIL_FROM", ""),
        },
    }
    laatste = Event.query.order_by(Event.updated_at.desc()).first()
    if laatste:
        status["uit"]["laatste_event"] = laatste.updated_at
    return render_template("admin/verbindingen.html", status=status,
                           title="Verbindingen", family=None, active=None)


@bp.route("/test-uit", methods=["POST"])
@admin_required
@limiter.limit("10/hour")
def test_uit():
    """Test de UiT-verbinding met één kale call. Toont GEEN key."""
    import requests
    from flask import current_app
    cfg = current_app.config
    try:
        r = requests.get(f"{cfg['UIT_SEARCH_URL']}/events",
                         params={"clientId": cfg["UIT_API_KEY"], "limit": 1},
                         timeout=8)
        if r.status_code == 200:
            n = r.json().get("totalItems", "?")
            flash(f"UiT-verbinding OK ✅ — {n} events beschikbaar.", "ok")
        else:
            flash(f"UiT antwoordde met status {r.status_code}. Controleer de key in .env.", "error")
    except Exception as exc:
        flash(f"UiT niet bereikbaar: {str(exc)[:120]}", "error")
    audit("UiT-verbinding getest")
    return redirect(url_for("admin.verbindingen"))


@bp.route("/test-smtp", methods=["POST"])
@admin_required
@limiter.limit("5/hour")
def test_smtp():
    """Stuur een testmail naar het adres van de ingelogde admin."""
    from ..services.magic import send_mail
    admin = db.session.get(Admin, session["admin_id"])
    try:
        send_mail(admin.email, "Ravot — testmail",
                  "<p>Dit is een testmail vanuit het Ravot-beheer. "
                  "Als je dit ziet, werkt SMTP. 🎉</p>",
                  text="Testmail vanuit Ravot-beheer. SMTP werkt.")
        flash(f"Testmail verstuurd naar {admin.email}. Kijk in je mailbox (of de console-log bij dev).", "ok")
    except Exception as exc:
        flash(f"Mail versturen mislukte: {str(exc)[:120]}", "error")
    audit("SMTP-testmail verstuurd")
    return redirect(url_for("admin.verbindingen"))


@bp.route("/logout")
def logout():
    if session.get("admin_id"):
        audit("logout")
    session.pop("admin_id", None)
    session.pop("admin_2fa_ok", None)
    return redirect(url_for("public.vandaag"))
