#!/usr/bin/env bash
set -euo pipefail

migration_root=/tmp/migration-sqls
migration_files_csv=${VTEST_MIGRATION_FILES:-}

reject() {
  echo "invalid explicit migration: $1" >&2
  exit 1
}

[ -d "$migration_root" ] || reject "staging root is missing"
[ ! -L "$migration_root" ] || reject "staging root must not be a symlink"
migration_root_real=$(realpath -- "$migration_root") || reject "cannot resolve staging root"
[ -d "$migration_root_real" ] || reject "canonical staging root is not a directory"

[ -n "$migration_files_csv" ] || reject "file path is empty"
case ",$migration_files_csv," in
  *,,*) reject "file path is empty" ;;
esac

IFS=',' read -r -a migration_files <<< "$migration_files_csv"
migration_files_real=()
for migration_file in "${migration_files[@]}"; do
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
  migration_files_real+=("$migration_file_real")
done

materialized_dir=$(mktemp -d /tmp/vtest-migration-batch.XXXXXX) || reject "cannot create materialization directory"
cleanup() {
  rm -rf -- "$materialized_dir"
}
trap cleanup EXIT

materialized_files=()
materialized_index=0
for migration_file_real in "${migration_files_real[@]}"; do
  printf -v materialized_file '%s/%06d.sql' "$materialized_dir" "$materialized_index"
  cp -- "$migration_file_real" "$materialized_file" || reject "cannot materialize migration"
  [ -s "$materialized_file" ] || reject "materialized migration is empty"
  materialized_files+=("$materialized_file")
  materialized_index=$((materialized_index + 1))
done

for materialized_index in "${!materialized_files[@]}"; do
  printf 'Applying allowlisted migration: %s\n' "${migration_files_real[$materialized_index]}" >&2
  docker exec -i openclaw-postgres psql \
    -X \
    --single-transaction \
    -U "${VTEST_POSTGRES_USER:?VTEST_POSTGRES_USER is required}" \
    -d "${VTEST_POSTGRES_DB:?VTEST_POSTGRES_DB is required}" \
    -v ON_ERROR_STOP=1 \
    -f - < "${materialized_files[$materialized_index]}"
done
