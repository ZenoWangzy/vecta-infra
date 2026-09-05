#!/usr/bin/env bash
# Ticket 106 — the only thing on mypc that notices a container going unhealthy
# or restarting. Runs from /etc/crontab every 5 minutes and writes ONE admin
# system alert row into proactive_outbox, in exactly the shape fleet-gateway's
# enqueueAdminAlertIfNeeded already writes (instance_id 'admin',
# trigger_type 'system_alert', channel 'web', channel_uid NULL, max_attempts 6),
# so the existing delivery worker carries it to the platform root without a new
# alert channel, a new table, or a new monitoring stack.
#
# Both signals are EDGE triggered, which is the whole of the dedupe: a container
# alerts when it enters `unhealthy`, not on every cycle it stays there, and a
# restart alerts on a RestartCount that grew since the previous run. Unchanged
# state writes nothing. The first ever run has no previous state, so it can only
# report an already-unhealthy container (once), never a restart delta.
#
# Install: docs/runbooks/gateway-healthchecks-and-watch.md.
set -uo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Overridable so the contract test can drive this against a fake docker and a
# fake psql. Defaults are the production invocations.
DOCKER="${GATEWAY_WATCH_DOCKER:-docker}"
PSQL="${GATEWAY_WATCH_PSQL:-docker exec -i openclaw-postgres psql -U openclaw_poc -d openclaw_poc -v ON_ERROR_STOP=1 -q}"
STATE="${GATEWAY_WATCH_STATE:-/var/lib/gateway-watch/state}"

ids=$($DOCKER ps -q) || { echo "gateway-watch: docker ps failed" >&2; exit 1; }
[ -n "$ids" ] && [ -n "${ids//[[:space:]]/}" ] || exit 0

# One inspect answers both questions. `docker ps --filter health=unhealthy`
# would answer the first one a second time, from a second source, and the two
# could disagree about a container that changed state between the calls.
# shellcheck disable=SC2086
now=$($DOCKER inspect \
  --format '{{.Name}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} {{.RestartCount}}' \
  $ids | sed 's,^/,,' | sort) \
  || { echo "gateway-watch: docker inspect failed" >&2; exit 1; }

alerts=""
while read -r name health count; do
  [ -n "$name" ] || continue
  prev_health=$(awk -v n="$name" '$1 == n { print $2 }' "$STATE" 2>/dev/null)
  prev_count=$(awk -v n="$name" '$1 == n { print $3 }' "$STATE" 2>/dev/null)
  if [ "$health" = "unhealthy" ] && [ "${prev_health:-none}" != "unhealthy" ]; then
    alerts="${alerts}容器 ${name} 健康检查转为 unhealthy（上一轮=${prev_health:-未知}）
"
  fi
  if [ -n "$prev_count" ] && [ "$count" -gt "$prev_count" ] 2>/dev/null; then
    alerts="${alerts}容器 ${name} RestartCount ${prev_count} → ${count}（自上一轮重启 $((count - prev_count)) 次）
"
  fi
done <<EOF
$now
EOF

if [ -n "$alerts" ]; then
  # Container names and restart counts are the only inputs, and Docker allows
  # neither `$` nor `\` in a name; stripping both anyway keeps the dollar-quoted
  # literal below closed no matter what ends up in this text.
  body=$(printf '【系统告警】生产容器状态异常\nsource=gateway-watch.sh\nhost=%s\n%s处理建议=在 mypc 上读 docker ps --filter health=unhealthy 和 docker logs --tail 200 <容器>；网关持续 unhealthy 时先看 /healthz 的 checks 字段（fleet: postgres/redis/litellm/fruitV4；channel: WeCom 回调新鲜度）。' \
    "$(hostname)" "$alerts" | tr -d '\\$')
  # shellcheck disable=SC2086
  printf "INSERT INTO proactive_outbox\n  (instance_id, trigger_type, channel, channel_uid, prompt, max_attempts)\nVALUES ('admin', 'system_alert', 'web', NULL, \$gwwatch\$%s\$gwwatch\$, 6);\n" "$body" \
    | $PSQL \
    || { echo "gateway-watch: alert insert failed, state not advanced" >&2; exit 1; }
fi

mkdir -p "$(dirname "$STATE")" || exit 1
printf '%s\n' "$now" > "$STATE"
