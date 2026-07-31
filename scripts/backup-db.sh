#!/bin/bash
# Dagelijkse database-backup voor Ravot.
# Maakt een gecomprimeerde pg_dump en bewaart de laatste 14 dagen.
#
# INSTALLEREN op de VPS (eenmalig):
#   chmod +x /srv/ravot/scripts/backup-db.sh
#   crontab -e   → voeg toe:
#   15 3 * * * /srv/ravot/scripts/backup-db.sh >> /var/log/ravot-backup.log 2>&1
#
# TERUGZETTEN (rampscenario):
#   gunzip -c /srv/ravot-backups/ravot-YYYY-MM-DD.sql.gz | \
#     docker compose -f /srv/ravot/docker-compose.yml exec -T db psql -U ravot ravot

set -euo pipefail
BACKUP_DIR="/srv/ravot-backups"
DATUM=$(date +%F)
mkdir -p "$BACKUP_DIR"

cd /srv/ravot
docker compose exec -T db pg_dump -U ravot ravot | gzip > "$BACKUP_DIR/ravot-$DATUM.sql.gz"

# Bewaar 14 dagen, ruim ouder op
find "$BACKUP_DIR" -name "ravot-*.sql.gz" -mtime +14 -delete

GROOTTE_MB=$(du -m "$BACKUP_DIR/ravot-$DATUM.sql.gz" | cut -f1)

# Registreer de geslaagde backup in de database, zodat de status-check op het
# dashboard 'm ziet (de web-container ziet de host-backupmap niet).
docker compose exec -T web flask backup-gelukt "$GROOTTE_MB" || true

echo "$(date -Is) backup ok: $BACKUP_DIR/ravot-$DATUM.sql.gz (${GROOTTE_MB}M)"
