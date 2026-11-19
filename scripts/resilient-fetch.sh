#!/usr/bin/env bash
# Fetch one exact commit from a GitHub repo with a bounded, slow-link-aware retry (mypc release).
# Usage: FETCH_TOKEN=<token> bash scripts/resilient-fetch.sh <repo-url> <sha> <dir> [warm-cache]
# Worst case 12.5 min: 3 x 4 min attempts + 10 s and 20 s backoff. RESILIENT_FETCH_BACKOFF scales the backoff (tests set 0).
# The token is read by the askpass helper only; it is never echoed or put in argv.

set -euo pipefail

repo_url="${1:?repo url is required}"
sha="${2:?sha is required}"
dir="${3:?target dir is required}"
warm_cache="${4:-}"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "sha must be a full 40-character lowercase Git SHA" >&2; exit 64; }
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

git init -q "$dir"
git -C "$dir" config remote.origin.url "$repo_url"
fetched=false
if [ -n "$warm_cache" ] && [ -d "$warm_cache" ] \
  && git -C "$warm_cache" cat-file -e "$sha^{commit}" 2>/dev/null; then
  git -C "$dir" fetch -q --depth=1 "$warm_cache" "$sha"
  fetched=true
  echo "Using warm source cache for $sha"
else
  for attempt in 1 2 3; do
    if GIT_HTTP_LOW_SPEED_LIMIT=1024 GIT_HTTP_LOW_SPEED_TIME=60 \
      GIT_ASKPASS="$askpass" GIT_TERMINAL_PROMPT=0 \
      timeout 4m git -C "$dir" fetch -q --depth=1 origin "$sha"; then
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
