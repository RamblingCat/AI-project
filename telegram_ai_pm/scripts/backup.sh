#!/bin/sh
set -eu
umask 077
BACKUP_DIR=${BACKUP_DIR:-/var/lib/postgresql/data/backups}
KEEP_DAYS=${KEEP_DAYS:-7}
case "$KEEP_DAYS" in ''|*[!0-9]*) echo 'KEEP_DAYS must be a positive integer' >&2; exit 1;; esac
[ "$KEEP_DAYS" -ge 1 ]
mkdir -p "$BACKUP_DIR"
temporary=$(mktemp "$BACKUP_DIR/pm-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX.dump.partial")
backup=$(basename "$temporary" .partial)
trap 'rm -f "$temporary"' EXIT HUP INT TERM
pg_dump --format=custom --no-owner --no-acl --file="$temporary"
pg_restore --list "$temporary" >/dev/null
mv "$temporary" "$BACKUP_DIR/$backup"
(cd "$BACKUP_DIR" && sha256sum "$backup" > "$backup.sha256")
find "$BACKUP_DIR" -type f -name 'pm-*.dump*' -mtime +"$KEEP_DAYS" -delete
echo "Backup created: $backup"
