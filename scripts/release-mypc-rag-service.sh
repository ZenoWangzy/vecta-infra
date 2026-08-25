#!/usr/bin/env bash
set -Eeuo pipefail

export LC_ALL=C

readonly EXPECTED_HOSTNAME='mypc'
readonly RAG_SERVICE='rag-service'
readonly RAG_CONTAINER='openclaw-rag-service'
readonly FLEET_CONTAINER='openclaw-fleet-gateway'
readonly EXPECTED_HF_ENDPOINT='https://hf-mirror.com'
readonly SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly BASE_COMPOSE="${RAG_BASE_COMPOSE:-/data/ocee/migration-compose.config.yml}"
readonly ENDPOINT_OVERRIDE="${RAG_RELEASE_OVERRIDE:-${SOURCE_ROOT}/deploy/mypc/rag-hf-endpoint.override.yml}"
readonly IMAGE_OVERRIDE="${RAG_RELEASE_IMAGE_OVERRIDE:-${SOURCE_ROOT}/deploy/mypc/rag-service-image.override.yml}"
readonly CONTRACT_CHECKER="${SOURCE_ROOT}/scripts/check-mypc-rag-release-contract.mjs"
readonly REGRESSION_SCRIPT="${RAG_REGRESSION_SCRIPT:-${SOURCE_ROOT}/scripts/mypc-app-regression.sh}"
readonly DOCKER_BIN="${DOCKER_BIN:-/usr/bin/docker}"
readonly CURL_BIN="${CURL_BIN:-/usr/bin/curl}"
readonly HOSTNAME_BIN="${RAG_HOSTNAME_BIN:-/usr/bin/hostname}"
readonly ID_BIN="${RAG_ID_BIN:-/usr/bin/id}"
readonly FLOCK_BIN="${FLOCK_BIN:-/usr/bin/flock}"
readonly TAR_BIN="${RAG_TAR_BIN:-/usr/bin/tar}"
readonly SHA256SUM_BIN="${RAG_SHA256SUM_BIN:-/usr/bin/sha256sum}"
readonly STAT_BIN="${RAG_STAT_BIN:-/usr/bin/stat}"
readonly DU_BIN="${RAG_DU_BIN:-/usr/bin/du}"
readonly DF_BIN="${RAG_DF_BIN:-/usr/bin/df}"
readonly FIND_BIN="${RAG_FIND_BIN:-/usr/bin/find}"
readonly REALPATH_BIN="${RAG_REALPATH_BIN:-/usr/bin/realpath}"
readonly LOCK_FILE="${RAG_RELEASE_LOCK_FILE:-/run/vecta-rag-service-release.lock}"
BACKUP_ROOT="${RAG_STATE_BACKUP_ROOT:-/data/ocee/backups/app-adoption}"
readonly RETRIES="${RAG_RELEASE_RETRIES:-30}"
readonly RETRY_DELAY_SECONDS="${RAG_RELEASE_RETRY_DELAY_SECONDS:-5}"
readonly HEALTH_URL="${RAG_HEALTH_URL:-http://127.0.0.1:8000/healthz}"
readonly RELEASE_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"

WORK_DIR=''
BASELINE_CONFIG=''
TARGET_CONFIG=''
BASELINE_CONFIG_FD=''
TARGET_CONFIG_FD=''
BASELINE_RUNTIME=''
BASELINE_IMAGE=''
BASELINE_IMAGE_ID=''
TARGET_IMAGE_ID=''
CACHE_VOLUME=''
CACHE_MOUNTPOINT=''
KNOWLEDGE_PATH=''
BACKUP_DIR=''
MUTATION_STARTED=0
FLEET_PAUSED_BY_RELEASE=0
RAG_STOPPED_BY_RELEASE=0
LOCK_HELD=0

fail() { printf 'FAIL: %s\n' "$*" >&2; return 1; }
usage() { printf 'Usage: %s --check|--execute\n' "$0" >&2; exit 2; }

docker_local() {
  /usr/bin/env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_CONFIG \
    "$DOCKER_BIN" "$@"
}

checker() {
  /usr/bin/env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_CONFIG \
    DOCKER_BIN="$DOCKER_BIN" node "$CONTRACT_CHECKER" "$@"
}

run_regression() {
  local phase="$1" expected_image="${2:-}"
  if [[ -n "$expected_image" ]]; then
    /usr/bin/env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_CONFIG \
      EXPECTED_IMAGE="$expected_image" "$REGRESSION_SCRIPT" \
      --service "$RAG_SERVICE" --phase "$phase"
  else
    /usr/bin/env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_CONFIG \
      "$REGRESSION_SCRIPT" --service "$RAG_SERVICE" --phase "$phase"
  fi
}

is_immutable_rag_digest() {
  [[ "$1" =~ ^127\.0\.0\.1:8082/rag-service@sha256:[0-9a-f]{64}$ ]]
}

is_full_sha() {
  [[ "$1" =~ ^[0-9a-f]{40}$ ]]
}

is_positive_integer() {
  [[ "$1" =~ ^[1-9][0-9]*$ ]]
}

is_nonnegative_integer() {
  [[ "$1" =~ ^[0-9]+$ ]]
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
  [[ -x "$TAR_BIN" ]] || fail 'tar binary is unavailable'
  [[ -x "$SHA256SUM_BIN" ]] || fail 'sha256sum binary is unavailable'
  [[ -x "$STAT_BIN" ]] || fail 'stat binary is unavailable'
  [[ -x "$DU_BIN" ]] || fail 'du binary is unavailable'
  [[ -x "$DF_BIN" ]] || fail 'df binary is unavailable'
  [[ -x "$FIND_BIN" ]] || fail 'find binary is unavailable'
  [[ -x "$REALPATH_BIN" ]] || fail 'realpath binary is unavailable'
  command -v node >/dev/null || fail 'node is unavailable'
  [[ -f "$BASE_COMPOSE" ]] || fail 'authoritative mypc Compose source is unavailable'
  [[ -f "$ENDPOINT_OVERRIDE" ]] || fail 'versioned RAG endpoint override is unavailable'
  [[ -f "$IMAGE_OVERRIDE" ]] || fail 'versioned RAG image override is unavailable'
  [[ -f "$CONTRACT_CHECKER" ]] || fail 'RAG release contract checker is unavailable'
  [[ -x "$REGRESSION_SCRIPT" ]] || fail 'RAG regression script is unavailable'
  [[ -n "${RAG_SERVICE_IMAGE:-}" ]] || fail 'RAG_SERVICE_IMAGE is required'
  [[ -n "${RAG_SOURCE_SHA:-}" ]] || fail 'RAG_SOURCE_SHA is required'
  [[ -n "${RAG_INFRA_SHA:-}" ]] || fail 'RAG_INFRA_SHA is required'
  is_immutable_rag_digest "$RAG_SERVICE_IMAGE" || fail 'RAG_SERVICE_IMAGE must be a local Nexus immutable digest reference'
  is_full_sha "$RAG_SOURCE_SHA" || fail 'RAG_SOURCE_SHA must be a full lowercase Git SHA'
  is_full_sha "$RAG_INFRA_SHA" || fail 'RAG_INFRA_SHA must be a full lowercase Git SHA'
  is_positive_integer "$RETRIES" || fail 'RAG_RELEASE_RETRIES must be a positive integer'
  is_nonnegative_integer "$RETRY_DELAY_SECONDS" || fail 'RAG_RELEASE_RETRY_DELAY_SECONDS must be a non-negative integer'
  [[ "$BACKUP_ROOT" == /* && "$BACKUP_ROOT" != / ]] || fail 'RAG_STATE_BACKUP_ROOT must be an absolute non-root path'
}

release_lock() {
  if (( LOCK_HELD != 0 )); then
    exec 9>&-
    LOCK_HELD=0
  fi
}

close_config_fds() {
  if [[ "$BASELINE_CONFIG_FD" =~ ^[0-9]+$ ]]; then
    exec {BASELINE_CONFIG_FD}>&-
    BASELINE_CONFIG_FD=''
  fi
  if [[ "$TARGET_CONFIG_FD" =~ ^[0-9]+$ ]]; then
    exec {TARGET_CONFIG_FD}>&-
    TARGET_CONFIG_FD=''
  fi
}

resume_writers_best_effort() {
  if (( RAG_STOPPED_BY_RELEASE != 0 )); then
    if docker_local start "$RAG_CONTAINER" >/dev/null 2>&1; then
      RAG_STOPPED_BY_RELEASE=0
    else
      printf 'FAIL: cleanup could not restart the baseline RAG container\n' >&2
    fi
  fi
  if (( FLEET_PAUSED_BY_RELEASE != 0 )); then
    if docker_local unpause "$FLEET_CONTAINER" >/dev/null 2>&1; then
      FLEET_PAUSED_BY_RELEASE=0
    else
      printf 'FAIL: cleanup could not resume the Fleet writer\n' >&2
    fi
  fi
}

cleanup() {
  if (( MUTATION_STARTED == 0 )); then
    resume_writers_best_effort
  fi
  close_config_fds
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
  local baseline_path target_path
  umask 077
  WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/vecta-rag-release.XXXXXX")"
  baseline_path="$(mktemp "${WORK_DIR}/baseline.XXXXXX")"
  target_path="$(mktemp "${WORK_DIR}/target.XXXXXX")"
  exec {BASELINE_CONFIG_FD}<>"$baseline_path"
  exec {TARGET_CONFIG_FD}<>"$target_path"
  rm -f -- "$baseline_path" "$target_path"
  BASELINE_CONFIG="/proc/$$/fd/${BASELINE_CONFIG_FD}"
  TARGET_CONFIG="/proc/$$/fd/${TARGET_CONFIG_FD}"
  BASELINE_RUNTIME="${WORK_DIR}/baseline-runtime.json"
}

render_config() {
  local image="$1" output="$2" include_endpoint="$3" base_dir
  local compose_files=(-f "$BASE_COMPOSE" -f "$IMAGE_OVERRIDE")
  base_dir="$(dirname "$BASE_COMPOSE")"
  if [[ "$include_endpoint" == 'true' ]]; then
    compose_files+=(-f "$ENDPOINT_OVERRIDE")
  fi
  if ! /usr/bin/env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_CONFIG \
    RAG_SERVICE_IMAGE="$image" "$DOCKER_BIN" compose \
      --project-directory "$base_dir" \
      "${compose_files[@]}" config --format json \
      >"$output" 2>/dev/null; then
    fail 'failed to render the RAG Compose release contract'
    return 1
  fi
}

result_field() {
  local result="$1" key="$2"
  printf '%s\n' "$result" | sed -n "s/^${key}=//p" | head -n 1
}

capture_baseline() {
  local result
  result="$(checker --mode baseline --container "$RAG_CONTAINER")" || return 1
  BASELINE_IMAGE="$(result_field "$result" IMAGE_REF)"
  BASELINE_IMAGE_ID="$(result_field "$result" IMAGE_ID)"
  is_immutable_rag_digest "$BASELINE_IMAGE" || fail 'running RAG image has no immutable rollback digest'
  [[ "$BASELINE_IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ ]] || fail 'running RAG image has no immutable image ID'
}

verify_target_provenance() {
  local result
  result="$(checker --mode provenance --container "$RAG_CONTAINER" \
    --image "$RAG_SERVICE_IMAGE" --source-sha "$RAG_SOURCE_SHA")" || return 1
  TARGET_IMAGE_ID="$(result_field "$result" IMAGE_ID)"
  [[ "$TARGET_IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ ]] || fail 'target RAG image has no immutable image ID'
}

capture_state_paths() {
  local result
  result="$(checker --mode state-paths --container "$RAG_CONTAINER" \
    --fleet-container "$FLEET_CONTAINER")" || return 1
  CACHE_VOLUME="$(result_field "$result" CACHE_VOLUME)"
  KNOWLEDGE_PATH="$(result_field "$result" KNOWLEDGE_PATH)"
  [[ "$CACHE_VOLUME" =~ ^[A-Za-z0-9_.-]+$ ]] \
    || { fail 'RAG cache volume name is unsafe'; return 1; }
  [[ "$KNOWLEDGE_PATH" == /* && "$KNOWLEDGE_PATH" != / ]] \
    || { fail 'RAG knowledge path is unsafe'; return 1; }
  [[ -d "$KNOWLEDGE_PATH" ]] || { fail 'RAG knowledge path is unavailable'; return 1; }
  CACHE_MOUNTPOINT="$(docker_local volume inspect --format '{{.Mountpoint}}' "$CACHE_VOLUME")" || {
    fail 'RAG cache volume is unavailable'; return 1;
  }
  [[ "$CACHE_MOUNTPOINT" == /* && "$CACHE_MOUNTPOINT" != / && -d "$CACHE_MOUNTPOINT" ]] \
    || { fail 'RAG cache mountpoint is unsafe or unavailable'; return 1; }
  validate_state_path_separation || return 1
}

paths_overlap() {
  local left="$1" right="$2"
  [[ "$left" == "$right" || "$left" == "$right/"* || "$right" == "$left/"* ]]
}

validate_state_path_separation() {
  local backup cache knowledge
  backup="$($REALPATH_BIN -m -- "$BACKUP_ROOT")" || return 1
  cache="$($REALPATH_BIN -m -- "$CACHE_MOUNTPOINT")" || return 1
  knowledge="$($REALPATH_BIN -m -- "$KNOWLEDGE_PATH")" || return 1
  [[ "$backup" != / && "$cache" != / && "$knowledge" != / ]] \
    || { fail 'RAG state paths must not resolve to the filesystem root'; return 1; }
  ! paths_overlap "$cache" "$knowledge" \
    || { fail 'RAG cache and knowledge paths must not overlap'; return 1; }
  ! paths_overlap "$backup" "$cache" \
    || { fail 'RAG backup root must not overlap the cache path'; return 1; }
  ! paths_overlap "$backup" "$knowledge" \
    || { fail 'RAG backup root must not overlap the knowledge path'; return 1; }
  BACKUP_ROOT="$backup"
  CACHE_MOUNTPOINT="$cache"
  KNOWLEDGE_PATH="$knowledge"
}

checker_match() {
  local config="$1" endpoint="${2:-}"
  local args=(--mode match --container "$RAG_CONTAINER" --target "$config")
  [[ -n "$endpoint" ]] && args+=(--endpoint "$endpoint")
  checker "${args[@]}"
}

checker_baseline_match() {
  local config="$1" endpoint="${2:-}"
  local args=(--mode match --container "$RAG_CONTAINER" --target "$config" \
    --allow-equivalent-image-reference true)
  [[ -n "$endpoint" ]] && args+=(--endpoint "$endpoint")
  checker "${args[@]}"
}

checker_transition() {
  checker --mode transition --container "$RAG_CONTAINER" \
    --baseline "$BASELINE_CONFIG" --target "$TARGET_CONFIG" \
    --endpoint "$EXPECTED_HF_ENDPOINT"
}

checker_snapshot() {
  checker --mode snapshot --container "$RAG_CONTAINER" --output "$BASELINE_RUNTIME"
}

checker_preserve() {
  checker --mode preserve --container "$RAG_CONTAINER" \
    --baseline-runtime "$BASELINE_RUNTIME"
}

prepare_contract() {
  verify_target_provenance || return 1
  capture_baseline || return 1
  render_config "$BASELINE_IMAGE" "$BASELINE_CONFIG" false || return 1
  render_config "$RAG_SERVICE_IMAGE" "$TARGET_CONFIG" true || return 1
  if checker_match "$TARGET_CONFIG" "$EXPECTED_HF_ENDPOINT" >/dev/null 2>&1; then
    printf 'RESULT=noop\n'
    return 10
  fi
  if ! checker_baseline_match "$BASELINE_CONFIG" >/dev/null 2>&1; then
    render_config "$BASELINE_IMAGE" "$BASELINE_CONFIG" true || return 1
    checker_baseline_match "$BASELINE_CONFIG" "$EXPECTED_HF_ENDPOINT" >/dev/null || return 1
  fi
  checker_transition || return 1
  checker_snapshot || return 1
  capture_state_paths || return 1
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

ensure_backup_capacity() {
  local cache_kb knowledge_kb available_kb required_kb
  mkdir -p -- "$BACKUP_ROOT"
  cache_kb="$("$DU_BIN" -sk "$CACHE_MOUNTPOINT" | awk '{print $1}')"
  knowledge_kb="$("$DU_BIN" -sk "$KNOWLEDGE_PATH" | awk '{print $1}')"
  available_kb="$("$DF_BIN" -Pk "$BACKUP_ROOT" | awk 'NR == 2 {print $4}')"
  [[ "$cache_kb" =~ ^[0-9]+$ && "$knowledge_kb" =~ ^[0-9]+$ && "$available_kb" =~ ^[0-9]+$ ]] \
    || fail 'RAG state backup capacity could not be measured'
  required_kb=$((cache_kb + knowledge_kb + 1024))
  (( available_kb >= required_kb )) || fail 'insufficient disk capacity for an exact RAG state backup'
}

write_evidence() {
  local key="$1" value="$2"
  [[ -n "$BACKUP_DIR" ]] || return 0
  printf '%s=%s\n' "$key" "$value" >> "${BACKUP_DIR}/release-evidence.env"
  chmod 600 "${BACKUP_DIR}/release-evidence.env"
}

quiesce_writers() {
  local fleet_state
  fleet_state="$(docker_local inspect --format '{{.State.Running}}:{{.State.Paused}}' "$FLEET_CONTAINER")" \
    || { fail 'Fleet writer state is unavailable'; return 1; }
  [[ "$fleet_state" == 'true:false' ]] \
    || { fail 'Fleet writer must be running and unpaused before the RAG release'; return 1; }
  docker_local pause "$FLEET_CONTAINER" >/dev/null
  FLEET_PAUSED_BY_RELEASE=1
  docker_local stop --time 30 "$RAG_CONTAINER" >/dev/null
  RAG_STOPPED_BY_RELEASE=1
}

resume_fleet_writer() {
  if (( FLEET_PAUSED_BY_RELEASE != 0 )); then
    docker_local unpause "$FLEET_CONTAINER" >/dev/null
    FLEET_PAUSED_BY_RELEASE=0
  fi
}

backup_state() {
  ensure_backup_capacity || return 1
  umask 077
  BACKUP_DIR="$(mktemp -d "${BACKUP_ROOT}/rag-service-immutable-release.XXXXXX")"
  chmod 700 "$BACKUP_DIR"
  "$TAR_BIN" --xattrs --acls --numeric-owner -C "$CACHE_MOUNTPOINT" \
    -czf "${BACKUP_DIR}/rag-model-cache.tgz" .
  "$TAR_BIN" --xattrs --acls --numeric-owner -C "$KNOWLEDGE_PATH" \
    -czf "${BACKUP_DIR}/rag-knowledge.tgz" .
  cp -- "$BASELINE_RUNTIME" "${BACKUP_DIR}/baseline-runtime.json"
  chmod 600 "${BACKUP_DIR}/baseline-runtime.json"
  {
    printf 'cache_metadata=%s\n' "$("$STAT_BIN" -c '%a:%u:%g' "$CACHE_MOUNTPOINT")"
    printf 'knowledge_metadata=%s\n' "$("$STAT_BIN" -c '%a:%u:%g' "$KNOWLEDGE_PATH")"
  } > "${BACKUP_DIR}/state-metadata.env"
  chmod 600 "${BACKUP_DIR}/state-metadata.env"
  "$SHA256SUM_BIN" "${BACKUP_DIR}/rag-model-cache.tgz" \
    "${BACKUP_DIR}/rag-knowledge.tgz" \
    "${BACKUP_DIR}/baseline-runtime.json" \
    "${BACKUP_DIR}/state-metadata.env" > "${BACKUP_DIR}/checksums.sha256"
  (
    cd "$BACKUP_DIR"
    "$SHA256SUM_BIN" -c checksums.sha256 >/dev/null
  )
  {
    printf 'release_id=%s\n' "$RELEASE_ID"
    printf 'infra_sha=%s\n' "$RAG_INFRA_SHA"
    printf 'source_sha=%s\n' "$RAG_SOURCE_SHA"
    printf 'baseline_image=%s\n' "$BASELINE_IMAGE"
    printf 'baseline_image_id=%s\n' "$BASELINE_IMAGE_ID"
    printf 'target_image=%s\n' "$RAG_SERVICE_IMAGE"
    printf 'target_image_id=%s\n' "$TARGET_IMAGE_ID"
    printf 'baseline_runtime_snapshot=verified\n'
    printf 'state_backup=verified\n'
  } > "${BACKUP_DIR}/release-evidence.env"
  chmod 600 "${BACKUP_DIR}/release-evidence.env"
  printf 'EVIDENCE_DIR=%s\n' "$BACKUP_DIR"
}

metadata_value() {
  local key="$1"
  sed -n "s/^${key}=//p" "${BACKUP_DIR}/state-metadata.env" | head -n 1
}

restore_one_state() {
  local path="$1" archive="$2" metadata_key="$3" metadata failed_archive mode uid gid
  local archive_entries live_entries
  metadata="$(metadata_value "$metadata_key")"
  IFS=: read -r mode uid gid <<< "$metadata"
  [[ "$mode" =~ ^[0-7]{3,4}$ && "$uid" =~ ^[0-9]+$ && "$gid" =~ ^[0-9]+$ ]] \
    || { fail 'RAG state metadata is invalid'; return 1; }
  [[ -d "$path" ]] || { fail 'RAG mutable state path is unavailable for rollback'; return 1; }
  failed_archive="${BACKUP_DIR}/${metadata_key}-failed-${RELEASE_ID}.tgz"
  [[ ! -e "$failed_archive" ]] || { fail 'RAG rollback preservation archive already exists'; return 1; }
  "$TAR_BIN" --xattrs --acls --numeric-owner -C "$path" -czf "$failed_archive" . || return 1
  "$SHA256SUM_BIN" "$failed_archive" > "${failed_archive}.sha256" || return 1
  "$FIND_BIN" "$path" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + || return 1
  chmod "$mode" "$path" || return 1
  chown "$uid:$gid" "$path" || return 1
  "$TAR_BIN" --xattrs --acls --numeric-owner -C "$path" -xzf "$archive" || return 1
  chmod "$mode" "$path" || return 1
  chown "$uid:$gid" "$path" || return 1
  "$TAR_BIN" --xattrs --acls --numeric-owner -C "$path" -dzf "$archive" >/dev/null || return 1
  archive_entries="$($TAR_BIN -tzf "$archive" | wc -l | tr -d '[:space:]')" || return 1
  live_entries="$($FIND_BIN "$path" -mindepth 1 -printf '.' | wc -c | tr -d '[:space:]')" || return 1
  [[ "$archive_entries" =~ ^[0-9]+$ && "$live_entries" =~ ^[0-9]+$ \
    && "$archive_entries" -eq $((live_entries + 1)) ]] \
    || { fail 'RAG restored state contains missing or extra entries'; return 1; }
  write_evidence "${metadata_key}_failed_archive" "$failed_archive"
}

restore_state() {
  [[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]] || fail 'RAG state backup is unavailable for rollback'
  (
    cd "$BACKUP_DIR"
    "$SHA256SUM_BIN" -c checksums.sha256 >/dev/null
  ) || return 1
  restore_one_state "$CACHE_MOUNTPOINT" "${BACKUP_DIR}/rag-model-cache.tgz" cache_metadata || return 1
  restore_one_state "$KNOWLEDGE_PATH" "${BACKUP_DIR}/rag-knowledge.tgz" knowledge_metadata || return 1
  write_evidence state_restore verified
}

compose_recreate() {
  local config="$1" base_dir
  base_dir="$(dirname "$BASE_COMPOSE")"
  docker_local compose --project-directory "$base_dir" -f "$config" \
    up -d --no-deps --no-build --pull never --force-recreate "$RAG_SERVICE"
}

remove_target_container() {
  local inspect_output inspect_status
  inspect_output="$(docker_local container inspect "$RAG_CONTAINER" 2>&1)"
  inspect_status=$?
  if (( inspect_status == 0 )); then
    docker_local rm -f "$RAG_CONTAINER" >/dev/null
    return
  fi
  if printf '%s' "$inspect_output" | grep -q 'No such container'; then
    return 0
  fi
  fail 'cannot determine whether the target RAG container is stopped'
  return "$inspect_status"
}

rollback() {
  local original_status="$1" remove_status restore_status recreate_status contract_status preserve_status health_status resume_status regression_status
  trap - ERR INT TERM
  set +e
  if (( MUTATION_STARTED == 0 )); then
    exit "$original_status"
  fi
  remove_target_container
  remove_status=$?
  if (( remove_status == 0 )); then
    restore_state
    restore_status=$?
  else
    restore_status=1
  fi
  if (( remove_status == 0 && restore_status == 0 )); then
    compose_recreate "$BASELINE_CONFIG"
    recreate_status=$?
    (( recreate_status == 0 )) && RAG_STOPPED_BY_RELEASE=0
    checker_match "$BASELINE_CONFIG"
    contract_status=$?
    checker_preserve
    preserve_status=$?
    check_rag_health
    health_status=$?
  else
    recreate_status=1
    contract_status=1
    preserve_status=1
    health_status=1
  fi
  if (( remove_status != 0 || restore_status != 0 || recreate_status != 0 || contract_status != 0 || preserve_status != 0 || health_status != 0 )); then
    write_evidence rollback failed
    printf 'FAIL: release failed and exact baseline was not proven\n' >&2
    exit 1
  fi
  MUTATION_STARTED=0
  resume_fleet_writer
  resume_status=$?
  run_regression after "$BASELINE_IMAGE"
  regression_status=$?
  if (( resume_status != 0 || regression_status != 0 )); then
    write_evidence rollback_state restored
    write_evidence rollback_runtime degraded
    write_evidence result rollback_needs_operator
    printf 'FAIL: release failed; baseline state was restored but runtime recovery needs an operator\n' >&2
    exit 1
  fi
  write_evidence rollback_state restored
  write_evidence rollback_runtime verified
  write_evidence rollback_health verified
  write_evidence rollback_regression verified
  write_evidence result rollback_restored
  printf 'FAIL: release failed; exact baseline restored\n' >&2
  exit "$original_status"
}

handle_unexpected_failure() {
  local status="$1"
  trap - ERR INT TERM
  if (( MUTATION_STARTED != 0 )); then
    rollback "$status"
  fi
  exit "$status"
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
    run_regression after "$RAG_SERVICE_IMAGE"
    return 0
  fi
  (( result == 0 )) || return "$result"
  check_rag_health
  run_regression before
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
    run_regression after "$RAG_SERVICE_IMAGE"
    printf 'RESULT=noop\n'
    return 0
  fi
  (( result == 0 )) || return "$result"
  check_rag_health
  run_regression before
  quiesce_writers
  backup_state
  write_evidence pre_health verified
  write_evidence pre_regression verified

  trap 'rollback 130' INT TERM
  MUTATION_STARTED=1
  if ! compose_recreate "$TARGET_CONFIG"; then
    rollback 1
  fi
  RAG_STOPPED_BY_RELEASE=0
  if ! checker_match "$TARGET_CONFIG" "$EXPECTED_HF_ENDPOINT"; then
    rollback 1
  fi
  if ! checker_preserve; then
    rollback 1
  fi
  if ! check_rag_health; then
    rollback 1
  fi
  write_evidence post_health verified
  write_evidence runtime_contract verified
  write_evidence transaction_commit target_validated
  MUTATION_STARTED=0
  if ! resume_fleet_writer; then
    write_evidence result post_commit_fleet_resume_failed
    fail 'target RAG was committed but Fleet could not resume; old state was not restored'
    return 1
  fi
  if ! run_regression after "$RAG_SERVICE_IMAGE"; then
    write_evidence result post_commit_regression_failed
    fail 'target RAG was committed but the post-commit regression failed; old state was not restored'
    return 1
  fi
  trap - INT TERM
  write_evidence post_regression verified
  write_evidence result changed
  printf 'RESULT=changed\n'
}

[[ "$#" == 1 ]] || usage
trap cleanup EXIT
trap 'handle_unexpected_failure $?' ERR
case "$1" in
  --check) run_check ;;
  --execute) run_execute ;;
  *) usage ;;
esac
