#!/usr/bin/env bash
set -euo pipefail

export LC_ALL=C

readonly EXPECTED_HOSTNAME='mypc'
readonly PROXY_CONTAINER='openclaw-webui-proxy'
readonly ADMIN_CONTAINER='openclaw-admin-console'
readonly CORE_NETWORK='openclaw-enterprise_openclaw-net'
readonly WEBUI_NETWORK='openclaw-enterprise_open-webui-net'
readonly TEMP_PROXY_NETWORKS="$WEBUI_NETWORK"
readonly TEMP_ADMIN_NETWORKS="$WEBUI_NETWORK,$CORE_NETWORK"
readonly CANONICAL_PROXY_NETWORKS="$WEBUI_NETWORK,$CORE_NETWORK"
readonly CANONICAL_ADMIN_NETWORKS="$CORE_NETWORK"

CURRENT_CORE_NETWORK_ID=''
CURRENT_WEBUI_NETWORK_ID=''
CURRENT_PROXY_CONTAINER_ID=''
CURRENT_ADMIN_CONTAINER_ID=''
CURRENT_PROXY_NETWORKS=''
CURRENT_ADMIN_NETWORKS=''
BASE_CORE_NETWORK_ID=''
BASE_WEBUI_NETWORK_ID=''
BASE_PROXY_CONTAINER_ID=''
BASE_ADMIN_CONTAINER_ID=''
BASE_PROXY_NETWORKS=''
BASE_ADMIN_NETWORKS=''
INSPECT_NETWORK_ID=''
INSPECT_CONTAINER_ID=''
INSPECT_NETWORKS=''
MUTATION_STARTED=0
ROLLBACK_FAILED=0

fail() { printf 'FAIL: %s\n' "$*" >&2; return 1; }
usage() { printf 'Usage: %s --check|--execute|--self-test\n' "$0" >&2; exit 2; }

normalize_networks() {
  local raw="$1" name result=''
  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    [[ -n "$result" ]] && result+=','
    result+="$name"
  done < <(printf '%s\n' "$raw" | sort -u)
  printf '%s' "$result"
}

network_has() { [[ ",${1}," == *",${2},"* ]]; }
state_is_temporary() { [[ "$1" == "$TEMP_PROXY_NETWORKS" && "$2" == "$TEMP_ADMIN_NETWORKS" ]]; }
state_is_canonical() { [[ "$1" == "$CANONICAL_PROXY_NETWORKS" && "$2" == "$CANONICAL_ADMIN_NETWORKS" ]]; }
should_reconnect_admin() { network_has "$1" "$WEBUI_NETWORK" && ! network_has "$2" "$WEBUI_NETWORK"; }
should_disconnect_proxy() { ! network_has "$1" "$CORE_NETWORK" && network_has "$2" "$CORE_NETWORK"; }

inspect_container() {
  local container="$1" output container_id network_names=''
  output="$(docker inspect --type=container --format='{{.Id}}{{range $name, $value := .NetworkSettings.Networks}}{{printf "\n%s" $name}}{{end}}' "$container" 2>/dev/null)" || return 1
  if [[ "$output" == *$'\n'* ]]; then
    container_id="${output%%$'\n'*}"
    network_names="${output#*$'\n'}"
  else
    container_id="$output"
  fi
  if [[ -z "$container_id" || "$container_id" == *[[:space:]]* ]]; then return 1; fi
  INSPECT_CONTAINER_ID="$container_id"
  INSPECT_NETWORKS="$(normalize_networks "$network_names")"
}

inspect_network() {
  local network="$1" output
  output="$(docker network inspect --format='{{.Id}}' "$network" 2>/dev/null)" || return 1
  if [[ -z "$output" || "$output" == *$'\n'* || "$output" == *[[:space:]]* ]]; then return 1; fi
  INSPECT_NETWORK_ID="$output"
}

inspect_state() {
  inspect_network "$CORE_NETWORK" || return 1
  CURRENT_CORE_NETWORK_ID="$INSPECT_NETWORK_ID"
  inspect_network "$WEBUI_NETWORK" || return 1
  CURRENT_WEBUI_NETWORK_ID="$INSPECT_NETWORK_ID"
  inspect_container "$PROXY_CONTAINER" || return 1
  CURRENT_PROXY_CONTAINER_ID="$INSPECT_CONTAINER_ID"
  CURRENT_PROXY_NETWORKS="$INSPECT_NETWORKS"
  inspect_container "$ADMIN_CONTAINER" || return 1
  CURRENT_ADMIN_CONTAINER_ID="$INSPECT_CONTAINER_ID"
  CURRENT_ADMIN_NETWORKS="$INSPECT_NETWORKS"
}

capture_baseline() {
  BASE_CORE_NETWORK_ID="$CURRENT_CORE_NETWORK_ID"; BASE_WEBUI_NETWORK_ID="$CURRENT_WEBUI_NETWORK_ID"
  BASE_PROXY_CONTAINER_ID="$CURRENT_PROXY_CONTAINER_ID"; BASE_ADMIN_CONTAINER_ID="$CURRENT_ADMIN_CONTAINER_ID"
  BASE_PROXY_NETWORKS="$CURRENT_PROXY_NETWORKS"; BASE_ADMIN_NETWORKS="$CURRENT_ADMIN_NETWORKS"
}

ids_match_baseline() {
  [[ "$CURRENT_CORE_NETWORK_ID" == "$BASE_CORE_NETWORK_ID" &&
    "$CURRENT_WEBUI_NETWORK_ID" == "$BASE_WEBUI_NETWORK_ID" &&
    "$CURRENT_PROXY_CONTAINER_ID" == "$BASE_PROXY_CONTAINER_ID" &&
    "$CURRENT_ADMIN_CONTAINER_ID" == "$BASE_ADMIN_CONTAINER_ID" ]]
}

state_matches_baseline() {
  ids_match_baseline && [[ "$CURRENT_PROXY_NETWORKS" == "$BASE_PROXY_NETWORKS" &&
    "$CURRENT_ADMIN_NETWORKS" == "$BASE_ADMIN_NETWORKS" ]]
}

assert_current_state() {
  inspect_state || { fail 'Docker inspection failed; refusing to mutate'; return 1; }
  ids_match_baseline || { fail 'Docker identity drifted; refusing to touch replacements'; return 1; }
  [[ "$CURRENT_PROXY_NETWORKS" == "$1" && "$CURRENT_ADMIN_NETWORKS" == "$2" ]] || {
    fail 'Docker network topology is not the exact expected transaction state'
    return 1
  }
}

require_hostname() {
  local actual
  actual="$(/bin/hostname 2>/dev/null)" || { fail 'unable to read the target hostname'; return 1; }
  [[ "$actual" == "$EXPECTED_HOSTNAME" ]] || { fail 'refusing reconciliation unless hostname is exactly mypc'; return 1; }
}

require_approvals() {
  [[ "${MYPC_DEPLOY_ENABLED:-}" == 'true' ]] || { fail 'refusing execute without MYPC_DEPLOY_ENABLED=true'; return 1; }
  [[ "${MYPC_NETWORK_RECONCILE_APPROVAL:-}" == 'true' ]] || { fail 'refusing execute without MYPC_NETWORK_RECONCILE_APPROVAL=true'; return 1; }
}

rollback_refresh() { inspect_state && ids_match_baseline; }

current_networks() {
  case "$1" in
    admin) printf '%s' "$CURRENT_ADMIN_NETWORKS" ;;
    proxy) printf '%s' "$CURRENT_PROXY_NETWORKS" ;;
    *) return 1 ;;
  esac
}

rollback_step() {
  local decision="$1" operation="$2" kind="$3" baseline_set="$4" network_id="$5" container_id="$6" current
  rollback_refresh || return 1
  current="$(current_networks "$kind")"
  "$decision" "$baseline_set" "$current" || return 0
  rollback_refresh || return 1
  current="$(current_networks "$kind")"
  if "$decision" "$baseline_set" "$current"; then
    case "$operation" in
      connect) docker network connect "$network_id" "$container_id" >/dev/null 2>&1; rc=$? ;;
      disconnect) docker network disconnect "$network_id" "$container_id" >/dev/null 2>&1; rc=$? ;;
      *) return 1 ;;
    esac
    (( rc == 0 )) || ROLLBACK_FAILED=1
    rollback_refresh || return 1
  fi
}

rollback() {
  local original_status="$1"
  trap - ERR INT TERM
  set +e
  if (( MUTATION_STARTED == 0 )); then
    [[ "$original_status" != 0 ]] || original_status=1
    exit "$original_status"
  fi

  ROLLBACK_FAILED=0
  rollback_step should_reconnect_admin connect admin "$BASE_ADMIN_NETWORKS" "$BASE_WEBUI_NETWORK_ID" "$BASE_ADMIN_CONTAINER_ID" || {
    printf 'FAIL: rollback stopped before or after reconnecting Admin\n' >&2
    exit 1
  }
  rollback_step should_disconnect_proxy disconnect proxy "$BASE_PROXY_NETWORKS" "$BASE_CORE_NETWORK_ID" "$BASE_PROXY_CONTAINER_ID" || {
    printf 'FAIL: rollback stopped before or after disconnecting Proxy\n' >&2
    exit 1
  }
  rollback_refresh || ROLLBACK_FAILED=1
  state_matches_baseline || ROLLBACK_FAILED=1
  if (( ROLLBACK_FAILED != 0 )); then
    printf 'FAIL: transaction failed and exact measured baseline was not proven\n' >&2
    exit 1
  fi
  printf 'FAIL: transaction failed; exact measured baseline restored\n' >&2
  [[ "$original_status" != 0 ]] || original_status=1
  exit "$original_status"
}

run_check() {
  require_hostname
  inspect_state || { fail 'Docker identity or network-set inspection failed'; return 1; }
  if state_is_canonical "$CURRENT_PROXY_NETWORKS" "$CURRENT_ADMIN_NETWORKS"; then
    printf 'OK: canonical network state; no mutation required\n'; return 0
  fi
  if state_is_temporary "$CURRENT_PROXY_NETWORKS" "$CURRENT_ADMIN_NETWORKS"; then
    printf 'OK: temporary network state is eligible for execute\n'; return 0
  fi
  fail 'refusing a starting topology other than exact temporary or canonical state'
}

run_execute() {
  require_hostname
  require_approvals
  inspect_state || { fail 'Docker identity or network-set inspection failed'; return 1; }
  capture_baseline
  if state_is_canonical "$BASE_PROXY_NETWORKS" "$BASE_ADMIN_NETWORKS"; then
    printf 'OK: canonical network state; no mutation required\n'; return 0
  fi
  state_is_temporary "$BASE_PROXY_NETWORKS" "$BASE_ADMIN_NETWORKS" || {
    fail 'refusing a starting topology other than exact temporary or canonical state'
    return 1
  }

  trap 'rollback "$?"' ERR
  trap 'rollback 130' INT TERM
  assert_current_state "$BASE_PROXY_NETWORKS" "$BASE_ADMIN_NETWORKS"
  # ponytail: Docker has no atomic inspect+connect; revalidate immediately before each mutation.
  MUTATION_STARTED=1
  docker network connect "$BASE_CORE_NETWORK_ID" "$BASE_PROXY_CONTAINER_ID" >/dev/null 2>&1
  assert_current_state "$CANONICAL_PROXY_NETWORKS" "$TEMP_ADMIN_NETWORKS"
  assert_current_state "$CANONICAL_PROXY_NETWORKS" "$TEMP_ADMIN_NETWORKS"
  docker network disconnect "$BASE_WEBUI_NETWORK_ID" "$BASE_ADMIN_CONTAINER_ID" >/dev/null 2>&1
  assert_current_state "$CANONICAL_PROXY_NETWORKS" "$CANONICAL_ADMIN_NETWORKS"
  trap - ERR INT TERM
  printf 'OK: temporary network state reconciled to canonical\n'
}

self_test_expect() {
  local expected="$1" label="$2" actual
  shift 2
  if "$@"; then actual=0; else actual=1; fi
  [[ "$actual" == "$expected" ]] || { fail "self-test mismatch: ${label}"; return 1; }
}

run_self_test() {
  self_test_expect 0 temporary-state state_is_temporary "$TEMP_PROXY_NETWORKS" "$TEMP_ADMIN_NETWORKS"
  self_test_expect 0 canonical-state state_is_canonical "$CANONICAL_PROXY_NETWORKS" "$CANONICAL_ADMIN_NETWORKS"
  self_test_expect 1 in-progress-state state_is_temporary "$CANONICAL_PROXY_NETWORKS" "$TEMP_ADMIN_NETWORKS"
  self_test_expect 1 extra-network state_is_temporary "${TEMP_PROXY_NETWORKS},unexpected" "$TEMP_ADMIN_NETWORKS"
  self_test_expect 1 missing-network state_is_canonical '' "$CANONICAL_ADMIN_NETWORKS"
  self_test_expect 0 reconnect-admin should_reconnect_admin "$TEMP_ADMIN_NETWORKS" "$CANONICAL_ADMIN_NETWORKS"
  self_test_expect 0 disconnect-proxy should_disconnect_proxy "$TEMP_PROXY_NETWORKS" "$CANONICAL_PROXY_NETWORKS"
  self_test_expect 1 reconnect-admin-noop should_reconnect_admin "$TEMP_ADMIN_NETWORKS" "$TEMP_ADMIN_NETWORKS"
  self_test_expect 1 disconnect-proxy-noop should_disconnect_proxy "$TEMP_PROXY_NETWORKS" "$TEMP_PROXY_NETWORKS"
  printf 'OK: transaction self-test\n'
}

[[ "$#" == 1 ]] || usage
case "$1" in
  --check) run_check ;;
  --execute) run_execute ;;
  --self-test) run_self_test ;;
  *) usage ;;
esac
