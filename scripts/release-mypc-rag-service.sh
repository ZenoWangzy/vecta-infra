#!/usr/bin/env bash
set -euo pipefail

export LC_ALL=C

readonly EXPECTED_HOSTNAME='mypc'
readonly RAG_SERVICE='rag-service'
readonly RAG_CONTAINER='openclaw-rag-service'
readonly EXPECTED_HF_ENDPOINT='https://hf-mirror.com'
readonly SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly BASE_COMPOSE="${RAG_BASE_COMPOSE:-/data/ocee/migration-compose.config.yml}"
readonly ENDPOINT_OVERRIDE="${RAG_RELEASE_OVERRIDE:-${SOURCE_ROOT}/deploy/mypc/rag-hf-endpoint.override.yml}"
readonly IMAGE_OVERRIDE="${RAG_RELEASE_IMAGE_OVERRIDE:-${SOURCE_ROOT}/deploy/mypc/rag-service-image.override.yml}"
readonly CONTRACT_CHECKER="${SOURCE_ROOT}/scripts/check-mypc-rag-release-contract.mjs"
readonly DOCKER_BIN="${DOCKER_BIN:-/usr/bin/docker}"
readonly CURL_BIN="${CURL_BIN:-/usr/bin/curl}"
readonly HOSTNAME_BIN="${RAG_HOSTNAME_BIN:-/usr/bin/hostname}"
readonly ID_BIN="${RAG_ID_BIN:-/usr/bin/id}"
readonly FLOCK_BIN="${FLOCK_BIN:-/usr/bin/flock}"
readonly LOCK_FILE="${RAG_RELEASE_LOCK_FILE:-/run/vecta-rag-service-release.lock}"
readonly RETRIES="${RAG_RELEASE_RETRIES:-30}"
readonly RETRY_DELAY_SECONDS="${RAG_RELEASE_RETRY_DELAY_SECONDS:-5}"
readonly HEALTH_URL="${RAG_HEALTH_URL:-http://127.0.0.1:8000/healthz}"

WORK_DIR=''
BASELINE_CONFIG=''
TARGET_CONFIG=''
BASELINE_IMAGE=''
MUTATION_STARTED=0
LOCK_HELD=0

fail() { printf 'FAIL: %s\n' "$*" >&2; return 1; }
usage() { printf 'Usage: %s --check|--execute\n' "$0" >&2; exit 2; }

docker_local() {
  /usr/bin/env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_CONFIG \
    "$DOCKER_BIN" "$@"
}

is_target_image() {
  [[ "$1" =~ ^127\.0\.0\.1:8082/rag-service@sha256:[0-9a-f]{64}$ ]]
}

is_rollback_image() {
  [[ "$1" =~ ^127\.0\.0\.1:8082/rag-service:([0-9a-f]{40}|cache-[0-9a-f]+)$ ]]
}

require_hostname() {
  local hostname
  hostname="$("$HOSTNAME_BIN")" || return 1
  [[ "$hostname" == "$EXPECTED_HOSTNAME" ]] || fail 'refusing RAG release unless hostname is exactly mypc'
}

require_root() {
  local uid
  uid="$("$ID_BIN" -u)" || return 1
  [[ "$uid" == 0 ]] || fail 'refusing RAG release unless running as root'
}

require_execute_approval() {
  [[ "${MYPC_DEPLOY_ENABLED:-}" == 'true' ]] || fail 'refusing execute without MYPC_DEPLOY_ENABLED=true'
}

require_files() {
  [[ -x "$DOCKER_BIN" ]] || fail 'Docker binary is unavailable'
  [[ -x "$CURL_BIN" ]] || fail 'curl binary is unavailable'
  [[ -f "$BASE_COMPOSE" ]] || fail 'authoritative mypc Compose source is unavailable'
  [[ -f "$ENDPOINT_OVERRIDE" ]] || fail 'versioned RAG endpoint override is unavailable'
  [[ -f "$IMAGE_OVERRIDE" ]] || fail 'versioned RAG image override is unavailable'
  [[ -f "$CONTRACT_CHECKER" ]] || fail 'RAG release contract checker is unavailable'
  [[ -n "${RAG_SERVICE_IMAGE:-}" ]] || fail 'RAG_SERVICE_IMAGE is required'
  is_target_image "$RAG_SERVICE_IMAGE" || fail 'RAG_SERVICE_IMAGE must be a local Nexus immutable digest reference'
}

release_lock() {
  if (( LOCK_HELD != 0 )); then
    exec 9>&-
    LOCK_HELD=0
  fi
}

cleanup() {
  release_lock
  if [[ -n "$WORK_DIR" && -d "$WORK_DIR" ]]; then
    rm -rf -- "$WORK_DIR"
  fi
}

acquire_lock() {
  umask 077
  exec 9>"$LOCK_FILE"
  "$FLOCK_BIN" -n 9 || { fail 'another RAG release is already running'; return 1; }
  LOCK_HELD=1
}

create_work_dir() {
  umask 077
  WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/vecta-rag-release.XXXXXX")"
  BASELINE_CONFIG="${WORK_DIR}/baseline.json"
  TARGET_CONFIG="${WORK_DIR}/target.json"
}

render_config() {
  local image="$1" override="$2" output="$3" base_dir
  base_dir="$(dirname "$BASE_COMPOSE")"
  if ! /usr/bin/env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_CONFIG \
    RAG_SERVICE_IMAGE="$image" "$DOCKER_BIN" compose \
      --project-directory "$base_dir" \
      -f "$BASE_COMPOSE" -f "$override" config --format json \
      >"$output" 2>"${WORK_DIR}/compose.stderr"; then
    fail 'failed to render the RAG Compose release contract'
    return 1
  fi
  chmod 600 "$output"
}

capture_baseline() {
  BASELINE_IMAGE="$(docker_local inspect --format '{{.Config.Image}}' "$RAG_CONTAINER")" || {
    fail 'running RAG container cannot provide a rollback image'; return 1;
  }
  is_rollback_image "$BASELINE_IMAGE" || {
    fail 'running RAG image is not an immutable rollback reference'; return 1;
  }
}

checker_match() {
  local config="$1" endpoint="${2:-}"
  local args=(--mode match --container "$RAG_CONTAINER" --target "$config")
  [[ -n "$endpoint" ]] && args+=(--endpoint "$endpoint")
  if /usr/bin/env DOCKER_BIN="$DOCKER_BIN" node "$CONTRACT_CHECKER" "${args[@]}"; then
    return 0
  fi
  return 1
}

checker_transition() {
  /usr/bin/env DOCKER_BIN="$DOCKER_BIN" node "$CONTRACT_CHECKER" \
    --mode transition --container "$RAG_CONTAINER" \
    --baseline "$BASELINE_CONFIG" --target "$TARGET_CONFIG" \
    --endpoint "$EXPECTED_HF_ENDPOINT"
}

prepare_contract() {
  capture_baseline
  render_config "$BASELINE_IMAGE" "$IMAGE_OVERRIDE" "$BASELINE_CONFIG"
  render_config "$RAG_SERVICE_IMAGE" "$ENDPOINT_OVERRIDE" "$TARGET_CONFIG"
  if checker_match "$TARGET_CONFIG" "$EXPECTED_HF_ENDPOINT" >/dev/null 2>&1; then
    printf 'RESULT=noop\n'
    return 10
  fi
  checker_transition
}

check_rag_health() {
  local attempt body
  for ((attempt = 1; attempt <= RETRIES; attempt += 1)); do
    body="$("$CURL_BIN" -fsS --max-time 10 "$HEALTH_URL" 2>/dev/null || true)"
    if printf '%s' "$body" | grep -Eq '"ok"[[:space:]]*:[[:space:]]*true' \
      && printf '%s' "$body" | grep -Eq '"service"[[:space:]]*:[[:space:]]*"rag-service"'; then
      printf 'OK: RAG runtime readiness\n'
      return 0
    fi
    (( attempt < RETRIES )) && sleep "$RETRY_DELAY_SECONDS"
  done
  fail 'RAG runtime readiness did not pass'
}

compose_recreate() {
  local config="$1" base_dir
  base_dir="$(dirname "$BASE_COMPOSE")"
  docker_local compose --project-directory "$base_dir" -f "$config" \
    up -d --no-build --pull never --force-recreate "$RAG_SERVICE"
}

rollback() {
  local original_status="$1"
  trap - ERR INT TERM
  set +e
  if (( MUTATION_STARTED == 0 )); then
    exit "$original_status"
  fi
  compose_recreate "$BASELINE_CONFIG"
  local recreate_status=$?
  checker_match "$BASELINE_CONFIG"
  local contract_status=$?
  check_rag_health
  local health_status=$?
  if (( recreate_status != 0 || contract_status != 0 || health_status != 0 )); then
    printf 'FAIL: release failed and exact baseline was not proven\n' >&2
    exit 1
  fi
  printf 'FAIL: release failed; exact baseline restored\n' >&2
  exit "$original_status"
}

run_check() {
  require_root
  require_hostname
  require_files
  acquire_lock
  create_work_dir
  local result=0
  prepare_contract || result=$?
  if (( result == 10 )); then
    check_rag_health
    return 0
  fi
  (( result == 0 )) || return "$result"
  printf 'RESULT=ready\n'
}

run_execute() {
  require_root
  require_hostname
  require_execute_approval
  require_files
  acquire_lock
  create_work_dir
  local result=0
  prepare_contract || result=$?
  if (( result == 10 )); then
    check_rag_health
    return 0
  fi
  (( result == 0 )) || return "$result"

  trap 'rollback 130' INT TERM
  MUTATION_STARTED=1
  if ! compose_recreate "$TARGET_CONFIG"; then
    rollback 1
  fi
  if ! checker_match "$TARGET_CONFIG" "$EXPECTED_HF_ENDPOINT"; then
    rollback 1
  fi
  if ! check_rag_health; then
    rollback 1
  fi
  trap - INT TERM
  printf 'RESULT=changed\n'
}

[[ "$#" == 1 ]] || usage
trap cleanup EXIT
case "$1" in
  --check) run_check ;;
  --execute) run_execute ;;
  *) usage ;;
esac
