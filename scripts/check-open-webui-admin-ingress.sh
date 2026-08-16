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
[ "$(sed -n '1p' "$TRANSACTION")" = '#!/usr/bin/bash' ] ||
  fail 'transaction shebang must be exactly #!/usr/bin/bash'
self_test_output="$(bash "$TRANSACTION" --self-test)" || fail 'transaction self-test failed'
[ "$self_test_output" = 'RESULT=noop' ] || fail 'transaction self-test did not emit the safe noop marker'
require_literal "$TRANSACTION" 'set -euo pipefail'
for literal in \
  "readonly EXPECTED_HOSTNAME='mypc'" \
  "readonly DOCKER_BIN='/usr/bin/docker'" \
  "readonly HOSTNAME_BIN='/usr/bin/hostname'" \
  "export PATH='/usr/bin:/bin'" \
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
if grep -F '"$DOCKER_BIN" inspect' "$TRANSACTION" | grep -vF -- '--format=' >/dev/null; then
  fail 'transaction contains an unformatted docker inspect'
fi
if grep -F '"$DOCKER_BIN" network inspect' "$TRANSACTION" | grep -vF -- '--format=' >/dev/null; then
  fail 'transaction contains an unformatted docker network inspect'
fi
if grep -F '"$DOCKER_BIN" network' "$TRANSACTION" |
  grep -Ev '"\$DOCKER_BIN" network (inspect|connect|disconnect)' >/dev/null; then
  fail 'transaction contains an unapproved docker network operation'
fi
if grep -Eiq \
  '"\$DOCKER_BIN"[[:space:]]+(restart|recreate|pull|build|create|start|stop|rm|run|exec|ps|compose|image|volume|system|container)|docker-compose|(^|[[:space:]])(bash|sh)[[:space:]]+-c|(^|[[:space:]])eval([[:space:]]|$)' \
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
  '/usr/bin/docker network connect openclaw-enterprise_open-webui-net openclaw-admin-console' \
  '/usr/bin/docker inspect --type=container --format=' \
  '/usr/bin/docker network disconnect openclaw-enterprise_openclaw-net openclaw-webui-proxy' \
  'that actor remains unknown'; do
  require_literal "$RUNBOOK" "$literal"
done
if grep -nE '^[[:space:]]*docker[[:space:]]+(inspect|network)' "$RUNBOOK" >/dev/null; then
  fail 'runbook manual Docker commands must use /usr/bin/docker'
fi
connect_line="$(grep -nF '/usr/bin/docker network connect openclaw-enterprise_open-webui-net openclaw-admin-console' "$RUNBOOK" | tail -n1 | cut -d: -f1)"
disconnect_line="$(grep -nF '/usr/bin/docker network disconnect openclaw-enterprise_openclaw-net openclaw-webui-proxy' "$RUNBOOK" | tail -n1 | cut -d: -f1)"
[ "$connect_line" -lt "$disconnect_line" ] || fail 'manual rollback order is not Admin then Proxy'

run_fake_transaction_probe() {
  local probe_dir probe_bin path_bin test_transaction state_file mutation_log fake_output_file
  local inspect_count_file network_inspect_count_file path_resolution_log output status captured_fake_output probe_text
  local probe_core_network='openclaw-enterprise_openclaw-net'
  local probe_webui_network='openclaw-enterprise_open-webui-net'
  local probe_proxy_networks="$probe_webui_network"
  local probe_admin_networks="$probe_webui_network,$probe_core_network"
  local probe_canonical_proxy_networks="$probe_webui_network,$probe_core_network"
  local probe_canonical_admin_networks="$probe_core_network"

  probe_dir="$(mktemp -d "${TMPDIR:-/tmp}/open-webui-admin-ingress.XXXXXX")"
  probe_bin="$probe_dir/fake-bin"
  path_bin="$probe_dir/path-bin"
  test_transaction="$probe_dir/reconcile-open-webui-admin-network.sh"
  state_file="$probe_dir/state"
  mutation_log="$probe_dir/mutations"
  fake_output_file="$probe_dir/output"
  inspect_count_file="$probe_dir/inspect-count"
  network_inspect_count_file="$probe_dir/network-inspect-count"
  path_resolution_log="$probe_dir/path-resolution"
  mkdir "$probe_bin" "$path_bin"
  trap 'rm -rf "$probe_dir"' EXIT

  cat > "$probe_bin/hostname" <<'EOF'
#!/bin/sh
if [ "${FAKE_MODE:-}" = wrong-hostname ]; then
  printf 'not-mypc\n'
else
  printf 'mypc\n'
fi
EOF
  cat > "$probe_bin/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

: "${FAKE_STATE:?}"
: "${FAKE_LOG:?}"
: "${FAKE_MODE:?}"
: "${FAKE_OUTPUT:?}"
: "${FAKE_INSPECT_COUNT:?}"
: "${FAKE_NETWORK_INSPECT_COUNT:?}"

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
next_container_inspect() {
  local count
  count="$(<"$FAKE_INSPECT_COUNT")"
  count=$((count + 1))
  printf '%s\n' "$count" > "$FAKE_INSPECT_COUNT"
  printf '%s' "$count"
}
next_network_inspect() {
  local count
  count="$(<"$FAKE_NETWORK_INSPECT_COUNT")"
  count=$((count + 1))
  printf '%s\n' "$count" > "$FAKE_NETWORK_INSPECT_COUNT"
  printf '%s' "$count"
}
print_container() {
  emit "$1"
  printf '%s\n' "$2" | tr ',' '\n' | tee -a "$FAKE_OUTPUT"
}

case "${1:-}" in
  network)
    case "${2:-}" in
      inspect)
        [[ "${3:-}" == '--format={{.Id}}' ]] || fail
        network_inspect_count="$(next_network_inspect)"
        [[ "$FAKE_MODE" != inspect-fail || "${4:-}" != "$WEBUI_NETWORK" ]] || exit 91
        case "${4:-}" in
          "$CORE_NETWORK")
            if [[ "$FAKE_MODE" == core-network-id-drift-before-mutation && "$network_inspect_count" -ge 3 ]]; then
              emit drifted-core-id
            else
              emit core-id
            fi
            ;;
          "$WEBUI_NETWORK")
            if [[ "$FAKE_MODE" == post-first-mutation-webui-network-drift && -s "$FAKE_LOG" ]]; then
              emit drifted-webui-id
            else
              emit webui-id
            fi
            ;;
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
    container_inspect_count="$(next_container_inspect)"
    case "${4:-}" in
      openclaw-webui-proxy)
        if [[ "$FAKE_MODE" == id-drift && "$container_inspect_count" -ge 3 ]]; then
          print_container drifted-proxy-id "$proxy_networks"
        else
          print_container proxy-id "$proxy_networks"
        fi
        ;;
      openclaw-admin-console)
        if [[ "$FAKE_MODE" == admin-id-drift-before-mutation && "$container_inspect_count" -ge 4 ]]; then
          print_container drifted-admin-id "$admin_networks"
        else
          print_container admin-id "$admin_networks"
        fi
        ;;
      *) fail ;;
    esac
    ;;
  *) fail ;;
esac
EOF
  cat > "$path_bin/hostname" <<'EOF'
#!/bin/sh
printf 'hostname\n' >> "$FAKE_PATH_RESOLUTION_LOG"
exit 97
EOF
  cat > "$path_bin/docker" <<'EOF'
#!/bin/sh
printf 'docker\n' >> "$FAKE_PATH_RESOLUTION_LOG"
exit 97
EOF
  chmod +x "$probe_bin/hostname" "$probe_bin/docker" "$path_bin/hostname" "$path_bin/docker"

  cp "$TRANSACTION" "$test_transaction"
  sed \
    -e "s|^readonly DOCKER_BIN='/usr/bin/docker'$|readonly DOCKER_BIN='$probe_bin/docker'|" \
    -e "s|^readonly HOSTNAME_BIN='/usr/bin/hostname'$|readonly HOSTNAME_BIN='$probe_bin/hostname'|" \
    "$test_transaction" > "$probe_dir/transaction.replaced"
  mv "$probe_dir/transaction.replaced" "$test_transaction"
  chmod +x "$test_transaction"
  [ "$(grep -Fc -- "readonly DOCKER_BIN='$probe_bin/docker'" "$test_transaction")" = 1 ] ||
    fail 'temporary transaction did not replace the exact Docker constant'
  [ "$(grep -Fc -- "readonly HOSTNAME_BIN='$probe_bin/hostname'" "$test_transaction")" = 1 ] ||
    fail 'temporary transaction did not replace the exact hostname constant'
  [ "$(grep -Fc -- "readonly DOCKER_BIN='/usr/bin/docker'" "$test_transaction")" = 0 ] ||
    fail 'temporary transaction retained the production Docker constant'
  [ "$(grep -Fc -- "readonly HOSTNAME_BIN='/usr/bin/hostname'" "$test_transaction")" = 0 ] ||
    fail 'temporary transaction retained the production hostname constant'

  reset_probe_case() {
    printf '%s\n%s\n' "$1" "$2" > "$state_file"
    : > "$mutation_log"
    : > "$fake_output_file"
    printf '0\n' > "$inspect_count_file"
    printf '0\n' > "$network_inspect_count_file"
    : > "$path_resolution_log"
  }

  run_transaction_process() {
    local operation="$1" mode="$2" deploy_approval="$3" network_approval="$4"
    export PATH="$path_bin:/usr/bin:/bin"
    export FAKE_MODE="$mode" FAKE_STATE="$state_file" FAKE_LOG="$mutation_log"
    export FAKE_OUTPUT="$fake_output_file" FAKE_INSPECT_COUNT="$inspect_count_file"
    export FAKE_NETWORK_INSPECT_COUNT="$network_inspect_count_file"
    export FAKE_PATH_RESOLUTION_LOG="$path_resolution_log"
    if [[ "$deploy_approval" == '__unset__' ]]; then
      unset MYPC_DEPLOY_ENABLED
    else
      export MYPC_DEPLOY_ENABLED="$deploy_approval"
    fi
    if [[ "$network_approval" == '__unset__' ]]; then
      unset MYPC_NETWORK_RECONCILE_APPROVAL
    else
      export MYPC_NETWORK_RECONCILE_APPROVAL="$network_approval"
    fi
    bash "$test_transaction" "$operation"
  }

  assert_safe_probe_output() {
    local text="$1"
    if printf '%s\n' "$text" |
      grep -Eiq '(^|[^[:alnum:]_])(env|secret|config|networksettings|openai_api_key|webui_secret_key)([^[:alnum:]_]|$)'; then
      fail 'fake transaction output exposed environment, secrets, or full inspect data'
    fi
    if printf '%s\n' "$text" | grep -Eq '[{}]'; then
      fail 'fake transaction output exposed structured full inspect data'
    fi
  }

  run_probe_case() {
    local label="$1" mode="$2" operation="$3" deploy_approval="$4" network_approval="$5"
    local initial_proxy="$6" initial_admin="$7" expected_status expected_output expected_state expected_log
    local actual_state actual_log
    reset_probe_case "$initial_proxy" "$initial_admin"
    if output="$(run_transaction_process "$operation" "$mode" "$deploy_approval" "$network_approval" 2>&1)"; then
      status=0
    else
      status=$?
    fi
    captured_fake_output="$(<"$fake_output_file")"
    for probe_text in "$output" "$captured_fake_output"; do
      assert_safe_probe_output "$probe_text"
    done
    case "$label" in
      wrong-hostname|missing-deploy-approval|wrong-deploy-approval|missing-network-approval|wrong-network-approval|id-drift|inspect-failure|admin-id-drift-before-mutation|core-network-id-drift-before-mutation)
        expected_status='nonzero'
        expected_state="$initial_proxy"$'\n'"$initial_admin"
        expected_log=''
        ;;
      post-first-mutation-webui-network-drift)
        expected_status='nonzero'
        expected_state="$probe_canonical_proxy_networks"$'\n'"$probe_admin_networks"
        expected_log='connect core-id proxy-id'
        ;;
      check-temporary)
        expected_status=0
        expected_output='RESULT=noop'
        expected_state="$initial_proxy"$'\n'"$initial_admin"
        expected_log=''
        ;;
      execute-canonical)
        expected_status=0
        expected_output='RESULT=noop'
        expected_state="$initial_proxy"$'\n'"$initial_admin"
        expected_log=''
        ;;
      success)
        expected_status=0
        expected_output='RESULT=changed'
        expected_state="$probe_canonical_proxy_networks"$'\n'"$probe_canonical_admin_networks"
        expected_log=$'connect core-id proxy-id\ndisconnect webui-id admin-id'
        ;;
      connect-failure)
        expected_status='nonzero'
        expected_state="$probe_proxy_networks"$'\n'"$probe_admin_networks"
        expected_log=$'connect core-id proxy-id\ndisconnect core-id proxy-id'
        ;;
      disconnect-failure)
        expected_status='nonzero'
        expected_state="$probe_proxy_networks"$'\n'"$probe_admin_networks"
        expected_log=$'connect core-id proxy-id\ndisconnect webui-id admin-id\nconnect webui-id admin-id\ndisconnect core-id proxy-id'
        ;;
      unexpected-extra-network)
        expected_status='nonzero'
        expected_state="${probe_canonical_proxy_networks},unexpected"$'\n'"$probe_admin_networks"
        expected_log='connect core-id proxy-id'
        ;;
      *) fail "unknown fake Docker probe mode: $mode" ;;
    esac
    if [[ "$expected_status" == nonzero ]]; then
      [ "$status" != 0 ] || fail "fake Docker ${label} unexpectedly succeeded"
    else
      [ "$status" = "$expected_status" ] || fail "fake Docker ${label} returned status ${status}"
      [ "$output" = "$expected_output" ] || fail "fake Docker ${label} returned unexpected output"
    fi
    actual_state="$(<"$state_file")"
    actual_log="$(<"$mutation_log")"
    [ "$actual_state" = "$expected_state" ] || fail "fake Docker ${label} ended in an unexpected state"
    [ "$actual_log" = "$expected_log" ] || fail "fake Docker ${label} recorded an unexpected mutation sequence"
    [ ! -s "$path_resolution_log" ] || fail "fake Docker ${label} resolved a command through PATH"
  }

  run_probe_case wrong-hostname wrong-hostname --execute true true "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case missing-deploy-approval success --execute __unset__ true "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case wrong-deploy-approval success --execute false true "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case missing-network-approval success --execute true __unset__ "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case wrong-network-approval success --execute true false "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case check-temporary success --check __unset__ __unset__ "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case execute-canonical success --execute true true "$probe_canonical_proxy_networks" "$probe_canonical_admin_networks"
  run_probe_case id-drift id-drift --execute true true "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case inspect-failure inspect-fail --execute true true "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case success success --execute true true "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case connect-failure connect-fail --execute true true "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case disconnect-failure disconnect-fail --execute true true "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case unexpected-extra-network extra-network --execute true true "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case admin-id-drift-before-mutation admin-id-drift-before-mutation --execute true true "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case core-network-id-drift-before-mutation core-network-id-drift-before-mutation --execute true true "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case post-first-mutation-webui-network-drift post-first-mutation-webui-network-drift --execute true true "$probe_proxy_networks" "$probe_admin_networks"
  trap - EXIT
  rm -rf "$probe_dir"
}

run_fake_transaction_probe

printf 'OK   Open WebUI ingress and atomic network-reconcile contract is present\n'
