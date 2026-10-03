#!/usr/bin/env bash
# db/migrations/*.sql 중 아직 적용 안 된 파일을 파일명 순서로 적용
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

psql_exec -q -c "create table if not exists schema_migrations (
  version text primary key,
  applied_at timestamptz not null default now()
)"

for f in db/migrations/*.sql; do
  v="$(basename "$f")"
  done_flag="$(psql_exec -tA -c "select 1 from schema_migrations where version = '$v'")"
  if [ -z "$done_flag" ]; then
    echo "applying $v"
    {
      echo "begin;"
      cat "$f"
      echo
      echo "insert into schema_migrations (version) values ('$v');"
      echo "commit;"
    } | psql_exec -q
  else
    echo "skip     $v"
  fi
done
echo "done"