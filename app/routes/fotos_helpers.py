"""Toegangscontrole voor het serveren van gebruikersfoto's."""
from flask import session


def _mag_zien(photo):
    if photo.status == "approved":
        return True
    # niet-goedgekeurd: enkel admin of de uploader zelf
    if session.get("admin_id") and session.get("admin_2fa_ok"):
        return True
    return bool(photo.family_id and session.get("family_id") == photo.family_id)
