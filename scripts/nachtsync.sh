#!/usr/bin/env bash
# Ravot-syncs met een ritme per bron — elke bron heeft zijn eigen tempo:
#
#   UiT       : elke nacht      (nieuwe events komen dagelijks binnen)
#   OSM       : wekelijks       (speeltuinen/parken veranderen traag; zware sync)
#   Overture  : maandelijks     (releases ~per kwartaal; hier hangt de AI-triage aan)
#
# Zet in crontab (crontab -e):
#   0 3 * * *   /srv/ravot/scripts/nachtsync.sh uit       >> /var/log/ravot-sync.log 2>&1
#   30 3 * * 0  /srv/ravot/scripts/nachtsync.sh osm       >> /var/log/ravot-sync.log 2>&1
#   0 4 1 * *   /srv/ravot/scripts/nachtsync.sh overture  >> /var/log/ravot-sync.log 2>&1
#
# (Zondag = 0. De maandelijkse Overture-run laadt nieuwe voorraad, triëert de
#  volledige achterstand — AI-'ja' gaat automatisch live — en verrijkt contact.)
set -euo pipefail
cd /srv/ravot
WAT="${1:-uit}"
echo "=== Ravot sync '$WAT' $(date '+%F %T') ==="
case "$WAT" in
  uit)      docker compose exec -T web flask sync-uit ;;
  osm)      docker compose exec -T web flask sync-osm ;;
  overture) docker compose exec -T web flask overture-maandelijks ;;
  *) echo "onbekend: $WAT (kies uit|osm|overture)"; exit 1 ;;
esac
echo "=== klaar $(date '+%F %T') ==="
