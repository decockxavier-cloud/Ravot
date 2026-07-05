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


def issue_token(email, purpose="login"):
    token = secrets.token_urlsafe(32)
    minutes = current_app.config["MAGIC_LINK_MINUTES"]
    db.session.add(MagicToken(
        email=email.lower().strip(),
        token_hash=_hash(token),
        purpose=purpose,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=minutes),
    ))
    db.session.commit()
    return token


def peek_token(token, purpose="login"):
    """Retourneert e-mailadres als de token geldig is, ZONDER hem te verbranden.
    Zo kan een automatische e-mailscanner (die de link vooraf bezoekt) de token
    niet opbranden — verbranden gebeurt pas bij een bewuste klik (POST)."""
    row = MagicToken.query.filter_by(token_hash=_hash(token), purpose=purpose).first()
    if row is None or row.used_at is not None:
        return None
    if row.expires_at < datetime.now(timezone.utc).replace(tzinfo=None):
        return None
    return row.email


def verify_token(token, purpose="login"):
    """Retourneert e-mailadres of None. Token wordt bij succes verbrand."""
    row = MagicToken.query.filter_by(token_hash=_hash(token), purpose=purpose).first()
    if row is None or row.used_at is not None:
        return None
    if row.expires_at < datetime.now(timezone.utc).replace(tzinfo=None):
        return None
    row.used_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.commit()
    return row.email


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
