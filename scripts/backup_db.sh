#!/usr/bin/env bash
# Бэкап app.db с ротацией (хранит последние 30 копий).
# Использование: backup_db.sh <source_db_path> <backup_dir> [keep=30]
# Пример (cron): /opt/mediasrv/scripts/backup_db.sh /opt/mediasrv/app.db /srv/Общее/backups

set -euo pipefail

SOURCE="${1:?usage: backup_db.sh <source.db> <backup_dir>}"
BACKUP_DIR="${2:?usage: backup_db.sh <source.db> <backup_dir>}"
KEEP="${3:-30}"

if [ ! -f "$SOURCE" ]; then
  echo "ERROR: source DB not found: $SOURCE" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"

TS=$(date -u +%Y-%m-%d)
DEST="$BACKUP_DIR/app-$TS.db"

# sqlite3 .backup безопасно в read'е concurrent с running'ом приложения,
# в отличие от cp которое может прочитать write-in-progress файл.
if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$SOURCE" ".backup '$DEST'"
else
  echo "WARNING: sqlite3 CLI not found, falling back to cp (less safe)" >&2
  cp "$SOURCE" "$DEST"
fi

echo "Backup created: $DEST"

# Ротация: оставить $KEEP самых свежих
cd "$BACKUP_DIR"
ls -1t app-*.db 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -v
