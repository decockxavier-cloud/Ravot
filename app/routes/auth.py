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


def _google_aan():
    from ..services import google_login
    return google_login.actief()


def _feestjes_aan():
    """Feestjes alleen beloven als ze effectief vrijgegeven zijn (patch 234)."""
    from ..models import get_bool
    return get_bool("feestjes_aan")


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("20/hour", methods=["POST"])
def login():
    from ..trechter import tel_stap
    tel_stap("login_gezien")
    # Veilige "kom terug"-bestemming (bv. vanaf de feestjespagina): enkel
    # interne paden, nooit externe URL's.
    volgende = request.args.get("next") or ""
    if volgende.startswith("/") and not volgende.startswith("//"):
        session["na_login"] = volgende
    # NPM's "Block Common Exploits" weigert paden in de querystring (403),
    # dus knoppen sturen een slash-vrij token mee: "feestje" of een fiche-slug.
    terug = (request.args.get("terug") or "").strip()[:120]
    if terug and re.fullmatch(r"[a-z0-9][a-z0-9\-]*", terug):
        if terug == "feestje":
            session["na_login"] = url_for("account.feestje_nieuw")
        else:
            session["na_login"] = f"/e/{terug}"
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not EMAIL_RE.match(email):
            flash("Dat lijkt geen geldig e-mailadres.", "error")
            return render_template("auth/login.html", google_aan=_google_aan(), feestjes_aan=_feestjes_aan(), title="Aanmelden", family=None, active=None)
        from ..models import get_int
        max_codes = get_int("codes_per_uur", 0) or current_app.config["MAGIC_REQUESTS_PER_HOUR"]
        if magic.recent_requests(email) >= max_codes:
            flash("Er zijn al enkele codes verstuurd. Kijk in je mailbox (ook spam).", "error")
            return render_template("auth/login.html", google_aan=_google_aan(), feestjes_aan=_feestjes_aan(), title="Aanmelden", family=None, active=None)
        # Onbekend adres? Dan sturen we NIET zomaar een code (typfouten en
        # vreemde adressen krijgen zo nooit ongevraagde mail). We tonen eerst
        # de vraag of ze een nieuw profiel willen; pas na die bewuste klik
        # (veld "nieuw") vertrekt de code en volgt de onboarding.
        from ..models import find_family_by_email
        bestaat = find_family_by_email(email) is not None
        if not bestaat and request.form.get("nieuw") != "1":
            return render_template("auth/nieuw_profiel.html", email=email,
                                   title="Nieuw bij Ravot?", family=None,
                                   active=None)
        tel_stap("code_gevraagd")
        tel_stap("poging")
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
    return render_template("auth/login.html", google_aan=_google_aan(), feestjes_aan=_feestjes_aan(), title="Aanmelden", family=None, active=None)


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
    from ..models import find_family_by_email
    family = find_family_by_email(email)
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


@bp.route("/gezinslid/<token>")
def gezinslid_bevestig(token):
    """Bevestiging van een extra gezinslid-adres via gesigneerde maillink.
    Publiek bereikbaar: het nieuwe lid is meestal nog niet ingelogd."""
    from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
    from ..models import FamilyMember
    from ..extensions import db
    from datetime import datetime
    s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="gezinslid")
    try:
        lid_id = s.loads(token, max_age=7 * 24 * 3600)
    except SignatureExpired:
        flash("Deze uitnodiging is verlopen. Vraag een nieuwe aan via het gezin.", "error")
        return redirect(url_for("auth.login"))
    except BadSignature:
        flash("Deze link klopt niet.", "error")
        return redirect(url_for("auth.login"))
    lid = db.session.get(FamilyMember, lid_id)
    if lid is None:
        flash("Deze uitnodiging bestaat niet meer.", "error")
        return redirect(url_for("auth.login"))
    if not lid.bevestigd:
        lid.bevestigd = True
        lid.bevestigd_at = datetime.utcnow()
        db.session.commit()
    flash("Adres bevestigd! Meld je aan met je eigen e-mailadres — je krijgt "
          "dan een inlogcode en komt in jullie gezinsaccount terecht.", "ok")
    return redirect(url_for("auth.login"))


# --- Inloggen met Google (patch 225) ----------------------------------------

@bp.route("/login/google")
@limiter.limit("20/hour")
def google_start():
    """Stuur de bezoeker naar Google. Eén tik in plaats van code-uit-mail."""
    from ..services import google_login
    if not google_login.actief():
        flash("Inloggen met Google is hier niet ingesteld.", "error")
        return redirect(url_for("auth.login"))
    from ..trechter import tel_stap
    tel_stap("google_gestart")
    tel_stap("poging")
    state = google_login.nieuwe_state()
    session["google_state"] = state
    terug = url_for("auth.google_terug", _external=True)
    return redirect(google_login.start_url(terug, state))


@bp.route("/login/google/terug")
@limiter.limit("30/hour")
def google_terug():
    """Terugkeer van Google: state controleren, e-mail ophalen, inloggen."""
    from ..models import find_family_by_email
    from ..services import google_login
    verwacht = session.pop("google_state", None)
    if not verwacht or request.args.get("state") != verwacht:
        # Geen of verkeerde state: mogelijk CSRF, dus niets doen.
        flash("Aanmelden is onderbroken. Probeer het opnieuw.", "error")
        return redirect(url_for("auth.login"))
    code = request.args.get("code")
    if not code:
        return redirect(url_for("auth.login"))
    terug = url_for("auth.google_terug", _external=True)
    email = google_login.email_uit_code(code, terug)
    if not email:
        flash("We kregen geen geverifieerd e-mailadres van Google. "
              "Meld je aan met je e-mailadres.", "error")
        return redirect(url_for("auth.login"))
    session.permanent = True
    family = find_family_by_email(email)
    if family is None:
        session["pending_email"] = email
        return redirect(url_for("account.onboarding"))
    session["family_id"] = family.id
    doel = session.pop("na_login", None)
    if doel and doel.startswith("/") and not doel.startswith("//"):
        return redirect(doel)
    return redirect(url_for("public.vandaag"))


@bp.route("/weekendmail-aan/<token>")
@limiter.limit("30/hour")
def weekendmail_aan(token):
    """Weekendmail aanzetten met één klik uit de welkomstmail (patch 248).

    Eén klik is een geldige, ondubbelzinnige toestemming: de bezoeker doet een
    actieve handeling en het token bewijst dat de link naar zijn eigen adres
    ging. Uitschrijven blijft even makkelijk.
    """
    from ..models import Family
    from ..services.weekendmail import parse_aanzet_token
    fid = parse_aanzet_token(token)
    if not fid:
        flash("Die link is niet meer geldig. Je kunt de weekendmail aanzetten "
              "in je instellingen.", "error")
        return redirect(url_for("auth.login"))
    fam = db.session.get(Family, fid)
    if fam is None:
        return redirect(url_for("auth.login"))
    fam.newsletter_opt_in = True
    db.session.commit()
    session["family_id"] = fam.id          # klikken = ingelogd, scheelt gedoe
    session.permanent = True
    flash("Top! Vanaf donderdag krijg je uitstappen in jullie buurt in je "
          "mailbox. Uitschrijven kan altijd met één klik. 🦊", "ok")
    return redirect(url_for("public.vandaag"))
