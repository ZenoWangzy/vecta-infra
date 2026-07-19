#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/mypc-app-regression.sh --service wecom-contact-sync --phase before|after

Read-only application migration checks for one approved mypc service.
USAGE
}

service=''
phase=''

while [ "$#" -gt 0 ]; do
  case "$1" in
    --service) service="${2:-}"; shift 2 ;;
    --phase) phase="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

case "$service" in
  wecom-contact-sync|rag-service|fleet-gateway|channel-gateway) ;;
  *) echo 'unsupported service' >&2; exit 2 ;;
esac
case "$phase" in before|after) ;; *) echo 'phase must be before or after' >&2; exit 2 ;; esac

check_http() {
  local name="$1" url="$2" code attempt
  for attempt in 1 2 3; do
    code="$(curl -fsS -o /dev/null -w '%{http_code}' --max-time 5 "$url" || true)"
    [ "$code" = 200 ] && { echo "OK   $name $code"; return 0; }
    [ "$attempt" = 3 ] || sleep 2
  done
  echo "FAIL $name returned ${code:-curl-error}" >&2
  exit 1
}

expected_image="${EXPECTED_IMAGE:-}"
expected_platform_root_employee_ids="${EXPECTED_PLATFORM_ROOT_EMPLOYEE_IDS:-}"

echo "mypc app regression: service=$service phase=$phase"
case "$service" in
  wecom-contact-sync)
    container=openclaw-wecom-contact-sync
    docker inspect "$container" >/dev/null
    [ "$(docker inspect -f '{{.State.Running}}' "$container")" = true ]
    echo "OK   container running: $container"
    docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{range $v.Aliases}}{{println .}}{{end}}{{end}}' "$container" \
      | grep -qx 'wecom-contact-sync'
    echo 'OK   production network alias: wecom-contact-sync'
    docker top "$container" -eo pid,args | grep -q '[w]ecom-contact-sync.js'
    echo 'OK   scheduled contact-sync process present'
    ;;
  rag-service)
    container=openclaw-rag-service
    docker inspect "$container" >/dev/null
    [ "$(docker inspect -f '{{.State.Running}}' "$container")" = true ]
    echo "OK   container running: $container"
    docker inspect "$container" | jq -e \
      '([.[0].Mounts[] | select(.Type == "volume" and .Name == "openclaw-enterprise_rag_model_cache" and .Destination == "/home/node/.cache/huggingface" and .RW)] | length == 1)
       and ([.[0].Mounts[] | select(.Type == "bind" and .Source == "/data/ocee/packages/rag-service/knowledge" and .Destination == "/app/knowledge" and .RW)] | length == 1)' >/dev/null
    echo 'OK   production RAG mounts preserved'
    check_http rag-service "${RAG_HEALTH_URL:-http://127.0.0.1:8000/healthz}"
    ;;
  fleet-gateway)
    container=openclaw-fleet-gateway
    docker inspect "$container" >/dev/null
    [ "$(docker inspect -f '{{.State.Running}}' "$container")" = true ]
    echo "OK   container running: $container"
    docker inspect "$container" | jq -e \
      '([.[0].NetworkSettings.Networks | keys[]] | sort == ["openclaw-enterprise_open-webui-net", "openclaw-enterprise_openclaw-net"])
       and ([.[0].Mounts[] | select(.Source == "/data/ocee/data/instances" and .Destination == "/app/data/instances" and .RW)] | length == 1)' >/dev/null
    echo 'OK   production Fleet networks and instance bind preserved'
    if [ -n "$expected_platform_root_employee_ids" ]; then
      actual_platform_root_employee_ids="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$container" \
        | sed -n 's/^PLATFORM_ROOT_EMPLOYEE_IDS=//p')"
      [ "$actual_platform_root_employee_ids" = "$expected_platform_root_employee_ids" ]
      echo 'OK   configured platform-root identities preserved'
    fi
    check_http fleet-gateway "${FLEET_HEALTH_URL:-http://127.0.0.1:3000/healthz}"
    ;;
  channel-gateway)
    container=openclaw-channel-gateway
    docker inspect "$container" >/dev/null
    [ "$(docker inspect -f '{{.State.Running}}' "$container")" = true ]
    echo "OK   container running: $container"
    check_http channel-gateway "${CHANNEL_HEALTH_URL:-http://127.0.0.1:9000/healthz}"
    curl -fsS --max-time 5 "${CHANNEL_HEALTH_URL:-http://127.0.0.1:9000/healthz}" |
      jq -e '.channelControl.channelOwnerId == "mypc" and .channelControl.channelActive and .channelControl.shouldConnectChannels and .wecom.authenticated and .feishu.wsReadyState == 1' >/dev/null
    echo 'OK   active mypc channel ownership and adapters preserved'
    ;;
esac

if [ -n "$expected_image" ]; then
  [ "$(docker inspect -f '{{.Config.Image}}' "$container")" = "$expected_image" ]
  echo "OK   container image: $expected_image"
fi

docker exec openclaw-postgres pg_isready -U "${POSTGRES_USER:-openclaw_poc}" -d "${POSTGRES_DB:-openclaw_poc}" >/dev/null
echo 'OK   postgres reachable'
docker exec openclaw-redis redis-cli ping | grep -qx PONG
echo 'OK   redis reachable'
check_http fleet-gateway "${FLEET_HEALTH_URL:-http://127.0.0.1:3000/healthz}"
echo "OK   mypc $service regression complete"
