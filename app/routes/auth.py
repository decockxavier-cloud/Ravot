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
    # Veilige "kom terug"-bestemming (bv. vanaf de feestjespagina): enkel
    # interne paden, nooit externe URL's.
    volgende = request.args.get("next") or ""
    if volgende.startswith("/") and not volgende.startswith("//"):
        session["na_login"] = volgende
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not EMAIL_RE.match(email):
            flash("Dat lijkt geen geldig e-mailadres.", "error")
            return render_template("auth/login.html", title="Aanmelden", family=None, active=None)
        from ..models import get_int
        max_codes = get_int("codes_per_uur", 0) or current_app.config["MAGIC_REQUESTS_PER_HOUR"]
        if magic.recent_requests(email) >= max_codes:
            flash("Er zijn al enkele codes verstuurd. Kijk in je mailbox (ook spam).", "error")
            return render_template("auth/login.html", title="Aanmelden", family=None, active=None)
        code = magic.issue_code(email)
        magic.send_mail(
            email, f"Jouw Ravot-inlogcode: {code}",
            render_template("mail/inlogcode.html", code=code),
            text=f"Jouw Ravot-inlogcode is {code}. Ze is 15 minuten geldig. "
                 f"Typ ze in op de website. Heb je dit niet aangevraagd? Negeer deze mail.",
        )
        # Onthoud voor welk adres we een code wachten (voorvullen + veiligheid).
        session["code_email"] = email
        return render_template("auth/code_invoeren.html", email=email,
                               title="Voer je code in", family=None, active=None)
    return render_template("auth/login.html", title="Aanmelden", family=None, active=None)


@bp.route("/code", methods=["POST"])
@limiter.limit("20/hour")
def code_verify():
    """Controleer de 6-cijferige inlogcode."""
    email = (request.form.get("email") or session.get("code_email") or "").strip().lower()
    code = re.sub(r"\D", "", request.form.get("code", ""))  # enkel cijfers
    if not email:
        flash("Vraag eerst een inlogcode aan.", "error")
        return redirect(url_for("auth.login"))
    resultaat = magic.verify_code(email, code)
    if resultaat is None:
        flash("Die code klopt niet of is verlopen. Probeer opnieuw of vraag een nieuwe aan.", "error")
        return render_template("auth/code_invoeren.html", email=email,
                               title="Voer je code in", family=None, active=None)
    session.pop("code_email", None)
    family = Family.query.filter_by(email=email).first()
    session.permanent = True
    if family is None:
        session["pending_email"] = email
        return redirect(url_for("account.onboarding"))
    session["family_id"] = family.id
    doel = session.pop("na_login", None)
    if doel and doel.startswith("/") and not doel.startswith("//"):
        return redirect(doel)
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
