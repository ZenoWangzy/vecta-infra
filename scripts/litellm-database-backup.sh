#!/usr/bin/env bash
# Backup + restore-drill for LiteLLM's own Postgres database (ticket 1052).
# Sibling in spirit to scripts/hermes-fleet-state-backup.sh but much smaller:
# this database only holds LiteLLM's key/spend/model bookkeeping (tens of MB
# for this workload, see the pre-existing `litellm` database used by
# openclaw-fruit-litellm as a size reference), so one pg_dump custom-format
# file is the whole backup unit -- no per-row manifest needed.
set -euo pipefail

DB_CONTAINER="${DB_CONTAINER:-openclaw-postgres}"
ADMIN_USER="${ADMIN_USER:-openclaw_poc}"
DB_NAME="litellm_gateway"
BACKUP_ROOT="${BACKUP_ROOT:-/data/ocee/backups/litellm-gateway}"

usage() {
  cat <<'EOF' >&2
Usage:
  scripts/litellm-database-backup.sh backup [--execute]
  scripts/litellm-database-backup.sh restore-drill --backup-file PATH [--execute]

Without --execute both subcommands only print what they would do.
EOF
}

cmd="${1:-}"
[ -n "$cmd" ] && shift || true

case "$cmd" in
  backup)
    execute=false
    if [ "${1:-}" = "--execute" ]; then execute=true; shift; fi
    ts="$(date -u +%Y%m%dT%H%M%SZ)"
    target="$BACKUP_ROOT/litellm-gateway-$ts.dump"
    echo "would dump database '$DB_NAME' -> $target"
    if [ "$execute" != true ]; then
      echo "preflight only; pass --execute to create the backup"
      exit 0
    fi
    install -d -m 0750 "$BACKUP_ROOT"
    docker exec "$DB_CONTAINER" pg_dump -U "$ADMIN_USER" -d "$DB_NAME" -Fc -f "/tmp/litellm-gateway-$ts.dump"
    docker cp "$DB_CONTAINER:/tmp/litellm-gateway-$ts.dump" "$target"
    docker exec "$DB_CONTAINER" rm -f "/tmp/litellm-gateway-$ts.dump"
    chmod 640 "$target"
    echo "backup written: $target"
    ;;
  restore-drill)
    backup_file=""
    execute=false
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --backup-file) backup_file="${2:?--backup-file requires a path}"; shift 2 ;;
        --execute) execute=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage; exit 2 ;;
      esac
    done
    [ -n "$backup_file" ] && [ -f "$backup_file" ] || { echo "--backup-file PATH is required and must exist" >&2; usage; exit 2; }
    drill_db="litellm_gateway_restore_drill"
    echo "would restore $backup_file -> throwaway database '$drill_db', compare table counts against '$DB_NAME', then drop '$drill_db'"
    if [ "$execute" != true ]; then
      echo "preflight only; pass --execute to run the drill"
      exit 0
    fi
    docker exec "$DB_CONTAINER" psql -U "$ADMIN_USER" -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS $drill_db;" >/dev/null
    docker exec "$DB_CONTAINER" psql -U "$ADMIN_USER" -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE $drill_db OWNER litellm_gateway;" >/dev/null
    docker cp "$backup_file" "$DB_CONTAINER:/tmp/restore-drill.dump"
    docker exec "$DB_CONTAINER" pg_restore -U "$ADMIN_USER" -d "$drill_db" --no-owner --role="$ADMIN_USER" /tmp/restore-drill.dump
    docker exec "$DB_CONTAINER" rm -f /tmp/restore-drill.dump
    orig_tables="$(docker exec "$DB_CONTAINER" psql -U "$ADMIN_USER" -d "$DB_NAME" -Atc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")"
    drill_tables="$(docker exec "$DB_CONTAINER" psql -U "$ADMIN_USER" -d "$drill_db" -Atc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")"
    echo "table count: original=$orig_tables restored=$drill_tables"
    docker exec "$DB_CONTAINER" psql -U "$ADMIN_USER" -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE $drill_db;" >/dev/null
    if [ "$orig_tables" != "$drill_tables" ]; then
      echo "MISMATCH: restore drill did not reproduce the schema" >&2
      exit 1
    fi
    echo "restore drill passed: $drill_tables tables restored and verified, throwaway database dropped"
    ;;
  *)
    usage
    exit 2
    ;;
esac
