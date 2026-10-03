#!/usr/bin/env bash
# 다른 스크립트에서 source 해서 사용 (직접 실행하지 않음)
cd "$(dirname "${BASH_SOURCE[0]}")/.."

[ -f .env ] || { echo ".env 파일이 없습니다 (.env.example 참고)"; exit 1; }

env_get() { grep -E "^$1=" .env | head -n1 | cut -d= -f2-; }

PG_USER="$(env_get POSTGRES_USER)"
PG_DB="$(env_get POSTGRES_DB)"

psql_exec() {
  docker compose exec -T db psql -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB" "$@"
}