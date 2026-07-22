"""Patch 92: het adminmenu mag niet toeklappen op subpagina's.

De zijbalk opent een <details>-groep enkel als 'active' in de sectielijst zit.
Routes die active=None doorgaven (verbindingen, gezinnen, pagina's, mailteksten
en hun detailpagina's) lieten de groep dus dichtklappen na de klik.
"""
import pytest
from argon2 import PasswordHasher

from app.extensions import db
from app.models import Admin


@pytest.fixture()
def admin_client(client, app):
    with app.app_context():
        db.session.add(Admin(email="a@ravot.be", pw_hash=PasswordHasher().hash("x"),
                             totp_secret="JBSWY3DPEHPK3PXP", totp_confirmed=True))
        db.session.commit()
        aid = Admin.query.first().id
    with client.session_transaction() as s:
        s["admin_id"] = aid
        s["admin_2fa_ok"] = True
    return client


# (pad, linktekst die als actief gemarkeerd moet zijn)
PAGINAS = [
    ("/beheer/verbindingen", b'class="on">Verbindingen'),
    ("/beheer/families", b'class="on">Gezinnen'),
    ("/beheer/paginas", b'class="on">Pagina'),
    ("/beheer/mails", b'class="on">Mailteksten'),
]


@pytest.mark.parametrize("pad,marker", PAGINAS)
def test_zijbalkgroep_blijft_open(admin_client, pad, marker):
    r = admin_client.get(pad)
    assert r.status_code == 200
    assert marker in r.data, f"actieve link ontbreekt op {pad} — groep klapt toe"


def test_alle_zijbalkpaginas_hebben_active(app):
    """Vangnet: elke admin-view met zijbalk moet een active-waarde meegeven
    die in de sectie-mapping van admin_base.html voorkomt."""
    import re
    src = open("app/routes/admin.py").read()
    # Enkel login/otp/2fa (zonder zijbalk) mogen nog active=None hebben.
    assert src.count("active=None") <= 3, \
        "nieuwe admin-view zonder active-waarde — menu klapt daar toe"
