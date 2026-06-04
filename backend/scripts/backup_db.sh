#!/bin/bash
# Daily PostgreSQL backup script for staging POS
# Keeps 30 days of backups, compresses with gzip
#
# Usage: Add to crontab:
#   0 3 * * * /var/www/staging-pos/woo-ghl-contact-db/backend/scripts/backup_db.sh

set -euo pipefail

BACKUP_DIR="/var/www/staging-pos/backups/db"
DB_NAME="newpos_staging"
DB_USER="newpos_staginguser"
RETENTION_DAYS=30
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/${DB_NAME}_${TIMESTAMP}.sql.gz"

# Ensure backup directory exists
mkdir -p "$BACKUP_DIR"

# Run pg_dump and compress
echo "[$(date)] Starting backup of ${DB_NAME}..."
if pg_dump -U "$DB_USER" -h localhost "$DB_NAME" | gzip > "$BACKUP_FILE"; then
    SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    echo "[$(date)] Backup complete: ${BACKUP_FILE} (${SIZE})"
else
    echo "[$(date)] ERROR: Backup failed!" >&2
    exit 1
fi

# Remove backups older than retention period
DELETED=$(find "$BACKUP_DIR" -name "*.sql.gz" -mtime +${RETENTION_DAYS} -delete -print | wc -l)
if [ "$DELETED" -gt 0 ]; then
    echo "[$(date)] Cleaned up ${DELETED} backup(s) older than ${RETENTION_DAYS} days"
fi

echo "[$(date)] Backup job finished"
