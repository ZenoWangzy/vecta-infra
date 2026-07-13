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
SMOKE_CHANNEL_REQUIRED="${SMOKE_CHANNEL_REQUIRED:-auto}"
code="$(curl -s -o /dev/null -w '%{http_code}' "${FLEET_BASE}/healthz" || echo 000)"
[ "$code" = "200" ] || { echo "FAIL Fleet ${FLEET_BASE}/healthz -> $code" >&2; exit 1; }
echo "OK   Fleet /healthz"
code="$(curl -s -o /dev/null -w '%{http_code}' "${OPENWEBUI_BASE}/health" || echo 000)"
[ "$code" = "200" ] || { echo "FAIL OpenWebUI ${OPENWEBUI_BASE}/health -> $code" >&2; exit 1; }
echo "OK   OpenWebUI /health"
code="$(curl -s -o /dev/null -w '%{http_code}' "${OPENWEBUI_BASE}/" || echo 000)"
case "$code" in 2*|3*) ;; *) echo "FAIL OpenWebUI ${OPENWEBUI_BASE}/ -> $code" >&2; exit 1 ;; esac
echo "OK   OpenWebUI / (code=$code)"
code="$(curl -s -o /dev/null -w '%{http_code}' "${OPENWEBUI_BASE}/chat/" || echo 000)"
case "$code" in 2*|3*) ;; *) echo "FAIL OpenWebUI ${OPENWEBUI_BASE}/chat/ -> $code" >&2; exit 1 ;; esac
echo "OK   OpenWebUI /chat/ (code=$code)"
code="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${SERVICE_KEY}" -H "X-Auth-Email: ${AUTH_EMAIL}" "${FLEET_BASE}/v1/models" || echo 000)"
[ "$code" = "200" ] || { echo "FAIL /v1/models -> $code" >&2; exit 1; }
echo "OK   /v1/models (authed)"
chat="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${SERVICE_KEY}" -H "X-Auth-Email: ${AUTH_EMAIL}" -H 'Content-Type: application/json' -d "{\"model\":\"${SMOKE_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"stream\":false}" "${FLEET_BASE}/v1/chat/completions" || echo 000)"
[ "${chat#2}" != "$chat" ] || { echo "FAIL /v1/chat/completions -> $chat" >&2; exit 1; }
echo "OK   /v1/chat/completions (chat_code=$chat)"
if [ -n "${INTERNAL_BASE:-}" ]; then
  c="$(curl -s -o /dev/null -w '%{http_code}' "${INTERNAL_BASE}:8000/healthz" || echo 000)"
  [ "$c" = "200" ] || { echo "FAIL RAG (internal) -> $c" >&2; exit 1; }
  echo "OK   RAG (internal)"

  channel_required="$SMOKE_CHANNEL_REQUIRED"
  if [ "$channel_required" = "auto" ]; then
    channel_required=false
    if [ -n "${WECOM_BOT_ID:-}" ] && [ -n "${WECOM_BOT_SECRET:-}" ]; then
      channel_required=true
    fi
  fi

  case "$channel_required" in
    true|TRUE|1|yes|YES)
      c="$(curl -s -o /dev/null -w '%{http_code}' "${INTERNAL_BASE}:9000/healthz" || echo 000)"
      [ "$c" = "200" ] || { echo "FAIL Channel (internal) -> $c" >&2; exit 1; }
      echo "OK   Channel (internal)"
      ;;
    *)
      echo "SKIP Channel (internal; SMOKE_CHANNEL_REQUIRED=$channel_required)"
      ;;
  esac
else
  echo "SKIP RAG/Channel (internal; set INTERNAL_BASE=http://127.0.0.1)"
fi
