#!/bin/bash
# Geplande Ravot-mails. Gebruik: mail.sh weekend | mail.sh maandag
# De aan/uit-schakelaars in /beheer/instellingen worden gerespecteerd:
# staat een mail uit, dan doet het commando niets.
set -euo pipefail
cd /srv/ravot

case "${1:-}" in
  weekend) docker compose exec -T web flask send-weekendmail ;;
  maandag) docker compose exec -T web flask send-maandagmail ;;
  *) echo "Gebruik: $0 weekend|maandag" >&2; exit 1 ;;
esac
