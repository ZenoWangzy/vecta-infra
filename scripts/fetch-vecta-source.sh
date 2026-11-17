#!/usr/bin/env bash
# Fetch a selected VectA commit from the mypc runner's warm cache, with an
# exact-SHA GitHub fallback. Release callers can also request main's retained
# history from the cache for the previous-Deployment image comparison.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/vecta-main-history.sh"

include_main_history=false
case "${1:-}" in
  "") ;;
  --with-main-history) include_main_history=true ;;
  *)
    echo "usage: bash scripts/fetch-vecta-source.sh [--with-main-history]" >&2
    exit 64
    ;;
esac
if [ "$#" -gt 1 ]; then
  echo "usage: bash scripts/fetch-vecta-source.sh [--with-main-history]" >&2
  exit 64
fi

: "${SOURCE_SHA:?SOURCE_SHA is required}"
SOURCE_BRANCH="${SOURCE_BRANCH:-main}"
export SOURCE_SHA SOURCE_BRANCH
[[ "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]] || {
  echo "source_sha must be a full 40-character lowercase Git SHA" >&2
  exit 1
}
case "$SOURCE_BRANCH" in
  main) ;;
  *) echo "source_branch must be main: $SOURCE_BRANCH" >&2; exit 1 ;;
esac
[ -n "${VECTA_READ_TOKEN:-}" ] || {
  echo "VECTA_READ_TOKEN secret is required" >&2
  exit 1
}
[ ! -e vecta ] || {
  echo "vecta source directory must not exist before fetch" >&2
  exit 1
}
command -v timeout >/dev/null || {
  echo "timeout is required" >&2
  exit 1
}

remote="https://github.com/ZenoWangzy/vecta.git"
cache="/home/github-runner/.cache/vecta-main.git"
target=vecta
umask 077
curl_config="$(mktemp)"
askpass="$(mktemp)"
keep_target=false
cleanup() {
  rm -f -- "$curl_config" "$askpass"
  if [ "$keep_target" != true ]; then
    rm -rf -- "$target"
  fi
}
trap cleanup EXIT
chmod 600 "$curl_config"
chmod 700 "$askpass"
cat > "$askpass" <<'ASKPASS'
#!/bin/sh
case "$1" in
  *Username*) printf '%s\n' x-access-token ;;
  *Password*) printf '%s\n' "$VECTA_READ_TOKEN" ;;
  *) exit 1 ;;
esac
ASKPASS
{
  printf 'header = "Authorization: Bearer %s"\n' "$VECTA_READ_TOKEN"
  printf 'header = "Accept: application/vnd.github+json"\n'
  printf 'retry = 5\nretry-delay = 5\nconnect-timeout = 30\n'
  printf 'speed-limit = 10240\nspeed-time = 60\n'
} > "$curl_config"

comparison_json="$(curl -fsSL --config "$curl_config" \
  "https://api.github.com/repos/ZenoWangzy/vecta/compare/$SOURCE_SHA...$SOURCE_BRANCH?per_page=1")"
merge_base_sha="$(printf '%s' "$comparison_json" | node -e '
  let input = "";
  process.stdin.on("data", chunk => input += chunk);
  process.stdin.on("end", () => {
    const comparison = JSON.parse(input);
    if (!["ahead", "identical"].includes(comparison.status)
        || comparison.base_commit?.sha !== process.env.SOURCE_SHA) {
      console.error("source_sha must remain on VectA main ancestry");
      process.exit(1);
    }
    process.stdout.write(comparison.merge_base_commit?.sha ?? "");
  });
')"
if [ "$merge_base_sha" != "$SOURCE_SHA" ]; then
  echo "source_sha must be an ancestor of the current VectA main HEAD" >&2
  echo "source_sha=$SOURCE_SHA merge_base=$merge_base_sha" >&2
  exit 1
fi

git init "$target" >/dev/null
git -C "$target" remote add origin "$remote"

main_history_loaded=false
if [ "$include_main_history" = true ]; then
  if [ -d "$cache" ]; then
    cache_main=""
    if vecta_main_history_is_complete "$cache" refs/heads/main; then
      cache_main="$(git --git-dir="$cache" rev-parse --verify refs/heads/main 2>/dev/null || true)"
    fi
    if [ -n "$cache_main" ] && timeout 30m git -C "$target" fetch --no-tags "$cache" \
      +refs/heads/main:refs/remotes/origin/main; then
      if git -C "$target" fsck --connectivity-only refs/remotes/origin/main \
        && vecta_main_history_is_complete "$target/.git" refs/remotes/origin/main; then
        main_history_loaded=true
        echo "Using cached VectA main history ($cache_main)"
      else
        echo "Cached VectA main history is incomplete; it will be refreshed from GitHub if needed" >&2
      fi
    else
      echo "VectA main history cache at $cache is stale or incomplete; it will be refreshed from GitHub if needed" >&2
    fi
  else
    echo "VectA main history cache is missing at $cache; it will be fetched from GitHub if needed" >&2
  fi
fi

fetch_with_auth() {
  GIT_HTTP_LOW_SPEED_LIMIT=0 \
    LC_ALL=C \
    GIT_ASKPASS="$askpass" \
    GIT_ASKPASS_REQUIRE=force \
    GIT_TERMINAL_PROMPT=0 \
    timeout 30m git -C "$target" fetch "$@"
}

fetch_exact() {
  local source="$1"
  fetch_with_auth --depth=1 "$source" "$SOURCE_SHA" || return 1
  git -C "$target" fsck --connectivity-only "$SOURCE_SHA"
}

fetch_main_from_github() {
  local attempt shallow
  for attempt in 1 2 3; do
    shallow="$(git -C "$target" rev-parse --is-shallow-repository)"
    local history_args=()
    if [ "$shallow" = true ]; then
      history_args+=(--unshallow)
    fi
    if fetch_with_auth --no-tags \
      "${history_args[@]}" "$remote" +refs/heads/main:refs/remotes/origin/main \
      && git -C "$target" fsck --connectivity-only refs/remotes/origin/main \
      && vecta_main_history_is_complete "$target/.git" refs/remotes/origin/main; then
      echo "Fetched VectA main history from GitHub (runner cache was stale or unavailable)"
      return 0
    fi
    if [ "$attempt" -lt 3 ]; then
      echo "Retrying VectA main history fetch after transient failure"
    fi
  done
  echo "VectA main history fetch from GitHub failed after 3 attempts; the runner cache was stale or unavailable" >&2
  return 1
}

source_loaded=false
if git -C "$target" cat-file -e "$SOURCE_SHA^{commit}" 2>/dev/null; then
  source_loaded=true
fi

cache_has_source=false
if [ -d "$cache" ] \
  && git --git-dir="$cache" cat-file -e "$SOURCE_SHA^{commit}" \
  && git --git-dir="$cache" fsck --connectivity-only "$SOURCE_SHA" >/dev/null 2>&1; then
  cache_has_source=true
fi

if [ "$source_loaded" = false ] && [ "$cache_has_source" = true ]; then
  if fetch_exact "$cache"; then
    source_loaded=true
    echo "Using cached exact VectA source"
  else
    echo "Cached VectA source unusable; falling back to GitHub"
  fi
fi

if [ "$source_loaded" = false ]; then
  cache_tip="not present"
  if [ -d "$cache" ]; then
    cache_tip="$(git --git-dir="$cache" rev-parse refs/heads/main 2>/dev/null || echo unresolvable)"
  fi
  echo "Cache at $cache is not warm for $SOURCE_SHA (cache main = $cache_tip); falling back to a direct exact-SHA fetch from GitHub."
  for attempt in 1 2 3; do
    if fetch_exact "$remote"; then
      source_loaded=true
      break
    fi
    if [ "$attempt" -eq 3 ]; then
      echo "VectA clone failed after 3 attempts" >&2
      exit 1
    fi
    echo "Retrying VectA clone after transient network failure"
  done
fi

test "$source_loaded" = true

if [ "$include_main_history" = true ] \
  && { [ "$main_history_loaded" != true ] \
    || ! git -C "$target" merge-base --is-ancestor "$SOURCE_SHA" refs/remotes/origin/main; }; then
  echo "Cached VectA main history does not contain $SOURCE_SHA; refreshing main from GitHub because the runner cache is stale" >&2
  fetch_main_from_github
  if ! git -C "$target" merge-base --is-ancestor "$SOURCE_SHA" refs/remotes/origin/main; then
    echo "The runner cache was stale or unavailable, and refreshed GitHub main still does not contain source_sha=$SOURCE_SHA" >&2
    exit 1
  fi
fi

git -C "$target" switch --detach "$SOURCE_SHA"
checkout_sha="$(git -C "$target" rev-parse HEAD)"
if [ "$checkout_sha" != "$SOURCE_SHA" ]; then
  echo "cloned VectA checkout does not match source_sha" >&2
  exit 1
fi
if [ -n "$(git -C "$target" status --porcelain --untracked-files=all)" ]; then
  echo "cloned VectA checkout must be clean" >&2
  exit 1
fi
keep_target=true
