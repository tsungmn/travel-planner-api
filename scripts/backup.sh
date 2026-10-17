#!/usr/bin/env bash
# PostgreSQL 백업: pg_dump -> gzip, 오래된 파일 자동 삭제.
# 사용: scripts/backup.sh            (프로젝트 루트나 어디서든 실행 가능)
# cron 예시 (매일 새벽 3시):
#   0 3 * * * /home/<유저>/travel-planner-api/scripts/backup.sh >> /home/<유저>/travel-planner-api/backups/backup.log 2>&1
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

KEEP_DAYS="${KEEP_DAYS:-14}"
DIR="${BACKUP_DIR:-$ROOT/backups}"
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)

set -a; source .env; set +a      # POSTGRES_USER / POSTGRES_DB 읽기

mkdir -p "$DIR"
umask 077
OUT="$DIR/${POSTGRES_DB}-$(date +%Y%m%d-%H%M%S).sql.gz"
TMP="$OUT.partial"

# 실패한 덤프가 정상 파일처럼 남지 않도록 임시 이름으로 받고 성공하면 바꾼다.
"${COMPOSE[@]}" exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner | gzip > "$TMP"
gzip -t "$TMP"
mv "$TMP" "$OUT"
echo "$(date -Is) ok $OUT ($(du -h "$OUT" | cut -f1))"

find "$DIR" -name "${POSTGRES_DB}-*.sql.gz" -mtime +"$KEEP_DAYS" -delete