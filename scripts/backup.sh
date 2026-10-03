#!/usr/bin/env bash
# DB를 gzip으로 덤프하고 오래된 백업을 삭제. cron에서 호출.
#   BACKUP_DIR   저장 위치   (기본 ~/backups/travel-planner)
#   KEEP_DAYS    보관 일수   (기본 14)
#   RCLONE_REMOTE  지정하면 rclone으로 외부 저장소에 복사 (예: mycloud:travel-backups)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/travel-planner}"
KEEP_DAYS="${KEEP_DAYS:-14}"
mkdir -p "$BACKUP_DIR"

out="$BACKUP_DIR/travel-$(date +%F-%H%M).sql.gz"
trap 'rm -f "$out.tmp"' ERR

docker compose exec -T db pg_dump -U "$PG_USER" "$PG_DB" | gzip > "$out.tmp"
mv "$out.tmp" "$out"
echo "saved $out"

if [ -n "${RCLONE_REMOTE:-}" ]; then
  rclone copy "$out" "$RCLONE_REMOTE"
  echo "uploaded to $RCLONE_REMOTE"
fi

find "$BACKUP_DIR" -name 'travel-*.sql.gz' -mtime +"$KEEP_DAYS" -delete