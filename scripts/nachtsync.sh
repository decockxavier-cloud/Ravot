#!/bin/bash
# Nachtelijke sync voor Ravot: alle INGESCHAKELDE bronnen (UiT, Ticketmaster,
# Toerisme Vlaanderen, OSM, Wikidata, feeds) + kwaliteitsherberekening + dedup.
# Bronnen die in /beheer/instellingen uitstaan, worden automatisch overgeslagen.
#
# INSTALLEREN op de VPS (eenmalig):
#   chmod +x /srv/ravot/scripts/nachtsync.sh
#   crontab -e   → voeg toe:
#   30 2 * * *  /srv/ravot/scripts/nachtsync.sh        >> /var/log/ravot-sync.log 2>&1
#   0 17 * * 4  /srv/ravot/scripts/mail.sh weekend     >> /var/log/ravot-mail.log 2>&1
#   0 10 * * 1  /srv/ravot/scripts/mail.sh maandag     >> /var/log/ravot-mail.log 2>&1
#
# (De backup draait al apart om 03:15 — de sync om 02:30 zit daar bewust vóór,
#  zodat de backup meteen de verse data bevat.)

set -euo pipefail
cd /srv/ravot

echo "=== Ravot nachtsync $(date '+%F %T') ==="
docker compose exec -T web flask sync-all
docker compose exec -T web flask herbereken-kwaliteit
docker compose exec -T web flask dedup
echo "=== Klaar $(date '+%F %T') ==="
