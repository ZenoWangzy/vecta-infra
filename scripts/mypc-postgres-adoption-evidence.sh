#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/mypc-postgres-adoption-evidence.sh --execute [--evidence-root PATH]

Creates the PostgreSQL evidence package required before a mypc Nexus adoption:
a schema/ledger inventory, custom-format logical dump, isolated restore rehearsal,
and schema/row-invariant comparison.

It never mounts, copies, renames, prunes, or modifies the live PostgreSQL volume.
The temporary restore uses a bind directory below the evidence root, not a Docker
volume, and is removed after a successful rehearsal.
USAGE
}

execute=false
evidence_root=/data/ocee/backups/postgres-adoption

while [ "$#" -gt 0 ]; do
  case "$1" in
    --execute) execute=true; shift ;;
    --evidence-root) evidence_root="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ "$execute" != true ]; then
  echo "--execute is required; this command writes a backup and restore rehearsal evidence package" >&2
  exit 2
fi

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing required command: $1" >&2
    exit 1
  }
}

for command in docker sha256sum sed diff cmp; do
  require_cmd "$command"
done

[ "$(id -u)" -eq 0 ] || {
  echo "run as root so evidence artifacts remain protected" >&2
  exit 1
}
[ -f /data/ocee/.env ] || { echo "missing /data/ocee/.env" >&2; exit 1; }

set -a
# shellcheck disable=SC1091
. /data/ocee/.env
set +a

: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"

source_container=${POSTGRES_CONTAINER:-openclaw-postgres}
postgres_image=${POSTGRES_RESTORE_IMAGE:-127.0.0.1:8082/pgvector/pgvector:pg16}
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
evidence_dir="${evidence_root%/}/postgres-adoption-${timestamp}"
dump_file="$evidence_dir/postgres.dump"
source_schema="$evidence_dir/source-schema.sql"
restore_schema="$evidence_dir/restored-schema.sql"
source_inventory="$evidence_dir/source-inventory.json"
restore_inventory="$evidence_dir/restored-inventory.json"
source_counts="$evidence_dir/source-row-counts.tsv"
restore_counts="$evidence_dir/restored-row-counts.tsv"
restore_data="$evidence_dir/restore-data"
restore_container="vecta-postgres-restore-${timestamp}"
success=false

cleanup() {
  docker rm -f "$restore_container" >/dev/null 2>&1 || true
  rm -rf "$restore_data"
}
trap cleanup EXIT

docker inspect "$source_container" >/dev/null
docker exec "$source_container" pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null
docker image inspect "$postgres_image" >/dev/null

install -d -m 0700 "$evidence_dir"
install -d -m 0700 "$restore_data"

inventory_sql="$(cat <<'SQL'
SELECT jsonb_build_object(
  'database', current_database(),
  'server_version', current_setting('server_version'),
  'schemas', (
    SELECT jsonb_agg(nspname ORDER BY nspname)
    FROM pg_namespace
    WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema'
  ),
  'public_table_count', (
    SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'
  ),
  'extensions', (SELECT jsonb_agg(extname ORDER BY extname) FROM pg_extension),
  'drizzle_migrations', (
    SELECT coalesce(
      jsonb_agg(jsonb_build_object('id', id, 'hash', hash, 'created_at', created_at) ORDER BY id),
      '[]'::jsonb
    ) FROM drizzle.__drizzle_migrations
  )
);
SQL
)"

count_tables=(tenants employees files fleet_instances knowledge_documents)
capture_counts() {
  local container="$1"
  local output="$2"
  : > "$output"
  for table in "${count_tables[@]}"; do
    count="$(docker exec "$container" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
      -v ON_ERROR_STOP=1 -At -c "SELECT count(*) FROM public.\"${table}\";")"
    printf '%s\t%s\n' "$table" "$count" >> "$output"
  done
}

echo "Capturing PostgreSQL schema and logical backup evidence in $evidence_dir"
docker exec "$source_container" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  --schema-only --no-owner --no-privileges > "$source_schema"
docker exec "$source_container" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -v ON_ERROR_STOP=1 -At -c "$inventory_sql" > "$source_inventory"
capture_counts "$source_container" "$source_counts"
docker exec "$source_container" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  --format=custom --no-owner --no-privileges > "$dump_file"
test -s "$dump_file"
sha256sum "$dump_file" > "$evidence_dir/postgres.dump.sha256"
docker run --rm --network none \
  -v "$dump_file:/backup/postgres.dump:ro" \
  "$postgres_image" pg_restore --list /backup/postgres.dump \
  > "$evidence_dir/postgres.dump.manifest"

docker run -d --rm --name "$restore_container" --network none \
  -e "POSTGRES_USER=$POSTGRES_USER" \
  -e "POSTGRES_PASSWORD=restore-rehearsal-only" \
  -e "POSTGRES_DB=$POSTGRES_DB" \
  -v "$restore_data:/var/lib/postgresql/data" \
  -v "$dump_file:/backup/postgres.dump:ro" \
  "$postgres_image" >/dev/null

for _ in $(seq 1 60); do
  if docker exec "$restore_container" pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
docker exec "$restore_container" pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null
docker exec "$restore_container" pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  --exit-on-error --no-owner --no-privileges /backup/postgres.dump

docker exec "$restore_container" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  --schema-only --no-owner --no-privileges > "$restore_schema"
docker exec "$restore_container" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -v ON_ERROR_STOP=1 -At -c "$inventory_sql" > "$restore_inventory"
capture_counts "$restore_container" "$restore_counts"

cat > "$evidence_dir/restore-rehearsal-pending-review.txt" <<EOF
timestamp_utc=$timestamp
source_container=$source_container
source_database=$POSTGRES_DB
restore_image=$postgres_image
dump_sha256=$(awk '{print $1}' "$evidence_dir/postgres.dump.sha256")
restore_completed=true
schema_review=pending
EOF

# PostgreSQL 16 emits random psql guard tokens in each dump. Ignore only those
# tokens so a non-empty diff remains meaningful schema evidence.
sed -e '/^\\restrict /d' -e '/^\\unrestrict /d' "$source_schema" > "$evidence_dir/source-schema.normalized.sql"
sed -e '/^\\restrict /d' -e '/^\\unrestrict /d' "$restore_schema" > "$evidence_dir/restored-schema.normalized.sql"
diff -u "$evidence_dir/source-schema.normalized.sql" \
  "$evidence_dir/restored-schema.normalized.sql" > "$evidence_dir/schema.diff" || {
  echo "restore schema differs from source schema; see $evidence_dir/schema.diff" >&2
  exit 1
}
cmp -s "$source_inventory" "$restore_inventory" || {
  echo "restore inventory differs from source inventory" >&2
  exit 1
}
cmp -s "$source_counts" "$restore_counts" || {
  echo "restore row-count invariants differ from source; see $evidence_dir" >&2
  exit 1
}

cat > "$evidence_dir/restore-rehearsal.txt" <<EOF
timestamp_utc=$timestamp
source_container=$source_container
source_database=$POSTGRES_DB
restore_container=$restore_container
restore_image=$postgres_image
dump_sha256=$(awk '{print $1}' "$evidence_dir/postgres.dump.sha256")
schema_compare=identical_after_psql_guard_normalization
inventory_compare=identical
row_invariants=identical
temporary_restore_data=removed_after_success
EOF
rm -f "$evidence_dir/restore-rehearsal-pending-review.txt"
chmod 0600 "$evidence_dir"/*
success=true
echo "OK PostgreSQL backup and isolated restore rehearsal complete: $evidence_dir"
