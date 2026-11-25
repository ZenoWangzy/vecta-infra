#!/usr/bin/env bash
# Run the original release machinery with three additional fail-closed checks.
set -euo pipefail
bash scripts/check-scoped-release.sh
export SCOPED_BASELINE="$(jq -er '.baseline' "releases/scoped/$SCOPED_MANIFEST.json")"
export SCOPED_SOURCE="${SOURCE_SHA:?}"
export VECTA_REPO_DIR="$PWD/vecta"
scratch="$(mktemp -d)"
driver="$scratch/driver"
cleanup() {
  git -C "$VECTA_REPO_DIR" worktree remove --force "$driver" >/dev/null 2>&1 || true
  rmdir "$scratch" 2>/dev/null || true
}
trap cleanup EXIT
git -C "$VECTA_REPO_DIR" worktree add -q --detach "$driver" "$SCOPED_SOURCE"
git -C "$driver" apply --check "$PWD/releases/scoped/require-exact-release.patch"
git -C "$driver" apply "$PWD/releases/scoped/require-exact-release.patch"
bash "$driver/scripts/release/mypc-release.sh" "$@"
