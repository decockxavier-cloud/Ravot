"""Magic links (wachtwoordloos inloggen) + mailverzending.

Beveiliging (strategienota §8.1):
- token: secrets.token_urlsafe, eenmalig, 15 min geldig
- enkel de sha256-hash wordt opgeslagen
- rate limit op aanvraag zit in de route (3/uur per adres)
"""
import hashlib
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr

from flask import current_app

from ..extensions import db
from ..models import MagicToken


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _hash_code(email, code):
    """Hash e-mail + code samen: zo is de opgeslagen hash uniek per gebruiker,
    ook al krijgen twee mensen toevallig dezelfde 6 cijfers."""
    return hashlib.sha256(f"{email.lower().strip()}:{code}".encode()).hexdigest()


MAX_CODE_ATTEMPTS = 5  # brute-force-slot: na 5 foute pogingen is de code dood


def issue_code(email, purpose="login"):
    """Genereer een 6-cijferige inlogcode. Retourneert de code (voor de mail).
    Enkel de hash (van e-mail+code) wordt opgeslagen."""
    email = email.lower().strip()
    code = f"{secrets.randbelow(1_000_000):06d}"  # 000000–999999, altijd 6 cijfers
    minutes = current_app.config["MAGIC_LINK_MINUTES"]
    # Bestaande openstaande codes voor dit adres ongeldig maken (één actieve code).
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for oud in MagicToken.query.filter_by(email=email, purpose=purpose, used_at=None):
        oud.used_at = now
    db.session.add(MagicToken(
        email=email,
        token_hash=_hash_code(email, code),
        purpose=purpose,
        expires_at=now + timedelta(minutes=minutes),
    ))
    db.session.commit()
    return code


def verify_code(email, code, purpose="login"):
    """Controleer e-mail + code. Retourneert e-mail bij succes (code verbrand),
    anders None. Telt foute pogingen; na MAX_CODE_ATTEMPTS is de code dood."""
    email = email.lower().strip()
    code = (code or "").strip()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Zoek de nieuwste openstaande code voor dit adres.
    row = MagicToken.query.filter_by(email=email, purpose=purpose, used_at=None) \
        .order_by(MagicToken.id.desc()).first()
    if row is None:
        return None
    if row.expires_at < now:
        return None
    if row.attempts >= MAX_CODE_ATTEMPTS:
        row.used_at = now  # te vaak geprobeerd → definitief dood
        db.session.commit()
        return None
    if row.token_hash != _hash_code(email, code):
        row.attempts += 1
        db.session.commit()
        return None
    row.used_at = now  # correct → verbranden
    db.session.commit()
    return email


def _hash_legacy(token):
    return hashlib.sha256(token.encode()).hexdigest()


def recent_requests(email, hours=1):
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)
    return MagicToken.query.filter(
        MagicToken.email == email.lower().strip(),
        MagicToken.created_at >= since,
    ).count()


# ------------------------------------------------------------------- mailer --

def send_mail(to, subject, html, text=None, headers=None):
    """SMTP als geconfigureerd; anders console (dev). Transactionele mails
    (magic links) gaan hier ook door — die vallen NIET onder de nieuwsbrief."""
    cfg = current_app.config
    if not cfg["SMTP_HOST"]:
        current_app.logger.info("MAIL (console) → %s | %s\n%s", to, subject, text or html)
        print(f"\n=== MAIL naar {to}: {subject} ===\n{text or html}\n===\n")
        return True
    msg = EmailMessage()
    msg["From"] = cfg["MAIL_FROM"]
    msg["To"] = to
    msg["Subject"] = subject
    for k, v in (headers or {}).items():
        msg[k] = v
    msg.set_content(text or "Bekijk deze mail in een HTML-client.")
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"]) as s:
        s.starttls()
        if cfg["SMTP_USER"]:
            s.login(cfg["SMTP_USER"], cfg["SMTP_PASS"])
        s.send_message(msg)
    return True
