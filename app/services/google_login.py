"""Inloggen met Google (patch 225).

Bewust zonder extra bibliotheek: OAuth 2.0 is hier een handvol HTTPS-oproepen,
en elke afhankelijkheid minder is er één die niet kan verlopen of breken.

Veiligheid, in volgorde van belang:
- `state` in de sessie beschermt tegen CSRF op de terugkeer-URL;
- de e-mail halen we op bij Google's userinfo-endpoint over TLS in plaats van
  een JWT zelf te ontleden — zo hoeven we geen handtekeningen te valideren;
- alleen een door Google **geverifieerd** adres wordt aanvaard. Zonder die
  controle zou iemand met een niet-geverifieerd adres het account van een
  ander kunnen overnemen, want e-mail is bij ons de identiteit.

De e-mailcode blijft gewoon bestaan: dit is een snellere deur, geen vervanging.
"""
import secrets
from urllib.parse import urlencode

import requests

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
INFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def actief():
    """Aan zodra client-id én -secret in de omgeving staan."""
    from flask import current_app
    return bool(current_app.config.get("GOOGLE_CLIENT_ID")
                and current_app.config.get("GOOGLE_CLIENT_SECRET"))


def start_url(redirect_uri, state):
    from flask import current_app
    return AUTH_URL + "?" + urlencode({
        "client_id": current_app.config["GOOGLE_CLIENT_ID"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email",
        "state": state,
        "prompt": "select_account",
    })


def nieuwe_state():
    return secrets.token_urlsafe(24)


def email_uit_code(code, redirect_uri):
    """Wissel de code in voor een token en haal het e-mailadres op.
    Retourneert het adres, of None bij elke twijfel."""
    from flask import current_app
    try:
        antw = requests.post(TOKEN_URL, timeout=15, data={
            "code": code,
            "client_id": current_app.config["GOOGLE_CLIENT_ID"],
            "client_secret": current_app.config["GOOGLE_CLIENT_SECRET"],
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        })
        antw.raise_for_status()
        token = (antw.json() or {}).get("access_token")
        if not token:
            return None
        info = requests.get(INFO_URL, timeout=15,
                            headers={"Authorization": f"Bearer {token}"})
        info.raise_for_status()
        gegevens = info.json() or {}
    except Exception:
        return None
    email = (gegevens.get("email") or "").strip().lower()
    # Niet-geverifieerd adres = geen bewijs van eigenaarschap.
    if not email or gegevens.get("email_verified") not in (True, "true"):
        return None
    return email
