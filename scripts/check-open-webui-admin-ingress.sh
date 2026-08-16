#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROXY_TEMPLATE="${ROOT_DIR}/roles/open-webui/templates/nginx.conf.j2"
HOST_TEMPLATE="${ROOT_DIR}/roles/open-webui/templates/host-nginx-site.conf.j2"
NETWORK_RECONCILE_PLAYBOOK="${ROOT_DIR}/playbooks/mypc-network-reconcile.yml"
RUNBOOK="${ROOT_DIR}/docs/runbooks/mypc-data-structure-compatibility.md"
BASE_SHA="${BASE_SHA:-7f6bdcd29d74d8dab80ca1ab17ab63b444fdb0ee}"

fail() {
  echo "FAIL: $*" >&2
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

task_block() {
  local task_name="$1"
  awk -v wanted="$task_name" '
    index($0, "- name: " wanted) { found=1; next }
    found && $0 ~ /^[[:space:]]*- name:/ { exit }
    found { print }
  ' "$NETWORK_RECONCILE_PLAYBOOK"
}

require_task_literal() {
  local task_name="$1"
  local literal="$2"
  local block
  block="$(task_block "$task_name")"
  [ -n "$block" ] || fail "missing task: ${task_name}"
  grep -Fq -- "$literal" <<<"$block" || fail "${task_name} missing: ${literal}"
}

require_file "$PROXY_TEMPLATE"
require_file "$HOST_TEMPLATE"
require_file "$NETWORK_RECONCILE_PLAYBOOK"
require_file "$RUNBOOK"

# Keep the existing browser ingress contract small and explicit.
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

# The branch diff is deliberately limited to the four authorized artifacts.
git rev-parse --verify "${BASE_SHA}^{commit}" >/dev/null 2>&1 || fail "base commit is unavailable: ${BASE_SHA}"
actual_scope="$(git diff --name-only "$BASE_SHA" -- | sort -u)"
expected_scope="$(printf '%s\n' \
  docs/runbooks/mypc-data-structure-compatibility.md \
  inventories/mypc/group_vars/mypc.yml \
  playbooks/mypc-network-reconcile.yml \
  scripts/check-open-webui-admin-ingress.sh)"
[ "$actual_scope" = "$expected_scope" ] || fail "diff scope is not exactly the four authorized files"
[ -z "$(git ls-files --others --exclude-standard)" ] || fail "untracked files are outside the authorized scope"

# Target and approval guards are on the play and on every mutating command.
for literal in \
  'hosts: mypc' \
  'mypc_deploy_enabled is defined' \
  'mypc_deploy_enabled | bool' \
  'mypc_network_reconcile_approval is defined' \
  'mypc_network_reconcile_approval | bool' \
  '/bin/hostname' \
  "stdout | trim == 'mypc'"; do
  require_literal "$NETWORK_RECONCILE_PLAYBOOK" "$literal"
done

for task in \
  'Attach Proxy to core from the temporary state' \
  'Detach Admin from WebUI from the temporary state' \
  'Reconnect Admin when baseline had WebUI and current state lacks it' \
  'Disconnect Proxy when baseline lacked core and current state has it'; do
  require_task_literal "$task" 'ansible.builtin.command:'
  require_task_literal "$task" 'not ansible_check_mode'
  require_task_literal "$task" 'mypc_deploy_enabled is defined'
  require_task_literal "$task" 'mypc_deploy_enabled | bool'
  require_task_literal "$task" 'mypc_network_reconcile_approval is defined'
  require_task_literal "$task" 'mypc_network_reconcile_approval | bool'
  require_task_literal "$task" 'failed_when:'
done

[ "$(grep -Eo '(^|[^[:alnum:]_])connect([^[:alnum:]_]|$)' "$NETWORK_RECONCILE_PLAYBOOK" | wc -l | tr -d ' ')" = 2 ] ||
  fail "expected exactly two network connect operations"
[ "$(grep -Eo '(^|[^[:alnum:]_])disconnect([^[:alnum:]_]|$)' "$NETWORK_RECONCILE_PLAYBOOK" | wc -l | tr -d ' ')" = 2 ] ||
  fail "expected exactly two network disconnect operations"

# Only the temporary and canonical sets may be accepted initially. The
# expected-after-attach state is checked in its own post-mutation assertion.
for literal in \
  'mypc_reconcile_temporary_proxy_networks:' \
  'mypc_reconcile_temporary_admin_networks:' \
  'mypc_reconcile_expected_proxy_networks:' \
  'mypc_reconcile_expected_admin_networks:' \
  'mypc_reconcile_proxy_networks == mypc_reconcile_temporary_proxy_networks' \
  'mypc_reconcile_admin_networks == mypc_reconcile_temporary_admin_networks' \
  'mypc_reconcile_proxy_networks == mypc_reconcile_expected_proxy_networks' \
  'mypc_reconcile_admin_networks == mypc_reconcile_expected_admin_networks'; do
  require_literal "$NETWORK_RECONCILE_PLAYBOOK" "$literal"
done
starting_block="$(task_block 'Require fixed identities and only temporary or canonical starting state')"
if awk '
  /mypc_reconcile_expected_proxy_networks/ { expected=1 }
  expected && /mypc_reconcile_temporary_admin_networks/ { bad=1 }
  END { exit !bad }
' <<<"$starting_block"; then
  fail "initial topology assertion accepts the in-progress Proxy/Admin pairing"
fi
if grep -Fq 'mypc_reconcile_after_attach' <<<"$starting_block"; then
  fail "initial topology assertion is coupled to a post-attach state"
fi

# Rescue is an inverse state reconciliation, in this order, not an rc-only
# rollback. Extra current attachments must not suppress either inverse test.
for literal in \
  'mypc_reconcile_webui_network_name in mypc_reconcile_baseline_admin_networks' \
  'mypc_reconcile_webui_network_name not in mypc_reconcile_rescue_admin_networks' \
  'mypc_reconcile_core_network_name not in mypc_reconcile_baseline_proxy_networks' \
  'mypc_reconcile_core_network_name in mypc_reconcile_rescue_proxy_networks' \
  'Require Admin to match the measured baseline' \
  'Require both rescue network sets to match the measured baseline' \
  'Preserve original failure and finish nonzero after rescue evidence'; do
  require_literal "$NETWORK_RECONCILE_PLAYBOOK" "$literal"
done
admin_rollback_line="$(grep -nF 'Reconnect Admin when baseline had WebUI and current state lacks it' "$NETWORK_RECONCILE_PLAYBOOK" | cut -d: -f1)"
proxy_rollback_line="$(grep -nF 'Disconnect Proxy when baseline lacked core and current state has it' "$NETWORK_RECONCILE_PLAYBOOK" | cut -d: -f1)"
[ "$admin_rollback_line" -lt "$proxy_rollback_line" ] || fail "rescue rollback order is not Admin then Proxy"

# No shell/template/module lifecycle escape hatch is allowed in this playbook.
if grep -Eiq \
  'ansible\.builtin\.(shell|raw|script|file|template|service|copy)|community\.docker|^[[:space:]]*(roles|import_playbook|import_role):|/bin/sh[[:space:]]+-ec|docker(-compose|[[:space:]]+compose)' \
  "$NETWORK_RECONCILE_PLAYBOOK"; then
  fail "playbook contains a forbidden shell, template, role, or Docker module path"
fi
if grep -Eiq '^[[:space:]]*-[[:space:]]+(restart|recreate|pull|build|create|start|stop|rm|run|prune|up|down)$' "$NETWORK_RECONCILE_PLAYBOOK"; then
  fail "playbook contains a forbidden Docker lifecycle command"
fi
for forbidden in image volume data; do
  if grep -Eiq "^[[:space:]]*-[[:space:]]+${forbidden}([[:space:]]|$)" "$NETWORK_RECONCILE_PLAYBOOK"; then
    fail "playbook contains a forbidden ${forbidden} operation"
  fi
done

if grep -Fq 'admin_console_join_open_webui_network' "$ROOT_DIR/inventories/mypc/group_vars/mypc.yml"; then
  fail "dead admin_console_join_open_webui_network inventory variable remains"
fi

# Runbook must distinguish preflight from mutation and state that the actor is
# unknown; exact manual rollback commands are part of the contract.
for literal in \
  '仅预检' \
  '不是成功证据' \
  'temporary' \
  'canonical' \
  '--check' \
  'ansible-playbook playbooks/mypc-network-reconcile.yml -i inventories/mypc/hosts.ini -e mypc_deploy_enabled=true -e mypc_network_reconcile_approval=true' \
  'docker network connect openclaw-enterprise_open-webui-net openclaw-admin-console' \
  'docker network disconnect openclaw-enterprise_openclaw-net openclaw-webui-proxy' \
  'that actor remains unknown'; do
  require_literal "$RUNBOOK" "$literal"
done
connect_line="$(grep -nF 'docker network connect openclaw-enterprise_open-webui-net openclaw-admin-console' "$RUNBOOK" | tail -n1 | cut -d: -f1)"
disconnect_line="$(grep -nF 'docker network disconnect openclaw-enterprise_openclaw-net openclaw-webui-proxy' "$RUNBOOK" | tail -n1 | cut -d: -f1)"
[ "$connect_line" -lt "$disconnect_line" ] || fail "runbook rollback order is not Admin then Proxy"

echo 'OK   Open WebUI/Admin ingress and network-reconcile contract is present'
