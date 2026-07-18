#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/mypc-data-layer-regression.sh [--service SERVICE] [--phase before|after]

Read-only mypc data-layer regression checks. SERVICE may be one of:
postgres, redis, minio, clickhouse, all.

The script does not create, update, delete, migrate, restart, or prune anything.
Run it before and after each approved one-service data-layer adoption step.
USAGE
}

service=all
phase=after

while [ "$#" -gt 0 ]; do
  case "$1" in
    --service)
      service="${2:-}"
      shift 2
      ;;
    --phase)
      phase="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "$service" in
  postgres|redis|minio|clickhouse|all) ;;
  *) echo "invalid --service: $service" >&2; exit 2 ;;
esac

case "$phase" in
  before|after) ;;
  *) echo "invalid --phase: $phase" >&2; exit 2 ;;
esac

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing required command: $1" >&2
    exit 1
  }
}

check_http() {
  local name="$1"
  local url="$2"
  local expected="${3:-200}"
  local code
  code="$(curl -fsS -o /dev/null -w '%{http_code}' --max-time 5 "$url" || true)"
  if [ "$code" != "$expected" ]; then
    echo "FAIL $name $url -> ${code:-curl-error}, expected $expected" >&2
    exit 1
  fi
  echo "OK   $name $url -> $code"
}

container_exists() {
  docker inspect "$1" >/dev/null 2>&1
}

container_running() {
  [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null || true)" = true ]
}

check_container_running() {
  local name="$1"
  container_exists "$name" || { echo "FAIL container missing: $name" >&2; exit 1; }
  container_running "$name" || { echo "FAIL container not running: $name" >&2; exit 1; }
  echo "OK   container running: $name"
}

check_container_health_if_configured() {
  local name="$1"
  local status
  status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$name")"
  case "$status" in
    healthy) echo "OK   container healthy: $name" ;;
    none) echo "SKIP container has no Docker healthcheck: $name" ;;
    *) echo "FAIL container health for $name: $status" >&2; exit 1 ;;
  esac
}

check_postgres() {
  local container="${POSTGRES_CONTAINER:-openclaw-postgres}"
  local db="${POSTGRES_DB:-openclaw_poc}"
  local user="${POSTGRES_USER:-openclaw_poc}"

  check_container_running "$container"
  docker exec "$container" pg_isready -U "$user" -d "$db" >/dev/null
  echo "OK   postgres pg_isready"
  docker exec "$container" psql -U "$user" -d "$db" -v ON_ERROR_STOP=1 -tAc \
    "select count(*) >= 1 from information_schema.tables where table_schema = 'public';" \
    | grep -qx t
  echo "OK   postgres public schema has tables"
  docker exec "$container" psql -U "$user" -d "$db" -v ON_ERROR_STOP=1 -tAc \
    "select count(*) from pg_namespace where nspname not like 'pg_%' and nspname <> 'information_schema';" \
    | awk '{ if ($1 + 0 < 1) exit 1 }'
  echo "OK   postgres user schemas visible"
}

check_redis() {
  local container="${REDIS_CONTAINER:-openclaw-redis}"
  check_container_running "$container"
  docker exec "$container" redis-cli ping | grep -qx PONG
  echo "OK   redis ping"
  docker exec "$container" redis-cli dbsize | awk '{ if ($1 < 0) exit 1 }'
  echo "OK   redis dbsize readable"
}

check_minio() {
  local container="${MINIO_CONTAINER:-openclaw-minio}"
  check_container_running "$container"
  docker exec "$container" sh -lc 'test -d /data && test -r /data'
  echo "OK   minio data directory readable"
  docker exec "$container" sh -lc 'ls -A /data | wc -l' \
    | awk '{ if ($1 < 1) exit 1 }'
  echo "OK   minio data has top-level entries"
  if [ -n "${MINIO_HEALTH_URL:-}" ]; then
    check_http "minio api" "$MINIO_HEALTH_URL" 200
  else
    check_container_health_if_configured "$container"
  fi
}

check_clickhouse() {
  local container="${CLICKHOUSE_CONTAINER:-openclaw-clickhouse}"
  if ! container_exists "$container"; then
    echo "SKIP clickhouse container missing: $container"
    return 0
  fi
  check_container_running "$container"
  docker exec "$container" clickhouse-client --query 'SELECT 1' | grep -qx 1
  echo "OK   clickhouse select 1"
}

check_app_health() {
  check_http "fleet-gateway" "${FLEET_HEALTH_URL:-http://127.0.0.1:3000/healthz}" 200
  check_http "admin-console" "${ADMIN_CONSOLE_URL:-http://127.0.0.1:5173/}" 200
  check_http "directory-service" "${DIRECTORY_HEALTH_URL:-http://127.0.0.1:8001/healthz}" 200
  check_http "rag-service" "${RAG_HEALTH_URL:-http://127.0.0.1:8000/healthz}" 200
  check_http "channel-gateway" "${CHANNEL_HEALTH_URL:-http://127.0.0.1:9000/healthz}" 200
  check_http "open-webui-proxy" "${OPEN_WEBUI_PROXY_HEALTH_URL:-http://127.0.0.1:3002/health}" 200
}

require_cmd docker
require_cmd curl

if [ -f /data/ocee/.env ]; then
  set -a
  # shellcheck disable=SC1091
  . /data/ocee/.env
  set +a
fi

echo "mypc data-layer regression: phase=$phase service=$service"

case "$service" in
  postgres) check_postgres ;;
  redis) check_redis ;;
  minio) check_minio ;;
  clickhouse) check_clickhouse ;;
  all)
    check_postgres
    check_redis
    check_minio
    check_clickhouse
    ;;
esac

check_app_health
echo "OK   mypc data-layer regression complete"
