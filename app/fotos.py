"""Veilige verwerking van gebruikersfoto's.

Elke upload wordt HERINGCODEERD via Pillow: dat valideert dat het écht een
afbeelding is (geen verkapte scripts / polyglot-bestanden), verwijdert alle
EXIF-metadata (privacy — o.a. GPS van waar de foto genomen is) en verkleint
te grote beelden. We vertrouwen nooit de originele bytes of bestandsnaam.

Foto's worden opgeslagen in een privémap (Docker-volume), niet onder /static,
en enkel via een route geserveerd — pending foto's zijn dus nooit publiek.
"""
import io
import os
import secrets

from flask import current_app
from PIL import Image, ImageOps

TOEGESTAAN = {"image/jpeg", "image/png", "image/webp"}
MAGIC = {
    b"\xff\xd8\xff": "jpeg",           # JPEG
    b"\x89PNG\r\n\x1a\n": "png",       # PNG
    b"RIFF": "webp",                   # WEBP (RIFF....WEBP)
}


def _map():
    d = current_app.config["UPLOAD_DIR"]
    os.makedirs(d, exist_ok=True)
    return d


def _lijkt_afbeelding(head):
    return any(head.startswith(m) for m in MAGIC)


def verwerk_upload(bestand):
    """Neem een geüpload bestand, valideer + heringcodeer het en bewaar als JPEG.
    Geeft de veilige bestandsnaam terug, of None als het geen geldige foto is."""
    if not bestand or not bestand.filename:
        return None
    ruw = bestand.read()
    if not ruw or not _lijkt_afbeelding(ruw[:16]):
        return None
    try:
        img = Image.open(io.BytesIO(ruw))
        img.verify()                      # 1e check: is het een geldige afbeelding?
        img = Image.open(io.BytesIO(ruw))  # verify() sluit de file -> heropenen
        img = ImageOps.exif_transpose(img)  # oriëntatie toepassen, dan EXIF droppen
        img = img.convert("RGB")           # normaliseren (strips alfa/rare modes)
    except Exception:
        return None
    maxz = current_app.config.get("FOTO_MAX_ZIJDE", 1600)
    img.thumbnail((maxz, maxz))            # verkleinen indien nodig (bewaart ratio)

    naam = f"{secrets.token_hex(16)}.jpg"  # zelfgekozen naam, nooit die van de user
    pad = os.path.join(_map(), naam)
    # Opslaan ZONDER exif -> alle metadata weg.
    img.save(pad, format="JPEG", quality=current_app.config.get("FOTO_KWALITEIT", 82),
             optimize=True)
    return naam


def pad_van(filename):
    return os.path.join(current_app.config["UPLOAD_DIR"], filename)


def verwijder(filename):
    try:
        os.remove(pad_van(filename))
    except OSError:
        pass
