#!/usr/bin/env bash
# Ticket 143: keep /home/github-runner/.cache/vecta-main.git hot so
# build-mypc-images.yml's "Download selected VectA source" step never has to
# fall back to a cold exact-SHA fetch over a link that kills bulk transfers.
#
# Two-hop relay, proven manually in production window 5 (2026-09-07):
#   GitHub (SSH, already works, already authenticated) -> /data/ocee (full
#   history, root-owned, this host's production checkout) -> the runner's
#   own full-history cache (github-runner-owned, local file:// fetch, no auth).
# Keep blobs in the cache: the build workflow's depth-1 local fetch asks for a
# complete selected tree, and upload-pack cannot serve promised blobs from a
# filtered bare cache on this local path. The VectA pack is about 276 MiB here.
# Both hops are `fetch` only. Nothing here ever touches a working tree,
# checks out a branch, or resets anything -- /data/ocee's checked-out state
# is not this script's concern and must not be disturbed.
#
# Run as root (needs to fetch into /data/ocee and sudo -u github-runner for
# the second hop). Intended to run on a short interval via systemd timer,
# not per-build -- the whole point is that a build never has to wait on this.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/vecta-main-history.sh"

OCEE=/data/ocee
CACHE=/home/github-runner/.cache/vecta-main.git
UPSTREAM_TIMEOUT=120 # seconds; fetching /data/ocee from GitHub is incremental
CACHE_TIMEOUT=1800 # seconds; the first unshallow fetch copies main's history

log() { printf '%s [warm-vecta-source-cache] %s\n' "$(date -u +%FT%TZ)" "$1"; }

log_cache_size() {
  local size=unavailable
  if [ -d "$CACHE" ]; then
    size="$(du -sh "$CACHE" 2>/dev/null | cut -f1)" || size=unavailable
  fi
  log "runner cache size: ${size:-unavailable}"
}
trap log_cache_size EXIT

if [ ! -d "$OCEE/.git" ]; then
  log "FATAL: $OCEE is not a git checkout -- not this script's job to bootstrap it, fix separately"
  exit 1
fi

log "hop 1: fetching $OCEE forward from its own origin (SSH, already authenticated)"
if ! timeout "$UPSTREAM_TIMEOUT" git -C "$OCEE" fetch origin main --quiet; then
  log "hop 1 FAILED (timeout or network) -- cache stays at its previous point, not fatal, next timer tick retries"
  exit 1
fi
ocee_tip="$(git -C "$OCEE" rev-parse refs/remotes/origin/main)"
log "hop 1 ok: /data/ocee/origin/main = $ocee_tip"
ocee_shallow="$(git -C "$OCEE" rev-parse --is-shallow-repository)"
if [ "$ocee_shallow" != false ]; then
  log "FATAL: $OCEE is shallow; it cannot warm the runner's full main history"
  exit 1
fi

mkdir -p "$(dirname "$CACHE")"
if [ ! -d "$CACHE" ]; then
  log "runner cache missing at $CACHE -- bootstrapping a full-history bare repository"
  sudo -u github-runner git init --bare "$CACHE" --quiet
  chown -R github-runner:github-runner "$CACHE"
fi

# Cross-user local fetch trips git's dubious-ownership guard the first time;
# this is config-only, never touches objects or a tree. `--add` on its own is
# NOT idempotent -- it appends every run, growing github-runner's shared
# ~/.gitconfig without bound (ticket 136: a 10-minute timer, forever). Check
# before adding so a second run leaves the file unchanged.
if ! sudo -u github-runner git config --global --get-all safe.directory 2>/dev/null \
  | grep -qxF "$OCEE/.git"; then
  sudo -u github-runner git config --global --add safe.directory "$OCEE/.git" 2>/dev/null || true
fi

cache_shallow="$(sudo -u github-runner git --git-dir="$CACHE" rev-parse --is-shallow-repository)"
history_args=()
if [ "$cache_shallow" = true ]; then
  log "runner cache is shallow; fetching full main history for the first time"
  history_args+=(--unshallow)
fi

log "hop 2: fetching runner cache from $OCEE locally (no auth, no network)"
if ! timeout "$CACHE_TIMEOUT" sudo -u github-runner git --git-dir="$CACHE" \
  fetch "${history_args[@]}" --force --quiet "file://$OCEE/.git" \
  "refs/remotes/origin/main:refs/heads/main"; then
  log "hop 2 FAILED -- unexpected, hop 1 succeeded and this is a local fetch; investigate, don't just retry blindly"
  exit 1
fi

cache_tip="$(sudo -u github-runner git --git-dir="$CACHE" rev-parse refs/heads/main)"
if [ "$cache_tip" != "$ocee_tip" ]; then
  log "FATAL: cache tip ($cache_tip) does not match /data/ocee tip ($ocee_tip) after a successful fetch -- should be impossible, investigate"
  exit 1
fi
if ! sudo -u github-runner bash -c 'source "$1" && vecta_main_history_is_complete "$2" refs/heads/main' \
  _ "$SCRIPT_DIR/vecta-main-history.sh" "$CACHE"; then
  log "FATAL: cache main history is incomplete after a successful fetch"
  exit 1
fi
sudo -u github-runner git --git-dir="$CACHE" gc --auto
log "cache warm: refs/heads/main = $cache_tip"
