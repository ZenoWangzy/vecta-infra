#!/usr/bin/env bash
# Cross-session, cross-process, cross-machine-independent production deploy
# lock for mypc. See docs/runbooks/production-deploy-lock.md for the full
# contract, the staleness rule, and why this is a single global lock rather
# than one per container.
#
# Designed to be streamed over stdin to `mypc` from the exact checkout that
# holds it, never installed as a standing copy there — there is then nothing
# on mypc that can go stale relative to this file:
#   ssh mypc 'bash -s -- acquire --holder "..." --session "..." \
#     --containers "openclaw-fleet-gateway,openclaw-channel-gateway"' \
#     < scripts/deploy-lock.sh
#
# The lock IS a live process, not a timestamp. `acquire` opens the lock file
# on fd 200, takes a non-blocking flock(1) on it, and — only once held —
# `exec`s into `sleep "$ttl"` without closing fd 200. That process is now the
# lock: the kernel releases the flock the instant it exits, for any reason
# (explicit `release`, the ttl elapsing, the holding ssh session dying, or
# mypc itself rebooting). A second `acquire` is therefore never guessing from
# a timestamp whether the first holder is still working — it asks the kernel
# for the same fd-backed lock and gets an authoritative yes/no.
set -euo pipefail

LOCK_DIR="${DEPLOY_LOCK_DIR:-/data/ocee/locks}"
LOCK_FILE="$LOCK_DIR/production-deploy.lock"
META_FILE="$LOCK_DIR/production-deploy.meta"
# Ceiling for "forgot to release": generous next to any single runbook
# window (the gateway promotion runbook's own worked example ran well under
# an hour end to end), short enough that a truly forgotten lock is gone the
# same day with nobody having to do anything.
DEFAULT_TTL_SECONDS=7200

usage() {
  cat >&2 <<'EOF'
usage:
  deploy-lock.sh acquire --holder NAME --session ID_OR_URL --containers "a,b,c" [--ttl-seconds N]
  deploy-lock.sh status
  deploy-lock.sh release
EOF
  exit 2
}

cmd="${1:-}"
[ -n "$cmd" ] && shift || true

case "$cmd" in
  acquire)
    holder=""
    session=""
    containers=""
    ttl="$DEFAULT_TTL_SECONDS"
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --holder) holder="$2"; shift 2 ;;
        --session) session="$2"; shift 2 ;;
        --containers) containers="$2"; shift 2 ;;
        --ttl-seconds) ttl="$2"; shift 2 ;;
        *) usage ;;
      esac
    done
    if [ -z "$holder" ] || [ -z "$session" ] || [ -z "$containers" ]; then
      usage
    fi

    mkdir -p "$LOCK_DIR"
    exec 200>>"$LOCK_FILE"
    if ! flock -n 200; then
      echo "DENIED: production deploy lock is already held." >&2
      echo "--- current holder (docs/runbooks/production-deploy-lock.md explains how to judge staleness) ---" >&2
      cat "$META_FILE" >&2 2>/dev/null || echo "(no metadata file — held by a pre-lock process)" >&2
      exit 1
    fi

    # We hold the exclusive lock on fd 200. Record who/what/when while we
    # still hold it, so a denied second acquirer always sees a consistent
    # record and never a half-written one.
    acquired_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    {
      printf 'holder=%s\n' "$holder"
      printf 'session=%s\n' "$session"
      printf 'containers=%s\n' "$containers"
      printf 'acquired_at=%s\n' "$acquired_at"
      printf 'ttl_seconds=%s\n' "$ttl"
      printf 'pid=%s\n' "$BASHPID"
      printf 'host=%s\n' "$(hostname)"
    } > "$META_FILE"

    echo "ACQUIRED holder=$holder pid=$BASHPID acquired_at=$acquired_at ttl_seconds=$ttl"
    # Do not close fd 200: exec into the process that IS the lock for the
    # rest of this ssh session's life (or up to $ttl, whichever ends first).
    exec sleep "$ttl"
    ;;

  status)
    # Public contract, not just a debug helper: exit 0/non-zero here is also
    # what ticket 139's per-employee refresh-config procedure gates on as a
    # read-only, non-acquiring pre-flight check (production-deploy-lock.md,
    # "Judgment call: should refresh-config take this lock?"). Keep this
    # exit-code meaning stable.
    if [ ! -f "$LOCK_FILE" ]; then
      echo "FREE (lock file does not exist yet — never acquired)"
      exit 0
    fi
    exec 201<>"$LOCK_FILE"
    if flock -n 201; then
      flock -u 201
      echo "FREE (kernel confirms no live holder; a prior holder's metadata, if any, is stale)"
      cat "$META_FILE" 2>/dev/null || true
      exit 0
    fi
    echo "HELD"
    cat "$META_FILE" 2>/dev/null || echo "(no metadata file — held by a pre-lock process)"
    exit 1
    ;;

  release)
    if [ ! -f "$META_FILE" ]; then
      echo "nothing to release (no metadata file at $META_FILE)"
      exit 0
    fi
    pid="$(sed -n 's/^pid=//p' "$META_FILE" | head -1)"
    if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      # Give the kernel a moment to tear the process down and drop the flock.
      for _ in 1 2 3 4 5; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.2
      done
    fi
    rm -f "$LOCK_FILE" "$META_FILE"
    echo "RELEASED"
    ;;

  *)
    usage
    ;;
esac
