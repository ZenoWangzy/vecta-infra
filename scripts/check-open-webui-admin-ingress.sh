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
  "readonly DOCKER_HOST_SOCKET='unix:///var/run/docker.sock'" \
  "readonly ID_BIN='/usr/bin/id'" \
  "readonly HOSTNAME_BIN='/usr/bin/hostname'" \
  "readonly FLOCK_BIN='/usr/bin/flock'" \
  "readonly CHMOD_BIN='/usr/bin/chmod'" \
  "readonly CHOWN_BIN='/usr/bin/chown'" \
  "readonly LOCK_FILE='/run/vecta-open-webui-admin-network.lock'" \
  "export PATH='/usr/bin:/bin'" \
  "readonly PROXY_CONTAINER='openclaw-webui-proxy'" \
  "readonly ADMIN_CONTAINER='openclaw-admin-console'" \
  "readonly CORE_NETWORK='openclaw-enterprise_openclaw-net'" \
  "readonly WEBUI_NETWORK='openclaw-enterprise_open-webui-net'" \
  'MYPC_DEPLOY_ENABLED:-' \
  'MYPC_NETWORK_RECONCILE_APPROVAL:-'; do
  require_literal "$TRANSACTION" "$literal"
done
for literal in \
  'require_root()' \
  '"$ID_BIN" -u' \
  'umask 077' \
  '"$CHMOD_BIN" 0600 "$LOCK_FILE"' \
  '"$CHOWN_BIN" root:root "$LOCK_FILE"'; do
  require_literal "$TRANSACTION" "$literal"
done
require_absent "$TRANSACTION" '/run/lock/vecta-open-webui-admin-network.lock'
require_absent "$TRANSACTION" 'umask 000'
for literal in \
  'docker_local()' \
  '/usr/bin/env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_CONFIG' \
  '"$DOCKER_BIN" --host "$DOCKER_HOST_SOCKET"' \
  'acquire_lock || return 1' \
  '"$FLOCK_BIN" -n 9' \
  'trap '\''release_lock'\'' EXIT'; do
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
  'run_network_mutation()' \
  'exact measured baseline was not proven' \
  'rc=' \
  'RESULT=changed' \
  'RESULT=noop'; do
  require_literal "$TRANSACTION" "$literal"
done
require_absent "$TRANSACTION" '.Config'
require_absent "$TRANSACTION" '.Env'
require_absent "$TRANSACTION" 'Config.Env'
require_absent "$TRANSACTION" '2>/dev/null'
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
  grep -Ev '"\$DOCKER_BIN" network (inspect|connect|disconnect)|"\$DOCKER_BIN" network "\$action"' >/dev/null; then
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
playbook_header="$(sed -n '1,12p' "$PLAYBOOK")"
for literal in \
  'become: true' \
  'become_user: root' \
  'become_method: sudo' \
  "become_flags: '-n'"; do
  printf '%s\n' "$playbook_header" | grep -Fq -- "$literal" ||
    fail "playbook header missing root become contract: ${literal}"
done
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
  '唯一支持的恢复路径' \
  '受保护事务脚本' \
  '不得对 replacement' \
  '自动操作' \
  'baseline not proven' \
  '四个 baseline IDs' \
  '精确重验' \
  '仅预检' \
  '不是成功证据' \
  'uvx --from ansible-core ansible-playbook playbooks/mypc-network-reconcile.yml -i inventories/mypc/hosts.ini --check -e mypc_deploy_enabled=true -e mypc_network_reconcile_approval=true' \
  'uvx --from ansible-core ansible-playbook playbooks/mypc-network-reconcile.yml -i inventories/mypc/hosts.ini -e mypc_deploy_enabled=true -e mypc_network_reconcile_approval=true' \
  'that actor remains unknown'; do
  require_literal "$RUNBOOK" "$literal"
done
if grep -nE '^[[:space:]]*/usr/bin/docker[[:space:]]+(inspect|network[[:space:]]+(connect|disconnect))' "$RUNBOOK" >/dev/null; then
  fail 'runbook contains manual Docker mutation or inspect commands'
fi

run_fake_transaction_probe() {
  local probe_dir probe_bin path_bin id_bin lock_bin chown_bin test_transaction state_file lock_file lock_holder lock_held
  local mutation_log fake_output_file fake_docker_calls flock_log chown_log
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
  id_bin="$probe_dir/id"
  lock_bin="$probe_dir/flock"
  chown_bin="$probe_dir/chown"
  test_transaction="$probe_dir/reconcile-open-webui-admin-network.sh"
  state_file="$probe_dir/state"
  lock_file="$probe_dir/lock"
  lock_holder="$probe_dir/lock-holder"
  lock_held="$probe_dir/lock-held"
  mutation_log="$probe_dir/mutations"
  fake_output_file="$probe_dir/output"
  fake_docker_calls="$probe_dir/docker-calls"
  flock_log="$probe_dir/flock-log"
  chown_log="$probe_dir/chown-log"
  inspect_count_file="$probe_dir/inspect-count"
  network_inspect_count_file="$probe_dir/network-inspect-count"
  path_resolution_log="$probe_dir/path-resolution"
  mkdir "$probe_bin" "$path_bin"
  trap 'rm -rf "$probe_dir"' EXIT

  cat > "$lock_bin" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

: "$FAKE_FLOCK_LOG"
: "$FAKE_FLOCK_HOLDER"
: "$FAKE_FLOCK_HELD"
[[ "$#" -ge 2 && "$1" == '-n' && "$2" == 9 ]] || {
  printf 'stderr-sentinel: unexpected fake flock invocation\n' >&2
  exit 90
}
printf '%s %s\n' "$1" "$2" >> "$FAKE_FLOCK_LOG"
if [[ -e "$FAKE_FLOCK_HOLDER" ]]; then
  exit 1
fi
: > "$FAKE_FLOCK_HELD"
EOF

  cat > "$id_bin" <<'EOF'
#!/bin/sh
if [ "${FAKE_MODE:-}" = non-root ]; then
  printf '1000\n'
else
  printf '0\n'
fi
EOF

  cat > "$chown_bin" <<'EOF'
#!/bin/sh
set -eu
[ "$#" = 2 ] && [ "$1" = root:root ] && [ -f "$2" ] || {
  printf 'stderr-sentinel: unexpected fake chown invocation\n' >&2
  exit 95
}
printf '%s\n' "$*" >> "$FAKE_CHOWN_LOG"
EOF

  cat > "$probe_bin/hostname" <<'EOF'
#!/bin/sh
if [ "${FAKE_MODE:-}" = wrong-hostname ]; then
  printf 'not-mypc\n'
elif [ "${FAKE_MODE:-}" = hostname-fail ]; then
  printf 'stderr-sentinel: hostname failed\n' >&2
  exit 96
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

stderr_sentinel() { printf 'stderr-sentinel: %s\n' "$1" >&2; }
fail() { stderr_sentinel 'unexpected fake Docker invocation'; exit 90; }
[[ "$#" -ge 2 && "$1" == '--host' && "$2" == 'unix:///var/run/docker.sock' ]] || fail
[[ -z "${DOCKER_HOST+x}" && -z "${DOCKER_CONTEXT+x}" && -z "${DOCKER_CONFIG+x}" ]] || {
  stderr_sentinel 'Docker endpoint environment was not cleared'
  exit 92
}
[[ "$FAKE_REQUIRE_LOCK" != 1 || -e "$FAKE_FLOCK_HELD" ]] || {
  stderr_sentinel 'Docker call happened without the transaction lock'
  exit 93
}
printf '%s\n' "$*" >> "$FAKE_DOCKER_CALLS"
shift 2
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
        if [[ "$FAKE_MODE" == inspect-fail && "${4:-}" == "$WEBUI_NETWORK" ]]; then
          stderr_sentinel 'narrow inspect failed'
          exit 91
        fi
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
            if [[ "$FAKE_MODE" == connect-fail || "$FAKE_MODE" == extra-network || "$FAKE_MODE" == rollback-disconnect-fail ]]; then
              stderr_sentinel 'forward connect failed'
              exit 42
            fi
            ;;
          webui-id:admin-id)
            write_state "$proxy_networks" "$WEBUI_NETWORK,$CORE_NETWORK"
            record connect webui-id admin-id
            if [[ "$FAKE_MODE" == rollback-reconnect-fail ]]; then
              stderr_sentinel 'rollback reconnect failed'
              exit 44
            fi
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
            if [[ "$FAKE_MODE" == disconnect-fail || "$FAKE_MODE" == rollback-reconnect-fail ]]; then
              stderr_sentinel 'forward disconnect failed'
              exit 43
            fi
            ;;
          core-id:proxy-id)
            write_state "$WEBUI_NETWORK" "$admin_networks"
            record disconnect core-id proxy-id
            if [[ "$FAKE_MODE" == rollback-disconnect-fail ]]; then
              stderr_sentinel 'rollback disconnect failed'
              exit 45
            fi
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
  chmod +x "$id_bin" "$lock_bin" "$chown_bin" "$probe_bin/hostname" "$probe_bin/docker" "$path_bin/hostname" "$path_bin/docker"

  cp "$TRANSACTION" "$test_transaction"
  sed \
    -e "s|^readonly DOCKER_BIN='/usr/bin/docker'$|readonly DOCKER_BIN='$probe_bin/docker'|" \
    -e "s|^readonly ID_BIN='/usr/bin/id'$|readonly ID_BIN='$id_bin'|" \
    -e "s|^readonly FLOCK_BIN='/usr/bin/flock'$|readonly FLOCK_BIN='$lock_bin'|" \
    -e "s|^readonly CHMOD_BIN='/usr/bin/chmod'$|readonly CHMOD_BIN='/bin/chmod'|" \
    -e "s|^readonly CHOWN_BIN='/usr/bin/chown'$|readonly CHOWN_BIN='$chown_bin'|" \
    -e "s|^readonly LOCK_FILE='/run/vecta-open-webui-admin-network.lock'$|readonly LOCK_FILE='$lock_file'|" \
    -e "s|^readonly HOSTNAME_BIN='/usr/bin/hostname'$|readonly HOSTNAME_BIN='$probe_bin/hostname'|" \
    "$test_transaction" > "$probe_dir/transaction.replaced"
  mv "$probe_dir/transaction.replaced" "$test_transaction"
  chmod +x "$test_transaction"
  [ "$(grep -Fc -- "readonly DOCKER_BIN='$probe_bin/docker'" "$test_transaction")" = 1 ] ||
    fail 'temporary transaction did not replace the exact Docker constant'
  [ "$(grep -Fc -- "readonly ID_BIN='$id_bin'" "$test_transaction")" = 1 ] ||
    fail 'temporary transaction did not replace the exact id constant'
  [ "$(grep -Fc -- "readonly HOSTNAME_BIN='$probe_bin/hostname'" "$test_transaction")" = 1 ] ||
    fail 'temporary transaction did not replace the exact hostname constant'
  [ "$(grep -Fc -- "readonly FLOCK_BIN='$lock_bin'" "$test_transaction")" = 1 ] ||
    fail 'temporary transaction did not replace the exact flock constant'
  [ "$(grep -Fc -- "readonly CHOWN_BIN='$chown_bin'" "$test_transaction")" = 1 ] ||
    fail 'temporary transaction did not replace the exact chown constant'
  [ "$(grep -Fc -- "readonly LOCK_FILE='$lock_file'" "$test_transaction")" = 1 ] ||
    fail 'temporary transaction did not replace the exact lock-file constant'
  [ "$(grep -Fc -- "readonly DOCKER_BIN='/usr/bin/docker'" "$test_transaction")" = 0 ] ||
    fail 'temporary transaction retained the production Docker constant'
  [ "$(grep -Fc -- "readonly HOSTNAME_BIN='/usr/bin/hostname'" "$test_transaction")" = 0 ] ||
    fail 'temporary transaction retained the production hostname constant'
  [ "$(grep -Fc -- "readonly FLOCK_BIN='/usr/bin/flock'" "$test_transaction")" = 0 ] ||
    fail 'temporary transaction retained the production flock constant'
  [ "$(grep -Fc -- "readonly CHOWN_BIN='/usr/bin/chown'" "$test_transaction")" = 0 ] ||
    fail 'temporary transaction retained the production chown constant'
  [ "$(grep -Fc -- "readonly LOCK_FILE='/run/vecta-open-webui-admin-network.lock'" "$test_transaction")" = 0 ] ||
    fail 'temporary transaction retained the production lock-file constant'

  reset_probe_case() {
    printf '%s\n%s\n' "$1" "$2" > "$state_file"
    : > "$mutation_log"
    : > "$fake_output_file"
    : > "$fake_docker_calls"
    : > "$flock_log"
    : > "$chown_log"
    printf '0\n' > "$inspect_count_file"
    printf '0\n' > "$network_inspect_count_file"
    : > "$path_resolution_log"
    rm -f "$lock_file" "$lock_holder" "$lock_held"
  }

  run_transaction_process() {
    local operation="$1" mode="$2" deploy_approval="$3" network_approval="$4" docker_environment='clean'
    if [[ "$#" -ge 5 ]]; then
      docker_environment="$5"
    fi
    export PATH="$path_bin:/usr/bin:/bin"
    export FAKE_MODE="$mode" FAKE_STATE="$state_file" FAKE_LOG="$mutation_log"
    export FAKE_OUTPUT="$fake_output_file" FAKE_INSPECT_COUNT="$inspect_count_file"
    export FAKE_NETWORK_INSPECT_COUNT="$network_inspect_count_file"
    export FAKE_PATH_RESOLUTION_LOG="$path_resolution_log"
    export FAKE_DOCKER_CALLS="$fake_docker_calls" FAKE_REQUIRE_LOCK=0
    export FAKE_FLOCK_LOG="$flock_log" FAKE_FLOCK_HOLDER="$lock_holder" FAKE_FLOCK_HELD="$lock_held"
    export FAKE_CHOWN_LOG="$chown_log"
    if [[ "$operation" == '--execute' ]]; then
      export FAKE_REQUIRE_LOCK=1
    fi
    unset DOCKER_HOST DOCKER_CONTEXT DOCKER_CONFIG
    case "$docker_environment" in
      clean) ;;
      remote-host) export DOCKER_HOST='ssh://remote.example' ;;
      remote-context) export DOCKER_CONTEXT='remote-context' ;;
      remote-config) export DOCKER_CONFIG='/tmp/remote-docker-config' ;;
      *) fail "unknown Docker environment: $docker_environment" ;;
    esac
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

  assert_stderr_sentinel() {
    local label="$1" text="$2"
    printf '%s\n' "$text" | grep -Fq 'stderr-sentinel:' ||
      fail "fake Docker ${label} did not expose stderr sentinel"
  }

  assert_stderr_context() {
    local label="$1" text="$2" expected
    case "$label" in
      hostname-failure) expected='FAIL: hostname command rc=96' ;;
      inspect-failure) expected='FAIL: Docker network inspect network=openclaw-enterprise_open-webui-net rc=91' ;;
      connect-failure) expected='FAIL: forward Docker network connect network=core-id container=proxy-id rc=42' ;;
      disconnect-failure) expected='FAIL: forward Docker network disconnect network=webui-id container=admin-id rc=43' ;;
      rollback-reconnect-fail) expected='FAIL: rollback Docker network connect network=webui-id container=admin-id rc=44' ;;
      rollback-disconnect-fail) expected='FAIL: rollback Docker network disconnect network=core-id container=proxy-id rc=45' ;;
      *) fail "unknown stderr context case: $label" ;;
    esac
    printf '%s\n' "$text" | grep -Fq -- "$expected" ||
      fail "fake Docker ${label} did not expose operation context and rc"
  }

  lock_mode() {
    if stat -f '%Lp' "$lock_file" >/dev/null 2>&1; then
      stat -f '%Lp' "$lock_file"
    else
      stat -c '%a' "$lock_file"
    fi
  }

  assert_lock_file_secure() {
    [ "$(lock_mode)" = 600 ] || fail 'lock file was not created with mode 0600'
    [ "$(<"$chown_log")" = "root:root $lock_file" ] ||
      fail 'lock file was not secured as root:root'
  }

  run_probe_case() {
    local label="$1" mode="$2" operation="$3" deploy_approval="$4" network_approval="$5"
    local initial_proxy="$6" initial_admin="$7" docker_environment='clean'
    local expected_status expected_output expected_state expected_log
    if [[ "$#" -ge 8 ]]; then
      docker_environment="$8"
    fi
    local actual_state actual_log actual_docker_calls actual_flock_log docker_call
    reset_probe_case "$initial_proxy" "$initial_admin"
    if output="$(run_transaction_process "$operation" "$mode" "$deploy_approval" "$network_approval" "$docker_environment" 2>&1)"; then
      status=0
    else
      status=$?
    fi
    captured_fake_output="$(<"$fake_output_file")"
    for probe_text in "$output" "$captured_fake_output"; do
      assert_safe_probe_output "$probe_text"
    done
    case "$label" in
      hostname-failure|inspect-failure|connect-failure|disconnect-failure|rollback-reconnect-fail|rollback-disconnect-fail)
        assert_stderr_sentinel "$label" "$output"
        assert_stderr_context "$label" "$output"
        ;;
    esac
    case "$label" in
      initial-missing|initial-extra|initial-in-progress|hostname-failure|wrong-hostname|missing-deploy-approval|wrong-deploy-approval|missing-network-approval|wrong-network-approval|id-drift|inspect-failure|admin-id-drift-before-mutation|core-network-id-drift-before-mutation|non-root)
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
      success|remote-docker-host|remote-docker-context|remote-docker-config)
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
      rollback-reconnect-fail)
        expected_status='nonzero'
        expected_state="$probe_canonical_proxy_networks"$'\n'"$probe_admin_networks"
        expected_log=$'connect core-id proxy-id\ndisconnect webui-id admin-id\nconnect webui-id admin-id'
        ;;
      rollback-disconnect-fail)
        expected_status='nonzero'
        expected_state="$probe_proxy_networks"$'\n'"$probe_admin_networks"
        expected_log=$'connect core-id proxy-id\ndisconnect core-id proxy-id'
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
    actual_docker_calls="$(<"$fake_docker_calls")"
    actual_flock_log="$(<"$flock_log")"
    if [[ -n "$actual_docker_calls" ]]; then
      while IFS= read -r docker_call; do
        [[ "$docker_call" == '--host unix:///var/run/docker.sock '* ]] ||
          fail "fake Docker $label observed a non-local endpoint"
      done <<< "$actual_docker_calls"
    fi
    if [[ -n "$actual_flock_log" ]]; then
      [ "$actual_flock_log" = '-n 9' ] ||
        fail "fake flock $label did not receive the non-blocking FD contract"
    fi
    [ "$actual_state" = "$expected_state" ] || fail "fake Docker ${label} ended in an unexpected state"
    [ "$actual_log" = "$expected_log" ] || fail "fake Docker ${label} recorded an unexpected mutation sequence"
    if [[ "$label" == non-root ]]; then
      [ -z "$actual_docker_calls" ] || fail 'non-root execution reached Docker inspect or mutation'
      [ -z "$actual_flock_log" ] || fail 'non-root execution reached the lock'
      [ ! -e "$lock_file" ] || fail 'non-root execution created the lock file'
    fi
    if [[ "$label" == success ]]; then
      assert_lock_file_secure
    fi
    case "$label" in
      rollback-reconnect-fail|rollback-disconnect-fail)
        if printf '%s\n' "$output" | grep -Fq 'baseline restored'; then
          fail "fake Docker ${label} incorrectly claimed baseline restored"
        fi
        ;;
    esac
    [ ! -s "$path_resolution_log" ] || fail "fake Docker ${label} resolved a command through PATH"
  }

  run_lock_contention_probe() {
    local output status actual_state actual_log actual_docker_calls actual_flock_log
    reset_probe_case "$probe_proxy_networks" "$probe_admin_networks"
    : > "$lock_holder"
    if output="$(run_transaction_process --execute success true true clean 2>&1)"; then
      status=0
    else
      status=$?
    fi
    actual_state="$(<"$state_file")"
    actual_log="$(<"$mutation_log")"
    actual_docker_calls="$(<"$fake_docker_calls")"
    actual_flock_log="$(<"$flock_log")"
    [ "$status" != 0 ] || fail 'lock contention unexpectedly succeeded'
    [ "$actual_state" = "$probe_proxy_networks"$'\n'"$probe_admin_networks" ] ||
      fail 'lock contention changed the measured topology'
    [ -z "$actual_log" ] || fail 'lock contention reached a Docker mutation'
    [ -z "$actual_docker_calls" ] || fail 'lock contention reached Docker before the lock'
    [ ! -s "$fake_output_file" ] || fail 'lock contention produced fake Docker output'
    [ "$actual_flock_log" = '-n 9' ] || fail 'lock contention did not use non-blocking flock'
    printf '%s\n' "$output" | grep -Fq 'another Open WebUI network reconciliation is already running' ||
      fail 'lock contention did not fail with the lock error'
    rm -f "$lock_holder" "$lock_held"
  }

  run_lock_contention_probe
  run_probe_case non-root non-root --execute true true "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case wrong-hostname wrong-hostname --execute true true "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case missing-deploy-approval success --execute __unset__ true "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case wrong-deploy-approval success --execute false true "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case missing-network-approval success --execute true __unset__ "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case wrong-network-approval success --execute true false "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case initial-missing success --execute true true '' "$probe_admin_networks"
  run_probe_case initial-extra success --execute true true "$probe_proxy_networks" "${probe_admin_networks},unexpected"
  run_probe_case initial-in-progress success --execute true true "$probe_canonical_proxy_networks" "$probe_admin_networks"
  run_probe_case hostname-failure hostname-fail --execute true true "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case check-temporary success --check __unset__ __unset__ "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case execute-canonical success --execute true true "$probe_canonical_proxy_networks" "$probe_canonical_admin_networks"
  run_probe_case id-drift id-drift --execute true true "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case inspect-failure inspect-fail --execute true true "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case success success --execute true true "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case remote-docker-host success --execute true true "$probe_proxy_networks" "$probe_admin_networks" remote-host
  run_probe_case remote-docker-context success --execute true true "$probe_proxy_networks" "$probe_admin_networks" remote-context
  run_probe_case remote-docker-config success --execute true true "$probe_proxy_networks" "$probe_admin_networks" remote-config
  run_probe_case connect-failure connect-fail --execute true true "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case disconnect-failure disconnect-fail --execute true true "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case rollback-reconnect-fail rollback-reconnect-fail --execute true true "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case rollback-disconnect-fail rollback-disconnect-fail --execute true true "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case unexpected-extra-network extra-network --execute true true "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case admin-id-drift-before-mutation admin-id-drift-before-mutation --execute true true "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case core-network-id-drift-before-mutation core-network-id-drift-before-mutation --execute true true "$probe_proxy_networks" "$probe_admin_networks"
  run_probe_case post-first-mutation-webui-network-drift post-first-mutation-webui-network-drift --execute true true "$probe_proxy_networks" "$probe_admin_networks"
  trap - EXIT
  rm -rf "$probe_dir"
}

run_fake_transaction_probe

printf 'OK   Open WebUI ingress and atomic network-reconcile contract is present\n'
