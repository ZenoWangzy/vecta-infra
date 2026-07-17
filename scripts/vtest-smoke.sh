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
SMOKE_RETRIES="${SMOKE_RETRIES:-30}"
SMOKE_RETRY_DELAY_SECONDS="${SMOKE_RETRY_DELAY_SECONDS:-5}"

wait_code() {
  name="$1"
  url="$2"
  expected="$3"
  attempt=1
  code=000
  while [ "$attempt" -le "$SMOKE_RETRIES" ]; do
    code="$(curl -s -o /dev/null -w '%{http_code}' "$url" || echo 000)"
    [ "$code" = "$expected" ] && return 0
    if [ "$attempt" -lt "$SMOKE_RETRIES" ]; then
      sleep "$SMOKE_RETRY_DELAY_SECONDS"
    fi
    attempt=$((attempt + 1))
  done
  echo "FAIL ${name} ${url} -> $code" >&2
  return 1
}

wait_reachable() {
  name="$1"
  url="$2"
  attempt=1
  code=000
  while [ "$attempt" -le "$SMOKE_RETRIES" ]; do
    code="$(curl -s -o /dev/null -w '%{http_code}' "$url" || echo 000)"
    case "$code" in 2*|3*) return 0 ;; esac
    if [ "$attempt" -lt "$SMOKE_RETRIES" ]; then
      sleep "$SMOKE_RETRY_DELAY_SECONDS"
    fi
    attempt=$((attempt + 1))
  done
  echo "FAIL ${name} ${url} -> $code" >&2
  return 1
}

check_container_env() {
  container="$1"
  env_name="$2"
  if ! docker exec "$container" sh -lc "test -n \"\$(printenv ${env_name})\""; then
    echo "FAIL ${container} missing non-empty ${env_name}" >&2
    return 1
  fi
  echo "OK   ${container} ${env_name} is set"
}

check_rag_admin_boundary() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "SKIP RAG admin boundary (docker unavailable)"
    return 0
  fi
  if ! docker inspect openclaw-fleet-gateway openclaw-rag-service >/dev/null 2>&1; then
    echo "SKIP RAG admin boundary (containers unavailable)"
    return 0
  fi

  check_container_env openclaw-fleet-gateway RAG_ADMIN_TOKEN
  check_container_env openclaw-rag-service ADMIN_TOKEN
  check_container_env openclaw-rag-service KNOWLEDGE_DIR
  check_container_env openclaw-rag-service HF_HOME
  check_container_env openclaw-rag-service TRANSFORMERS_CACHE
  check_container_env openclaw-rag-service HF_ENDPOINT

  rag_admin_token="$(docker exec openclaw-fleet-gateway printenv RAG_ADMIN_TOKEN)"
  rag_internal_token="$(docker exec openclaw-fleet-gateway printenv RAG_INTERNAL_TOKEN || true)"
  rag_admin_url="${RAG_BASE:-${INTERNAL_BASE}:8000}/admin/status"
  rag_admin_code="$(curl -s -o /dev/null -w '%{http_code}' \
    -H "x-admin-token: ${rag_admin_token}" \
    -H "x-internal-token: ${rag_internal_token}" \
    "$rag_admin_url" || echo 000)"
  [ "$rag_admin_code" = "200" ] || { echo "FAIL RAG admin boundary -> $rag_admin_code" >&2; exit 1; }
  echo "OK   RAG admin boundary"
}

wait_code Fleet "${FLEET_BASE}/healthz" 200
echo "OK   Fleet /healthz"
wait_code OpenWebUI "${OPENWEBUI_BASE}/health" 200
echo "OK   OpenWebUI /health"
wait_reachable OpenWebUI "${OPENWEBUI_BASE}/"
echo "OK   OpenWebUI / (code=$code)"
wait_reachable OpenWebUI "${OPENWEBUI_BASE}/chat/"
echo "OK   OpenWebUI /chat/ (code=$code)"
code="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${SERVICE_KEY}" -H "X-Auth-Email: ${AUTH_EMAIL}" "${FLEET_BASE}/v1/models" || echo 000)"
[ "$code" = "200" ] || { echo "FAIL /v1/models -> $code" >&2; exit 1; }
echo "OK   /v1/models (authed)"
chat="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${SERVICE_KEY}" -H "X-Auth-Email: ${AUTH_EMAIL}" -H 'Content-Type: application/json' -d "{\"model\":\"${SMOKE_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"stream\":false}" "${FLEET_BASE}/v1/chat/completions" || echo 000)"
[ "${chat#2}" != "$chat" ] || { echo "FAIL /v1/chat/completions -> $chat" >&2; exit 1; }
echo "OK   /v1/chat/completions (chat_code=$chat)"
if [ -n "${INTERNAL_BASE:-}" ]; then
  wait_code "RAG (internal)" "${INTERNAL_BASE}:8000/healthz" 200
  echo "OK   RAG (internal)"
  wait_code "Fruit industry pack" "${INTERNAL_BASE}:8002/healthz" 200
  echo "OK   Fruit industry pack"
  check_rag_admin_boundary

  channel_required="$SMOKE_CHANNEL_REQUIRED"
  if [ "$channel_required" = "auto" ]; then
    channel_required=false
    if [ -n "${WECOM_BOT_ID:-}" ] && [ -n "${WECOM_BOT_SECRET:-}" ]; then
      channel_required=true
    fi
  fi

  case "$channel_required" in
    true|TRUE|1|yes|YES)
      wait_code "Channel (internal)" "${INTERNAL_BASE}:9000/healthz" 200
      echo "OK   Channel (internal)"
      ;;
    *)
      echo "SKIP Channel (internal; SMOKE_CHANNEL_REQUIRED=$channel_required)"
      ;;
  esac
else
  echo "SKIP RAG/Channel (internal; set INTERNAL_BASE=http://127.0.0.1)"
fi
