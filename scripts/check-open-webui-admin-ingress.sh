#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROXY_TEMPLATE="${ROOT_DIR}/roles/open-webui/templates/nginx.conf.j2"
HOST_TEMPLATE="${ROOT_DIR}/roles/open-webui/templates/host-nginx-site.conf.j2"
NETWORK_RECONCILE_PLAYBOOK="${ROOT_DIR}/playbooks/mypc-network-reconcile.yml"
RUNBOOK="${ROOT_DIR}/docs/runbooks/mypc-data-structure-compatibility.md"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

require_file() {
  local file="$1"
  [ -f "$file" ] || fail "missing file: ${file}"
}

require_literal() {
  local file="$1"
  local literal="$2"
  grep -Fq -- "$literal" "$file" || fail "${file} missing literal: ${literal}"
}

require_file "$PROXY_TEMPLATE"
require_file "$HOST_TEMPLATE"
require_file "$NETWORK_RECONCILE_PLAYBOOK"
require_file "$RUNBOOK"

# Public host nginx owns the matrix-ai.com.cn browser ingress. It must route
# /admin/ through the Open WebUI proxy instead of directly to an app container.
require_literal "$HOST_TEMPLATE" 'location = /admin {'
require_literal "$HOST_TEMPLATE" 'return 301 /admin/;'
require_literal "$HOST_TEMPLATE" 'location ^~ /admin/ {'
require_literal "$HOST_TEMPLATE" 'proxy_pass {{ host_nginx_webui_proxy_url }};'

# The Open WebUI proxy owns container-network routing and strips the /admin
# mount before forwarding to the Admin Console SPA.
require_literal "$PROXY_TEMPLATE" 'location = /admin {'
require_literal "$PROXY_TEMPLATE" 'return 302 /admin/;'
require_literal "$PROXY_TEMPLATE" 'location ^~ /admin/ {'
require_literal "$PROXY_TEMPLATE" 'rewrite ^/admin/(.*)$ /$1 break;'
require_literal "$PROXY_TEMPLATE" 'proxy_pass http://$admin_console:5173;'

# ADR-070 personal-channel bind page is served by fleet-gateway, not the SPA.
require_literal "$PROXY_TEMPLATE" 'location ^~ /personal/ {'
require_literal "$PROXY_TEMPLATE" 'proxy_pass http://$fleet_gateway:3000;'

admin_location_count() {
  local file="$1"
  local count
  count="$(grep -F 'location = /admin {' "$file" | wc -l | tr -d ' ')"
  [ "$count" = "1" ] || fail "${file} must contain exactly one location = /admin block, found ${count}"
}

admin_location_count "$PROXY_TEMPLATE"
admin_location_count "$HOST_TEMPLATE"
echo 'OK   Open WebUI /admin ingress contract is infra-owned and present'

# N1: the target is guarded by the group, the two explicit approvals, and the
# remote /bin/hostname result. Inventory metadata is not treated as identity.
require_literal "$NETWORK_RECONCILE_PLAYBOOK" 'hosts: mypc'
require_literal "$NETWORK_RECONCILE_PLAYBOOK" 'mypc_deploy_enabled is defined'
require_literal "$NETWORK_RECONCILE_PLAYBOOK" 'mypc_deploy_enabled | bool'
require_literal "$NETWORK_RECONCILE_PLAYBOOK" 'mypc_network_reconcile_approval is defined'
require_literal "$NETWORK_RECONCILE_PLAYBOOK" 'mypc_network_reconcile_approval | bool'
require_literal "$NETWORK_RECONCILE_PLAYBOOK" '/bin/hostname'
require_literal "$NETWORK_RECONCILE_PLAYBOOK" "stdout | trim == 'mypc'"
if grep -Eq 'inventory_hostname[[:space:]]*==' "$NETWORK_RECONCILE_PLAYBOOK"; then
  fail "$NETWORK_RECONCILE_PLAYBOOK must not use an inventory_hostname guard"
fi
if grep -Eq 'ansible_host|mypc-host\.example\.com' "$NETWORK_RECONCILE_PLAYBOOK"; then
  fail "$NETWORK_RECONCILE_PLAYBOOK must not use the placeholder host variable"
fi
echo 'PASS N1 target and approval guards'

# N2: IDs belong to the fixed /bin/sh -ec transaction, never to Ansible vars.
for fixed_literal in \
  openclaw-enterprise_openclaw-net \
  openclaw-enterprise_open-webui-net \
  openclaw-webui-proxy \
  openclaw-admin-console \
  openclaw-fleet-gateway; do
  require_literal "$NETWORK_RECONCILE_PLAYBOOK" "$fixed_literal"
done
require_literal "$NETWORK_RECONCILE_PLAYBOOK" '{{ "{{.Id}}|{{.Name}}" }}'
require_literal "$NETWORK_RECONCILE_PLAYBOOK" '{{ "{{.Id}}|{{.State.Running}}|{{json .NetworkSettings.Networks}}" }}'
require_literal "$NETWORK_RECONCILE_PLAYBOOK" '{{ "{{.Id}}|{{.State.Running}}" }}'
if grep -Eq 'mypc_reconcile_[A-Za-z0-9_]*(id|ids|network|container)' "$NETWORK_RECONCILE_PLAYBOOK"; then
  fail "$NETWORK_RECONCILE_PLAYBOOK must not expose dynamic IDs as Ansible variables"
fi
if grep -Eq '^[[:space:]]+(set_fact|loop):' "$NETWORK_RECONCILE_PLAYBOOK"; then
  fail "$NETWORK_RECONCILE_PLAYBOOK must not build dynamic ID state with Ansible tasks"
fi
if grep -Eq '\{\{[[:space:]]*mypc_reconcile_' "$NETWORK_RECONCILE_PLAYBOOK"; then
  fail "$NETWORK_RECONCILE_PLAYBOOK must not use Ansible ID variables in mutations"
fi
connect_count="$(grep -Fc 'docker network connect' "$NETWORK_RECONCILE_PLAYBOOK")"
disconnect_count="$(grep -Fc 'docker network disconnect' "$NETWORK_RECONCILE_PLAYBOOK")"
[ "$connect_count" = "2" ] || fail "$NETWORK_RECONCILE_PLAYBOOK must have attach plus rescue reconnect"
[ "$disconnect_count" = "1" ] || fail "$NETWORK_RECONCILE_PLAYBOOK must have exactly one admin detach"
echo 'PASS N2 fixed IDs stay inside the shell transaction'

# N3: Fleet is a dependency probe only. Its network set is intentionally not a
# contract; the fixed health URL proves DNS plus HTTP reachability from proxy.
require_literal "$NETWORK_RECONCILE_PLAYBOOK" 'FLEET_HEALTH_URL="http://openclaw-fleet-gateway:3000/healthz"'
require_literal "$NETWORK_RECONCILE_PLAYBOOK" 'wget -q -O /dev/null -T 5 $FLEET_HEALTH_URL'
if grep -Eiq 'fleet[^[:space:]]*[[:space:]_]*(network|topology)|fleet.*(difference|dict2items)|FLEET_NETWORK' "$NETWORK_RECONCILE_PLAYBOOK"; then
  fail "$NETWORK_RECONCILE_PLAYBOOK must not constrain Fleet network topology"
fi
echo 'PASS N3 Fleet is probe-only'

# N4: rescue is a non-short-circuit shell path. Reconnect failure is recorded,
# then ID/network/login checks still run and the transaction exits non-zero.
require_literal "$NETWORK_RECONCILE_PLAYBOOK" 'rescue() {'
require_literal "$NETWORK_RECONCILE_PLAYBOOK" 'trap rescue EXIT'
require_literal "$NETWORK_RECONCILE_PLAYBOOK" 'set +e'
rescue_block="$(sed -n '/rescue() {/,/trap rescue EXIT/p' "$NETWORK_RECONCILE_PLAYBOOK")"
[ -n "$rescue_block" ] || fail "$NETWORK_RECONCILE_PLAYBOOK is missing the shell rescue block"
for rescue_literal in \
  'docker network connect "$WEBUI_NETWORK_ID" "$ADMIN_ID"' \
  'reconnect_rc="$?"' \
  'rescue_network_check "$CORE_NETWORK_NAME" "$CORE_NETWORK_ID"' \
  'rescue_network_check "$WEBUI_NETWORK_NAME" "$WEBUI_NETWORK_ID"' \
  'rescue_container_check "$ADMIN_NAME" "$ADMIN_ID" true true' \
  'rescue_container_check "$PROXY_NAME" "$PROXY_ID" true true' \
  'rescue_probe "Admin /login" "$ADMIN_LOGIN_URL"' \
  'rescue_probe "Fleet /healthz" "$FLEET_HEALTH_URL"' \
  'rescue_probe "proxy /login" "$PROXY_LOGIN_URL"' \
  'exit 1'; do
  grep -Fq -- "$rescue_literal" <<<"$rescue_block" || fail "rescue missing: ${rescue_literal}"
done
if grep -Fq 'docker network disconnect' <<<"$rescue_block"; then
  fail "$NETWORK_RECONCILE_PLAYBOOK rescue must not roll back the proxy core attachment"
fi
if grep -Eq '\|\|[[:space:]]*(exit|return)|&&[[:space:]]*(exit|return)' <<<"$rescue_block"; then
  fail "$NETWORK_RECONCILE_PLAYBOOK rescue must not short-circuit after reconnect failure"
fi
echo 'PASS N4 rescue continues checks and exits non-zero'

# N5: only network attachment mutations are allowed; no lifecycle or role path.
if grep -Eq 'community\.docker|docker_(container|network)|^[[:space:]]+(roles|import_playbook|import_role):' "$NETWORK_RECONCILE_PLAYBOOK"; then
  fail "$NETWORK_RECONCILE_PLAYBOOK contains a module, role, or import path"
fi
forbidden_docker_command_re='docker[[:space:]]+((container|image|volume|network|system)[[:space:]]+)?(create|start|restart|recreate|stop|rm|run|pull|build|prune)([[:space:]]|$)'
if grep -Eq "$forbidden_docker_command_re" "$NETWORK_RECONCILE_PLAYBOOK"; then
  fail "$NETWORK_RECONCILE_PLAYBOOK contains a forbidden Docker lifecycle operation"
fi
for forbidden_command in \
  'docker container rm openclaw-admin-console' \
  'docker image pull open-webui:latest' \
  'docker volume prune --force' \
  'docker network create open-webui-net'; do
  if ! printf '%s\n' "$forbidden_command" | grep -Eq "$forbidden_docker_command_re"; then
    fail "Docker denylist regression self-check missed: ${forbidden_command}"
  fi
done
for allowed_command in \
  'docker network inspect open-webui-net' \
  'docker network connect open-webui-net openclaw-webui-proxy' \
  'docker network disconnect open-webui-net openclaw-admin-console'; do
  if printf '%s\n' "$allowed_command" | grep -Eq "$forbidden_docker_command_re"; then
    fail "Docker denylist regression self-check overblocked: ${allowed_command}"
  fi
done
echo 'PASS N5 Docker subcommand denylist regression self-check'
require_literal "$NETWORK_RECONCILE_PLAYBOOK" 'when: ansible_check_mode'
require_literal "$NETWORK_RECONCILE_PLAYBOOK" 'when: not ansible_check_mode'
require_literal "$NETWORK_RECONCILE_PLAYBOOK" 'check_mode: false'
require_literal "$NETWORK_RECONCILE_PLAYBOOK" '/bin/sh -ec'
if grep -Fq 'Config.Env' "$NETWORK_RECONCILE_PLAYBOOK"; then
  fail "$NETWORK_RECONCILE_PLAYBOOK must not inspect container environment"
fi
if grep -Eq 'printf[^\n]*(network_info|container_info|NETWORKS|_ID)' "$NETWORK_RECONCILE_PLAYBOOK"; then
  fail "$NETWORK_RECONCILE_PLAYBOOK must not print inspect data or IDs"
fi
if grep -Fq 'admin_console_join_open_webui_network' "$ROOT_DIR/inventories/mypc/group_vars/mypc.yml"; then
  fail "dead admin_console_join_open_webui_network inventory variable remains"
fi
require_literal "$RUNBOOK" '仅预检'
require_literal "$RUNBOOK" '不是成功证据'
require_literal "$RUNBOOK" '--check'
echo 'PASS N5 fixed literals and network-only lifecycle boundary'

echo 'OK   mypc Open WebUI/Admin network-only reconcile contract is present'
