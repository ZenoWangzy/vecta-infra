#!/bin/sh
# Host cron owns scheduling; archives stay beside the container's timing log.
set -eu
case "${1:-}" in
  '') force=0 ;;
  --force) force=1 ;;
  *) echo 'Usage: vecta-upstream-timing-rotate [--force]' >&2; exit 2 ;;
esac
exec 9>/run/lock/vecta-upstream-timing-rotate.lock
flock -n 9 || exit 0
[ "$(docker inspect --format '{{.State.Running}}' openclaw-webui-proxy)" = true ] || exit 0
docker exec openclaw-webui-proxy sh -eu -c '
  log=/var/log/nginx/vecta-upstream-timing.log
  # A restart or interrupted reopen may leave no current file yet.
  if [ ! -f "$log" ]; then nginx -s reopen; exit 0; fi
  [ "$1" = 1 ] || [ "$(stat -c %s "$log")" -ge 33554432 ] || exit 0
  i=5
  while [ "$i" -gt 1 ]; do
    prev=$((i - 1))
    [ ! -f "$log.$prev" ] || mv -f "$log.$prev" "$log.$i"
    i=$prev
  done
  mv "$log" "$log.1"
  if ! nginx -s reopen; then
    # Keep the active writer discoverable for the next cron attempt.
    [ -e "$log" ] || mv "$log.1" "$log"
    exit 1
  fi
' sh "$force"
