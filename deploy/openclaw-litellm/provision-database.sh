#!/usr/bin/env bash
# Ticket 1052: one-time, idempotent provisioning of LiteLLM's own Postgres
# role + database inside the *existing* openclaw-postgres instance.
#
# This is deliberately NOT folded into roles/infra-services/tasks/postgres.yml:
# that role's stateful-adoption gate (mypc_stateful_services_enabled /
# mypc_postgres_adoption_approved, see inventories/mypc/group_vars/mypc.yml)
# guards *recreating the postgres container itself* and is intentionally
# fail-closed pending schema-inventory/backup/restore evidence for the whole
# instance. Adding one new role + one new database to the already-running
# instance does not touch the container's lifecycle, image, or volume, so it
# does not need that gate -- but it must still never be run by hand against
# production without reading this file first.
#
# Never prints the generated password. Safe to re-run: a no-op if
# litellm-database.env already exists, and refuses to guess if the role
# exists without a matching env file (see below) rather than risk locking
# out a value nobody has anymore.
#
# Usage: deploy/openclaw-litellm/provision-database.sh
# Run on mypc as a user that can `docker exec` into openclaw-postgres.
set -euo pipefail

DB_CONTAINER="${DB_CONTAINER:-openclaw-postgres}"
# openclaw_poc is the existing application role already used to run DDL in
# this instance (it is a superuser in the stock postgres image's default
# init). Reused here only to issue CREATE ROLE / CREATE DATABASE -- the new
# role below is what LiteLLM itself connects as.
ADMIN_USER="${ADMIN_USER:-openclaw_poc}"
ROLE_NAME="litellm_gateway"
DB_NAME="litellm_gateway"
ENV_DIR="/data/ocee/deploy/openclaw-litellm"
ENV_FILE="$ENV_DIR/litellm-database.env"

role_exists() {
  [ "$(docker exec "$DB_CONTAINER" psql -U "$ADMIN_USER" -d postgres -Atc \
    "SELECT 1 FROM pg_roles WHERE rolname='$ROLE_NAME'")" = "1" ]
}

db_exists() {
  [ "$(docker exec "$DB_CONTAINER" psql -U "$ADMIN_USER" -d postgres -Atc \
    "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'")" = "1" ]
}

if [ -f "$ENV_FILE" ]; then
  echo "already provisioned: $ENV_FILE exists, nothing to do" >&2
  exit 0
fi

if role_exists; then
  echo "refusing to proceed: role $ROLE_NAME already exists but $ENV_FILE is missing." >&2
  echo "its password is unknown to this script -- resolve by hand, do not generate a second one." >&2
  exit 1
fi

# Generated on this host, in this process, and never echoed anywhere.
NEW_PASSWORD="$(docker exec "$DB_CONTAINER" sh -c "tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32")"
[ -n "$NEW_PASSWORD" ] || { echo "failed to generate a password" >&2; exit 1; }

docker exec -i "$DB_CONTAINER" psql -U "$ADMIN_USER" -d postgres -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$ROLE_NAME') THEN
    CREATE ROLE $ROLE_NAME WITH LOGIN PASSWORD '$NEW_PASSWORD';
  END IF;
END
\$\$;
SQL

if ! db_exists; then
  docker exec "$DB_CONTAINER" psql -U "$ADMIN_USER" -d postgres -v ON_ERROR_STOP=1 \
    -c "CREATE DATABASE $DB_NAME OWNER $ROLE_NAME;"
fi

install -d -m 0700 "$ENV_DIR"
printf 'LITELLM_DATABASE_URL=postgresql://%s:%s@openclaw-postgres:5432/%s\n' \
  "$ROLE_NAME" "$NEW_PASSWORD" "$DB_NAME" > "$ENV_FILE"
chmod 600 "$ENV_FILE"
unset NEW_PASSWORD

echo "provisioned role=$ROLE_NAME database=$DB_NAME -> $ENV_FILE (mode 600, not committed)"
