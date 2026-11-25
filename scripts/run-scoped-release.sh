#!/usr/bin/env bash
# Run the original release machinery with three additional fail-closed checks.
set -euo pipefail
bash scripts/check-scoped-release.sh
export SCOPED_BASELINE="$(jq -er '.baseline' "releases/scoped/$SCOPED_MANIFEST.json")"
manifest="releases/scoped/$SCOPED_MANIFEST.json"
export SCOPED_ISSUE="$(jq -er '.release_issue' "$manifest")"
export SCOPED_PR="$(jq -er '.pull_request' "$manifest")"
export SCOPED_UPSTREAM="$(jq -er '.merged_revision' "$manifest")"
export SCOPED_SERVICES="$(jq -er '.services | join(",")' "$manifest")"
export SCOPED_GUARDS="$PWD/scripts/scoped-driver-guards.sh"
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
if [[ "${DRY_RUN:-false}" != true ]]; then
  record="$(GH_TOKEN="${GH_TOKEN:?}" gh api 'repos/ZenoWangzy/vecta/deployments?environment=production&per_page=1')"
  jq -e --arg sha "$SCOPED_SOURCE" --arg services "$SCOPED_SERVICES" '
    .[0] as $d | $d.sha == $sha and all(($services | split(","))[];
      . as $service | ($d.payload.images[$service] | type == "string" and test("^sha256:[a-f0-9]{64}$"))
      and $d.payload.image_revisions[$service] == $sha)' <<< "$record" >/dev/null
  status="$(gh api "repos/ZenoWangzy/vecta/deployments/$(jq -er '.[0].id' <<< "$record")/statuses?per_page=1" --jq '.[0].state')"
  [[ "$status" == success ]] || { echo 'production deployment has no successful status' >&2; exit 75; }
  jq -e --arg sha "$SCOPED_SOURCE" --argjson pr "$SCOPED_PR" '
    (.run.evidence.pr_number | tonumber) == $pr and
    (.run.evidence.release_acceptance | .sha == $sha and .status == "pass" and .exit_code == 0)' \
    "${AGENT_HOME:?}/state/$SCOPED_ISSUE.json" >/dev/null
fi
