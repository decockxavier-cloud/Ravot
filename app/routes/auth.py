"""Auth: wachtwoordloos (magic links) + one-click nieuwsbrief-uitschrijving."""
import re

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, session, url_for)

from ..extensions import db, limiter
from ..models import Family
from ..services import magic
from ..services.weekendmail import parse_unsubscribe_token

bp = Blueprint("auth", __name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("20/hour", methods=["POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not EMAIL_RE.match(email):
            flash("Dat lijkt geen geldig e-mailadres.", "error")
            return render_template("auth/login.html", title="Aanmelden", family=None, active=None)
        if magic.recent_requests(email) >= current_app.config["MAGIC_REQUESTS_PER_HOUR"]:
            flash("Er zijn al enkele inloglinks verstuurd. Kijk in je mailbox (ook spam).", "error")
            return render_template("auth/login.html", title="Aanmelden", family=None, active=None)
        token = magic.issue_token(email)
        link = current_app.config["SITE_URL"] + url_for("auth.verify", token=token)
        magic.send_mail(
            email, "Jouw Ravot-inloglink",
            render_template("mail/magic_link.html", link=link),
            text=f"Meld je aan bij Ravot met deze link (15 min geldig): {link}",
        )
        return render_template("auth/check_mail.html", email=email,
                               title="Kijk in je mailbox", family=None, active=None)
    return render_template("auth/login.html", title="Aanmelden", family=None, active=None)


@bp.route("/login/<token>", methods=["GET", "POST"])
def verify(token):
    if request.method == "GET":
        # Toon een bevestigingsknop zonder de token te verbranden.
        # Automatische e-mailscanners doen alleen deze GET en klikken de knop niet,
        # dus de link blijft geldig tot de gebruiker zelf bevestigt.
        email = magic.peek_token(token)
        if email is None:
            flash("Deze link is verlopen of al gebruikt. Vraag een nieuwe aan.", "error")
            return redirect(url_for("auth.login"))
        return render_template("auth/bevestig_login.html", token=token,
                               title="Aanmelden bevestigen", family=None, active=None)
    # POST: nu pas verbranden en inloggen
    email = magic.verify_token(token)
    if email is None:
        flash("Deze link is verlopen of al gebruikt. Vraag een nieuwe aan.", "error")
        return redirect(url_for("auth.login"))
    family = Family.query.filter_by(email=email).first()
    session.permanent = True
    if family is None:
        session["pending_email"] = email
        return redirect(url_for("account.onboarding"))
    session["family_id"] = family.id
    return redirect(url_for("public.vandaag"))


@bp.route("/logout")
def logout():
    session.pop("family_id", None)
    return redirect(url_for("public.vandaag"))


@bp.route("/uitschrijven/<token>", methods=["GET", "POST"])
def unsubscribe(token):
    """One-click, zonder login, zonder vragen. Account blijft bestaan.
    POST wordt aanvaard voor List-Unsubscribe-Post (RFC 8058)."""
    data = parse_unsubscribe_token(token)
    if not data:
        return render_template("auth/unsub.html", ok=False, title="Uitschrijven",
                               family=None, active=None)
    family = db.session.get(Family, data.get("f"))
    if family:
        if data.get("k") == "monday":
            family.monday_opt_in = False
        else:
            family.newsletter_opt_in = False
        db.session.commit()
    return render_template("auth/unsub.html", ok=True, title="Uitgeschreven",
                           family=None, active=None)
