#!/usr/bin/env bash
# Phase 3 — minimal vtest smoke. PUBLIC path via nginx (no :port on https).
# Fleet /healthz + OpenWebUI /health + authed /v1/models + 1 low-cost chat.
# RAG/Channel health are INTERNAL (127.0.0.1:port); covered only if INTERNAL_BASE set.
set -euo pipefail
BASE="${BASE:-https://vtest.matrix-ai.com.cn}"
FLEET_BASE="${FLEET_BASE:-$BASE}"
OPENWEBUI_BASE="${OPENWEBUI_BASE:-$BASE}"
SERVICE_KEY="${SERVICE_KEY:?SERVICE_KEY required}"
AUTH_EMAIL="${AUTH_EMAIL:-IT001@openclaw.internal}"
SMOKE_MODEL="${SMOKE_MODEL:-openclaw-agent}"
code="$(curl -s -o /dev/null -w '%{http_code}' "${FLEET_BASE}/healthz" || echo 000)"
[ "$code" = "200" ] || { echo "FAIL Fleet ${FLEET_BASE}/healthz -> $code" >&2; exit 1; }
echo "OK   Fleet /healthz"
code="$(curl -s -o /dev/null -w '%{http_code}' "${OPENWEBUI_BASE}/health" || echo 000)"
[ "$code" = "200" ] || { echo "FAIL OpenWebUI ${OPENWEBUI_BASE}/health -> $code" >&2; exit 1; }
echo "OK   OpenWebUI /health"
code="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${SERVICE_KEY}" -H "X-Auth-Email: ${AUTH_EMAIL}" "${FLEET_BASE}/v1/models" || echo 000)"
[ "$code" = "200" ] || { echo "FAIL /v1/models -> $code" >&2; exit 1; }
echo "OK   /v1/models (authed)"
chat="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${SERVICE_KEY}" -H "X-Auth-Email: ${AUTH_EMAIL}" -H 'Content-Type: application/json' -d "{\"model\":\"${SMOKE_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"stream\":false}" "${FLEET_BASE}/v1/chat/completions" || echo 000)"
[ "${chat#2}" != "$chat" ] || { echo "FAIL /v1/chat/completions -> $chat" >&2; exit 1; }
echo "OK   /v1/chat/completions (chat_code=$chat)"
if [ -n "${INTERNAL_BASE:-}" ]; then
  for spec in "8000/healthz:RAG" "9000/healthz:Channel"; do
    url="${spec%%:*}"; name="${spec##*:}"
    c="$(curl -s -o /dev/null -w '%{http_code}' "${INTERNAL_BASE}:${url}" || echo 000)"
    [ "$c" = "200" ] || { echo "FAIL $name (internal) -> $c" >&2; exit 1; }
    echo "OK   $name (internal)"
  done
else
  echo "SKIP RAG/Channel (internal; set INTERNAL_BASE=http://127.0.0.1)"
fi
