#!/usr/bin/env bash
# Best-effort vtest runner cleanup after Nexus-backed deploys.
# Keeps recent Docker cache for faster CI while bounding stale build state.
set -euo pipefail

prune_until="${VTEST_DOCKER_CACHE_PRUNE_UNTIL:-48h}"

if [ "$prune_until" = "never" ]; then
  echo "SKIP Docker build-state prune (VTEST_DOCKER_CACHE_PRUNE_UNTIL=never)"
  exit 0
fi

echo "Prune dangling images older than ${prune_until}"
if ! docker image prune --force --filter "until=${prune_until}"; then
  echo "WARN Docker dangling-image prune failed; continuing" >&2
fi

echo "Prune build cache older than ${prune_until}"
if ! docker builder prune --force --filter "until=${prune_until}"; then
  echo "WARN Docker build-cache prune failed; continuing" >&2
fi
