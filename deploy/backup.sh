#!/usr/bin/env bash
# Nightly PostgreSQL backup. Config in /etc/sesh/backup.env:
#   BACKUP_PG_URL=postgresql://sesh_user:PASSWORD@localhost:5432/sesh   (libpq URL, NOT the +psycopg form)
#   BACKUP_DIR=/var/backups/sesh      KEEP_DAYS=14      RCLONE_REMOTE=   (optional, e.g. r2backup:sesh-db-backups)
set -euo pipefail
: "${BACKUP_PG_URL:?set BACKUP_PG_URL in /etc/sesh/backup.env}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/sesh}"
KEEP_DAYS="${KEEP_DAYS:-14}"
umask 077
mkdir -p "$BACKUP_DIR"
file="$BACKUP_DIR/sesh-$(date -u +%Y%m%dT%H%M%SZ).dump"
pg_dump --format=custom --no-owner "$BACKUP_PG_URL" > "$file"
pg_restore --list "$file" > /dev/null          # proves the dump is readable
sha256sum "$file" > "$file.sha256"
if [ -n "${RCLONE_REMOTE:-}" ]; then
  rclone copy "$file" "$RCLONE_REMOTE" && rclone copy "$file.sha256" "$RCLONE_REMOTE"
fi
find "$BACKUP_DIR" -name 'sesh-*.dump*' -mtime +"$KEEP_DAYS" -delete
echo "backup ok: $file ($(du -h "$file" | cut -f1))"
