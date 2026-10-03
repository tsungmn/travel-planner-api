#!/usr/bin/env bash
# 사용법: scripts/restore.sh <backup.sql.gz>
# 현재 DB를 삭제하고 백업으로 교체한다.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

file="${1:?usage: scripts/restore.sh <backup.sql.gz>}"
[ -f "$file" ] || { echo "not found: $file"; exit 1; }

read -r -p "DB '$PG_DB' 를 삭제하고 $file 로 복원합니다. 계속할까요? (yes/no) " ans
[ "$ans" = "yes" ] || { echo "취소"; exit 1; }

docker compose stop api
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U "$PG_USER" -d postgres \
  -c "drop database if exists \"$PG_DB\" with (force)" \
  -c "create database \"$PG_DB\""
gunzip -c "$file" | psql_exec -q
docker compose start api
echo "restored"