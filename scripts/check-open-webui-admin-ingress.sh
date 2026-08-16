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
"$TRANSACTION" --self-test
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
require_absent "$TRANSACTION" '.Config.Env'
require_absent "$TRANSACTION" 'docker compose'
require_absent "$TRANSACTION" 'docker-compose'
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

printf 'OK   Open WebUI ingress and atomic network-reconcile contract is present\n'
