#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROXY_TEMPLATE="${ROOT_DIR}/roles/open-webui/templates/nginx.conf.j2"
HOST_TEMPLATE="${ROOT_DIR}/roles/open-webui/templates/host-nginx-site.conf.j2"
TRANSACTION="${ROOT_DIR}/scripts/reconcile-open-webui-admin-network.sh"
PLAYBOOK="${ROOT_DIR}/playbooks/mypc-network-reconcile.yml"
RUNBOOK="${ROOT_DIR}/docs/runbooks/mypc-data-structure-compatibility.md"
INVENTORY="${ROOT_DIR}/inventories/mypc/group_vars/mypc.yml"
BASE_SHA="${BASE_SHA:-7f6bdcd29d74d8dab80ca1ab17ab63b444fdb0ee}"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

require_file() {
  [ -f "$1" ] || fail "missing file: $1"
}

require_literal() {
  local file="$1"
  local literal="$2"
  grep -Fq -- "$literal" "$file" || fail "${file} missing literal: ${literal}"
}

require_absent() {
  local file="$1"
  local literal="$2"
  if grep -Fq -- "$literal" "$file"; then
    fail "${file} contains forbidden literal: ${literal}"
  fi
}

cd "$ROOT_DIR"
for file in "$PROXY_TEMPLATE" "$HOST_TEMPLATE" "$TRANSACTION" "$PLAYBOOK" "$RUNBOOK" "$INVENTORY"; do
  require_file "$file"
done

# Existing browser ingress contract.
for literal in \
  'location = /admin {' \
  'return 301 /admin/;' \
  'location ^~ /admin/ {'; do
  require_literal "$HOST_TEMPLATE" "$literal"
  require_literal "$PROXY_TEMPLATE" "${literal/301/302}"
done
require_literal "$HOST_TEMPLATE" 'proxy_pass {{ host_nginx_webui_proxy_url }};'
require_literal "$PROXY_TEMPLATE" 'rewrite ^/admin/(.*)$ /$1 break;'
require_literal "$PROXY_TEMPLATE" 'proxy_pass http://$admin_console:5173;'
require_literal "$PROXY_TEMPLATE" 'location ^~ /personal/ {'
require_literal "$PROXY_TEMPLATE" 'proxy_pass http://$fleet_gateway:3000;'
for template in "$PROXY_TEMPLATE" "$HOST_TEMPLATE"; do
  [ "$(grep -Fc 'location = /admin {' "$template")" = 1 ] ||
    fail "${template} must contain exactly one /admin location"
done

# The transaction is the only Docker state machine; its self-test is Docker-free.
bash -n "$TRANSACTION"
self_test_output="$("$TRANSACTION" --self-test)" || fail 'transaction self-test failed'
[ "$self_test_output" = 'RESULT=noop' ] || fail 'transaction self-test did not emit the safe noop marker'
require_literal "$TRANSACTION" 'set -euo pipefail'
for literal in \
  "readonly EXPECTED_HOSTNAME='mypc'" \
  "readonly PROXY_CONTAINER='openclaw-webui-proxy'" \
  "readonly ADMIN_CONTAINER='openclaw-admin-console'" \
  "readonly CORE_NETWORK='openclaw-enterprise_openclaw-net'" \
  "readonly WEBUI_NETWORK='openclaw-enterprise_open-webui-net'" \
  'MYPC_DEPLOY_ENABLED:-' \
  'MYPC_NETWORK_RECONCILE_APPROVAL:-'; do
  require_literal "$TRANSACTION" "$literal"
done
for literal in --check --execute --self-test; do
  require_literal "$TRANSACTION" "$literal"
done
for literal in \
  'state_is_intermediate()' \
  'rollback_plan()' \
  'rollback_refresh || return 1' \
  'state_is_temporary "$CURRENT_PROXY_NETWORKS" "$CURRENT_ADMIN_NETWORKS"' \
  'state_is_intermediate "$CURRENT_PROXY_NETWORKS" "$CURRENT_ADMIN_NETWORKS"' \
  'state_is_canonical "$CURRENT_PROXY_NETWORKS" "$CURRENT_ADMIN_NETWORKS"' \
  'state_matches_baseline || ROLLBACK_FAILED=1' \
  'RESULT=changed' \
  'RESULT=noop'; do
  require_literal "$TRANSACTION" "$literal"
done
require_absent "$TRANSACTION" '.Config'
require_absent "$TRANSACTION" 'docker compose'
require_absent "$TRANSACTION" 'docker-compose'
if grep -Eiq -- '(\{\{[[:space:]]*json|--format=[^[:space:]]*json|\{\{[[:space:]]*\.[[:space:]]*\}\})' "$TRANSACTION"; then
  fail 'transaction contains a JSON or full-object Go template'
fi
container_format='--format='\''{{.Id}}{{range $name, $value := .NetworkSettings.Networks}}{{printf "\n%s" $name}}{{end}}'\'''
network_format="--format='{{.Id}}'"
require_literal "$TRANSACTION" "$container_format"
require_literal "$TRANSACTION" "$network_format"
if grep -F -- '--format=' "$TRANSACTION" |
  grep -vF -- "$container_format" |
  grep -vF -- "$network_format" >/dev/null; then
  fail 'transaction contains an unapproved Docker inspect format'
fi
if grep -F 'docker inspect' "$TRANSACTION" | grep -vF -- '--format=' >/dev/null; then
  fail 'transaction contains an unformatted docker inspect'
fi
if grep -F 'docker network inspect' "$TRANSACTION" | grep -vF -- '--format=' >/dev/null; then
  fail 'transaction contains an unformatted docker network inspect'
fi
if grep -F 'docker network' "$TRANSACTION" | grep -Ev 'docker network (inspect|connect|disconnect)' >/dev/null; then
  fail 'transaction contains an unapproved docker network operation'
fi
if grep -Eiq \
  'docker[[:space:]]+(restart|recreate|pull|build|create|start|stop|rm|run|exec|ps|compose)|docker-compose|docker[[:space:]]+(image|volume|system|container)[[:space:]]|(^|[[:space:]])(bash|sh)[[:space:]]+-c|(^|[[:space:]])eval([[:space:]]|$)' \
  "$TRANSACTION"; then
  fail 'transaction contains a forbidden lifecycle, nested-shell, or Docker path'
fi

# Thin Ansible wrapper: no command construction, Docker parsing, roles, or deploy wiring.
require_literal "$PLAYBOOK" 'ansible.builtin.script:'
require_literal "$PLAYBOOK" 'cmd: ../scripts/reconcile-open-webui-admin-network.sh --check'
require_literal "$PLAYBOOK" 'cmd: ../scripts/reconcile-open-webui-admin-network.sh --execute'
require_literal "$PLAYBOOK" 'check_mode: false'
require_literal "$PLAYBOOK" 'when: not ansible_check_mode'
require_literal "$PLAYBOOK" 'MYPC_DEPLOY_ENABLED:'
require_literal "$PLAYBOOK" 'MYPC_NETWORK_RECONCILE_APPROVAL:'
if grep -Eiq \
  'ansible\.builtin\.(command|shell|raw)|community\.docker|docker[[:space:]]+(inspect|network)|^[[:space:]]*(roles|import_playbook|import_role|include_role):|cmd:.*\{\{' \
  "$PLAYBOOK"; then
  fail 'playbook is not a thin literal script wrapper'
fi

require_absent "$INVENTORY" 'admin_console_join_open_webui_network'

# Exact base scope excludes roles, workflows, deployment, and CI changes.
git rev-parse --verify "${BASE_SHA}^{commit}" >/dev/null 2>&1 || fail "base commit is unavailable: ${BASE_SHA}"
actual_scope="$({ git diff --name-only "$BASE_SHA" --; git diff --cached --name-only --; git ls-files --others --exclude-standard; } | sort -u)"
expected_scope="$(printf '%s\n' \
  docs/runbooks/mypc-data-structure-compatibility.md \
  inventories/mypc/group_vars/mypc.yml \
  playbooks/mypc-network-reconcile.yml \
  scripts/check-open-webui-admin-ingress.sh \
  scripts/reconcile-open-webui-admin-network.sh)"
[ "$actual_scope" = "$expected_scope" ] || fail 'diff scope is not exactly the authorized five files'
if printf '%s\n' "$actual_scope" | grep -Eq '(^|/)(roles|\.github/workflows)(/|$)'; then
  fail 'role or CI wiring changed'
fi

# Runbook keeps operator evidence separate from transaction guarantees.
for literal in \
  'temporary' \
  'canonical' \
  'ID drift' \
  'fail-closed' \
  '仅预检' \
  '不是成功证据' \
  'uvx --from ansible-core ansible-playbook playbooks/mypc-network-reconcile.yml -i inventories/mypc/hosts.ini --check -e mypc_deploy_enabled=true -e mypc_network_reconcile_approval=true' \
  'uvx --from ansible-core ansible-playbook playbooks/mypc-network-reconcile.yml -i inventories/mypc/hosts.ini -e mypc_deploy_enabled=true -e mypc_network_reconcile_approval=true' \
  'docker network connect openclaw-enterprise_open-webui-net openclaw-admin-console' \
  'docker network disconnect openclaw-enterprise_openclaw-net openclaw-webui-proxy' \
  'that actor remains unknown'; do
  require_literal "$RUNBOOK" "$literal"
done
connect_line="$(grep -nF 'docker network connect openclaw-enterprise_open-webui-net openclaw-admin-console' "$RUNBOOK" | tail -n1 | cut -d: -f1)"
disconnect_line="$(grep -nF 'docker network disconnect openclaw-enterprise_openclaw-net openclaw-webui-proxy' "$RUNBOOK" | tail -n1 | cut -d: -f1)"
[ "$connect_line" -lt "$disconnect_line" ] || fail 'manual rollback order is not Admin then Proxy'

run_fake_transaction_probe() {
  local probe_dir probe_bin state_file mutation_log fake_output_file output status actual_state actual_log
  local probe_core_network='openclaw-enterprise_openclaw-net'
  local probe_webui_network='openclaw-enterprise_open-webui-net'
  local probe_proxy_networks="$probe_webui_network"
  local probe_admin_networks="$probe_webui_network,$probe_core_network"

  probe_dir="$(mktemp -d "${TMPDIR:-/tmp}/open-webui-admin-ingress.XXXXXX")"
  probe_bin="$probe_dir/bin"
  state_file="$probe_dir/state"
  mutation_log="$probe_dir/mutations"
  fake_output_file="$probe_dir/output"
  mkdir "$probe_bin"
  trap 'rm -rf "$probe_dir"' EXIT

  cat > "$probe_bin/hostname" <<'EOF'
#!/bin/sh
printf 'mypc\n'
EOF
  cat > "$probe_bin/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

: "${FAKE_STATE:?}"
: "${FAKE_LOG:?}"
: "${FAKE_MODE:?}"

readonly CORE_NETWORK='openclaw-enterprise_openclaw-net'
readonly WEBUI_NETWORK='openclaw-enterprise_open-webui-net'

fail() { exit 90; }
emit() { printf '%s\n' "$1" | tee -a "$FAKE_OUTPUT"; }
read_state() {
  IFS= read -r proxy_networks < "$FAKE_STATE"
  IFS= read -r admin_networks < <(sed -n '2p' "$FAKE_STATE")
}
write_state() { printf '%s\n%s\n' "$1" "$2" > "$FAKE_STATE"; }
record() { printf '%s %s %s\n' "$1" "$2" "$3" >> "$FAKE_LOG"; }
print_container() {
  emit "$1"
  printf '%s\n' "$2" | tr ',' '\n' | tee -a "$FAKE_OUTPUT"
}

case "${1:-}" in
  network)
    case "${2:-}" in
      inspect)
        [[ "${3:-}" == '--format={{.Id}}' ]] || fail
        case "${4:-}" in
          "$CORE_NETWORK") emit core-id ;;
          "$WEBUI_NETWORK") emit webui-id ;;
          *) fail ;;
        esac
        ;;
      connect)
        read_state
        case "${3:-}:${4:-}" in
          core-id:proxy-id)
            if [[ "$FAKE_MODE" == extra-network ]]; then
              write_state "$WEBUI_NETWORK,$CORE_NETWORK,unexpected" "$admin_networks"
            else
              write_state "$WEBUI_NETWORK,$CORE_NETWORK" "$admin_networks"
            fi
            record connect core-id proxy-id
            [[ "$FAKE_MODE" != connect-fail && "$FAKE_MODE" != extra-network ]] || exit 42
            ;;
          webui-id:admin-id)
            write_state "$proxy_networks" "$WEBUI_NETWORK,$CORE_NETWORK"
            record connect webui-id admin-id
            ;;
          *) fail ;;
        esac
        ;;
      disconnect)
        read_state
        case "${3:-}:${4:-}" in
          webui-id:admin-id)
            write_state "$proxy_networks" "$CORE_NETWORK"
            record disconnect webui-id admin-id
            [[ "$FAKE_MODE" != disconnect-fail ]] || exit 43
            ;;
          core-id:proxy-id)
            write_state "$WEBUI_NETWORK" "$admin_networks"
            record disconnect core-id proxy-id
            ;;
          *) fail ;;
        esac
        ;;
      *) fail ;;
    esac
    ;;
  inspect)
    [[ "${2:-}" == '--type=container' && "${3:-}" == --format=* ]] || fail
    read_state
    case "${4:-}" in
      openclaw-webui-proxy) print_container proxy-id "$proxy_networks" ;;
      openclaw-admin-console) print_container admin-id "$admin_networks" ;;
      *) fail ;;
    esac
    ;;
  *) fail ;;
esac
EOF
  chmod +x "$probe_bin/hostname" "$probe_bin/docker"

  reset_probe_case() {
    printf '%s\n%s\n' "$probe_proxy_networks" "$probe_admin_networks" > "$state_file"
    : > "$mutation_log"
    : > "$fake_output_file"
  }

  run_probe_case() {
    local label="$1" mode="$2" expected_state expected_log captured_fake_output probe_text
    reset_probe_case
    if output="$(PATH="$probe_bin:$PATH" FAKE_MODE="$mode" FAKE_STATE="$state_file" FAKE_LOG="$mutation_log" \
      FAKE_OUTPUT="$fake_output_file" \
      MYPC_DEPLOY_ENABLED=true MYPC_NETWORK_RECONCILE_APPROVAL=true "$TRANSACTION" --execute 2>&1)"; then
      status=0
    else
      status=$?
    fi
    captured_fake_output="$(<"$fake_output_file")"
    for probe_text in "$output" "$captured_fake_output"; do
      if printf '%s\n' "$probe_text" | grep -Eiq '(^|[^[:alnum:]_])(env|secret|config|networksettings|openai_api_key|webui_secret_key)([^[:alnum:]_]|$)'; then
        fail 'fake Docker output exposed environment, secrets, or full inspect data'
      fi
    done
    case "$mode" in
      success)
        [ "$status" = 0 ] || fail "fake Docker ${label} did not succeed"
        expected_state="${probe_webui_network},${probe_core_network}"$'\n'"$probe_core_network"
        expected_log=$'connect core-id proxy-id\ndisconnect webui-id admin-id'
        ;;
      connect-fail)
        [ "$status" != 0 ] || fail "fake Docker ${label} unexpectedly succeeded"
        expected_state="$probe_webui_network"$'\n'"${probe_webui_network},${probe_core_network}"
        expected_log=$'connect core-id proxy-id\ndisconnect core-id proxy-id'
        ;;
      disconnect-fail)
        [ "$status" != 0 ] || fail "fake Docker ${label} unexpectedly succeeded"
        expected_state="$probe_webui_network"$'\n'"${probe_webui_network},${probe_core_network}"
        expected_log=$'connect core-id proxy-id\ndisconnect webui-id admin-id\nconnect webui-id admin-id\ndisconnect core-id proxy-id'
        ;;
      extra-network)
        [ "$status" != 0 ] || fail "fake Docker ${label} unexpectedly succeeded"
        expected_state="${probe_webui_network},${probe_core_network},unexpected"$'\n'"${probe_webui_network},${probe_core_network}"
        expected_log='connect core-id proxy-id'
        ;;
      *) fail "unknown fake Docker probe mode: $mode" ;;
    esac
    actual_state="$(<"$state_file")"
    actual_log="$(<"$mutation_log")"
    [ "$actual_state" = "$expected_state" ] || fail "fake Docker ${label} ended in an unexpected state"
    [ "$actual_log" = "$expected_log" ] || fail "fake Docker ${label} recorded an unexpected mutation sequence"
  }

  run_probe_case success success
  run_probe_case connect-failure connect-fail
  run_probe_case disconnect-failure disconnect-fail
  run_probe_case unexpected-extra-network extra-network
  trap - EXIT
  rm -rf "$probe_dir"
}

run_fake_transaction_probe

printf 'OK   Open WebUI ingress and atomic network-reconcile contract is present\n'
