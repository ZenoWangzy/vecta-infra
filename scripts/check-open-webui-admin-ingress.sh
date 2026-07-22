#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROXY_TEMPLATE="${ROOT_DIR}/roles/open-webui/templates/nginx.conf.j2"
HOST_TEMPLATE="${ROOT_DIR}/roles/open-webui/templates/host-nginx-site.conf.j2"

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

reject_duplicate_exact_admin_location() {
  local file="$1"
  local count
  count="$(grep -F 'location = /admin {' "$file" | wc -l | tr -d ' ')"
  [ "$count" = "1" ] || fail "${file} must contain exactly one location = /admin block, found ${count}"
}

require_file "$PROXY_TEMPLATE"
require_file "$HOST_TEMPLATE"

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

reject_duplicate_exact_admin_location "$PROXY_TEMPLATE"
reject_duplicate_exact_admin_location "$HOST_TEMPLATE"

echo 'OK   Open WebUI /admin ingress contract is infra-owned and present'
