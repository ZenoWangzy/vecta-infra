#!/usr/bin/env bash
set -euo pipefail

migration_root=/tmp/migration-sqls
migration_file=${VTEST_MIGRATION_FILE:-}

reject() {
  echo "invalid explicit migration: $1" >&2
  exit 1
}

[ -d "$migration_root" ] || reject "staging root is missing"
[ ! -L "$migration_root" ] || reject "staging root must not be a symlink"
migration_root_real=$(realpath -- "$migration_root") || reject "cannot resolve staging root"
[ -d "$migration_root_real" ] || reject "canonical staging root is not a directory"

[ -n "$migration_file" ] || reject "file path is empty"
migration_name=$(basename -- "$migration_file")
[ "$migration_file" = "$migration_root/$migration_name" ] || reject "file must be a direct child of $migration_root"
case "$migration_name" in
  ''|.*|*..*|*[!A-Za-z0-9._-]*) reject "unsafe file name" ;;
esac
case "$migration_name" in
  *.sql) ;;
  *) reject "file must use the .sql extension" ;;
esac

[ -f "$migration_file" ] || reject "file is not a regular file"
[ ! -L "$migration_file" ] || reject "file must not be a symlink"
[ -s "$migration_file" ] || reject "file is empty"
migration_file_real=$(realpath -- "$migration_file") || reject "cannot resolve file"
[ "$(dirname -- "$migration_file_real")" = "$migration_root_real" ] || reject "canonical file is outside the staging root"
[ "$migration_file_real" = "$migration_root_real/$migration_name" ] || reject "canonical file name changed"

echo "Applying allowlisted migration: $migration_file"
docker exec -i openclaw-postgres psql \
  -U "${VTEST_POSTGRES_USER:?VTEST_POSTGRES_USER is required}" \
  -d "${VTEST_POSTGRES_DB:?VTEST_POSTGRES_DB is required}" \
  -v ON_ERROR_STOP=1 < "$migration_file_real"
