#!/usr/bin/env bash
# PostgreSQL 백업: pg_dump -> gzip, 오래된 파일 자동 삭제.
# 사용: scripts/backup.sh            (어느 위치에서 실행해도 됩니다)
# cron 예시 (매일 새벽 3시):
#   0 3 * * * /home/<유저>/travel-planner-api/scripts/backup.sh >> /home/<유저>/travel-planner-api/backups/backup.log 2>&1
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

KEEP_DAYS="${KEEP_DAYS:-14}"
DIR="${BACKUP_DIR:-$ROOT/backups}"
COMPOSE=(docker compose -f docker-compose.db.yml)

# DB 이름은 .env 를 읽지 않고 DB 컨테이너 안의 환경 변수에서 가져옵니다.
DB_NAME="$("${COMPOSE[@]}" exec -T db printenv POSTGRES_DB)"

mkdir -p "$DIR"
umask 077
OUT="$DIR/${DB_NAME}-$(date +%Y%m%d-%H%M%S).sql.gz"
TMP="$OUT.partial"
trap 'rm -f "$TMP"' EXIT   # 실패하면 임시 파일을 남기지 않는다

# 실패한 덤프가 정상 파일처럼 남지 않도록 임시 이름으로 받고, 성공하면 이름을 바꾼다.
"${COMPOSE[@]}" exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner' | gzip > "$TMP"
gzip -t "$TMP"

# 빈 출력(gzip 후 약 20바이트)은 실패로 본다
if [ "$(stat -c%s "$TMP")" -lt 100 ]; then
  echo "$(date -Is) FAIL 백업 파일이 너무 작습니다: $TMP" >&2
  exit 1
fi

mv "$TMP" "$OUT"
echo "$(date -Is) ok $OUT ($(du -h "$OUT" | cut -f1))"

find "$DIR" -name "${DB_NAME}-*.sql.gz" -mtime +"$KEEP_DAYS" -delete