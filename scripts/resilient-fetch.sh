#!/usr/bin/env bash
# Fetch one exact commit from a GitHub repo with a bounded, slow-link-aware retry (mypc release).
# Usage: FETCH_TOKEN=<token> bash scripts/resilient-fetch.sh <repo-url> <sha> <dir> [warm-cache]
#        FETCH_TOKEN=<token> bash scripts/resilient-fetch.sh --select-route <repo-url>   (prints the chosen proxy URL, empty = direct)
# Route: before a GitHub fetch, one ls-remote probe (15 s cap) per route, in order: local squid bridge 127.0.0.1:3129,
# VPS tinyproxy 10.67.0.1:8888 (slow, fallback), then direct. The first that answers is used for this command only
# (git -c http.proxy), never written to a gitconfig. mypc direct git smart-HTTP stalls while github.com still answers (2026-11-20).
# GitHub steps (probe and fetch) run with GIT_CONFIG_GLOBAL/SYSTEM=/dev/null: github-runner's ~/.gitconfig includeIf rewrites
# the zenoforge and vecta-infra URLs to hand-fed local mirrors that lack new pins ("not our ref", run 44660078292).
# The warm-cache fetch is a local path and keeps the normal config.
# This file is downloaded alone to bootstrap the infra checkout, so it must not source sibling scripts.
# Worst case 13 min: 2 x 15 s probes + 3 x 4 min attempts + 10 s and 20 s backoff. RESILIENT_FETCH_BACKOFF scales the backoff (tests set 0).
# The token is read by the askpass helper only; it is never echoed or put in argv.

set -euo pipefail

select_route_only=false
if [ "${1:-}" = --select-route ]; then
  select_route_only=true
  shift
fi
repo_url="${1:?repo url is required}"
if [ "$select_route_only" = false ]; then
  sha="${2:?sha is required}"
  dir="${3:?target dir is required}"
  warm_cache="${4:-}"
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "sha must be a full 40-character lowercase Git SHA" >&2; exit 64; }
fi
: "${FETCH_TOKEN:?FETCH_TOKEN is required}"
command -v timeout >/dev/null || { echo "timeout is required" >&2; exit 1; }

askpass="$(mktemp)"
trap 'rm -f "$askpass"' EXIT
chmod 700 "$askpass"
cat > "$askpass" <<'ASKPASS'
#!/bin/sh
case "$1" in
  *Username*) printf '%s\n' x-access-token ;;
  *Password*) printf '%s\n' "$FETCH_TOKEN" ;;
  *) exit 1 ;;
esac
ASKPASS

egress_args=()
select_route() {
  case "$repo_url" in https://*|http://*) ;; *) return 0 ;; esac
  local proxy
  for proxy in http://127.0.0.1:3129 http://10.67.0.1:8888; do
    if GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_ASKPASS="$askpass" GIT_TERMINAL_PROMPT=0 timeout 15 git -c "http.proxy=$proxy" ls-remote "$repo_url" HEAD >/dev/null 2>&1; then
      egress_args=(-c "http.proxy=$proxy")
      echo "git route: proxy ${proxy#*://}" >&2
      return 0
    fi
    echo "git route: proxy ${proxy#*://} did not answer" >&2
  done
  echo "git route: direct (no proxy answered)" >&2
}

if [ "$select_route_only" = true ]; then
  select_route
  [ "${#egress_args[@]}" -eq 0 ] || printf '%s\n' "${egress_args[1]#http.proxy=}"
  exit 0
fi

git init -q "$dir"
git -C "$dir" config remote.origin.url "$repo_url"
fetched=false
# The cache fetch sits in the `if` condition so a cache with the commit but a missing tree or blob
# (cat-file passes, fetch fails) falls through to GitHub instead of exiting under `set -e`.
if [ -n "$warm_cache" ] && [ -d "$warm_cache" ] \
  && git -C "$warm_cache" cat-file -e "$sha^{commit}" 2>/dev/null \
  && git -C "$dir" fetch -q --depth=1 "$warm_cache" "$sha"; then
  fetched=true
  echo "Using warm source cache for $sha"
else
  [ -z "$warm_cache" ] || echo "warm source cache unusable for $sha; fetching from $repo_url" >&2
  select_route
  for attempt in 1 2 3; do
    if GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null \
      GIT_HTTP_LOW_SPEED_LIMIT=1024 GIT_HTTP_LOW_SPEED_TIME=60 \
      GIT_ASKPASS="$askpass" GIT_TERMINAL_PROMPT=0 \
      timeout 4m git "${egress_args[@]}" -C "$dir" fetch -q --depth=1 origin "$sha"; then
      fetched=true
      break
    fi
    echo "fetch attempt $attempt failed: $repo_url $sha" >&2
    [ "$attempt" -eq 3 ] || sleep $((attempt * ${RESILIENT_FETCH_BACKOFF:-10}))
  done
fi
[ "$fetched" = true ] || { echo "fetch $repo_url $sha failed after 3 attempts" >&2; exit 1; }
git -C "$dir" checkout -q --detach --force "$sha"
test "$(git -C "$dir" rev-parse HEAD)" = "$sha"
[ -z "$(git -C "$dir" status --porcelain --untracked-files=all)" ]
