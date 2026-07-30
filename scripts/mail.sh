#!/bin/bash
# Geplande Ravot-mails. Gebruik: mail.sh weekend | maandag | preview
# Cron-voorbeeld:
#   0 17 * * 3  /srv/ravot/scripts/mail.sh preview  >> /var/log/ravot-mail.log 2>&1
#   0 17 * * 4  /srv/ravot/scripts/mail.sh weekend  >> /var/log/ravot-mail.log 2>&1
#   0 9  * * 1  /srv/ravot/scripts/mail.sh maandag  >> /var/log/ravot-mail.log 2>&1
# (woensdag-preview = daags vóór de donderdagmail: tijd om bij te sturen)
# De aan/uit-schakelaars in /beheer/instellingen worden gerespecteerd:
# staat een mail uit, dan doet het commando niets.
set -euo pipefail
cd /srv/ravot

case "${1:-}" in
  weekend) docker compose exec -T web flask send-weekendmail ;;
  preview) docker compose exec -T web flask redactie-preview ;;
  maandag) docker compose exec -T web flask send-maandagmail ;;
  *) echo "Gebruik: $0 weekend|maandag" >&2; exit 1 ;;
esac
