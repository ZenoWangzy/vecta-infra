#!/usr/bin/env bash
# Invoked only for an explicit scoped dispatch from the repository's main.
set -euo pipefail
[[ "${SCOPED_MANIFEST:-}" =~ ^[a-z0-9][a-z0-9-]*$ ]] || { echo 'invalid scope name' >&2; exit 64; }
manifest="releases/scoped/$SCOPED_MANIFEST.json"
scope_base="$(jq -er '.baseline' "$manifest")"
# An in-progress/failed newest deployment leaves the baseline ambiguous. Fail closed.
live_record="$(GH_TOKEN="${VECTA_READ_TOKEN:?}" gh api 'repos/ZenoWangzy/vecta/deployments?environment=production&per_page=1')"
live_id="$(jq -er '.[0].id | numbers' <<< "$live_record")"
live_sha="$(jq -er '.[0].sha' <<< "$live_record")"
live_status="$(GH_TOKEN="$VECTA_READ_TOKEN" gh api "repos/ZenoWangzy/vecta/deployments/$live_id/statuses?per_page=1" --jq '.[0].state')"
[[ "$live_status" == success && "$live_sha" == "$scope_base" ]] || {
  echo "production deployment $live_id ($live_sha, $live_status) differs from approved baseline" >&2; exit 75;
}
pr="$(jq -er '.pull_request | numbers' "$manifest")"
merge="$(jq -er '.merged_revision' "$manifest")"
pr_record="$(GH_TOKEN="${VECTA_READ_TOKEN:?}" gh api "repos/ZenoWangzy/vecta/pulls/$pr")"
jq -e --arg sha "$merge" '.merged and .base.ref == "main" and .merge_commit_sha == $sha' <<< "$pr_record" >/dev/null
candidate="$(python3 scripts/scoped-release-source.py --repo vecta --manifest "$manifest" --production-baseline "$live_sha")"
[[ "$candidate" == "${SOURCE_SHA:?}" && "$(git -C vecta rev-parse HEAD)" == "$candidate" ]] || {
  echo 'selected source or checkout differs from approved patch on production' >&2; exit 65;
}
mapfile -t services < <(jq -r '.services[]' "$manifest")
expected="$(IFS=,; echo "${services[*]}")"
mapped="$(REPO_DIR="$PWD/vecta" node vecta/scripts/release/changed-images.mjs "$scope_base" "$candidate")"
[[ "$expected" == "${RELEASE_SERVICES:-}" && "$mapped" == "$(printf '%s\n' "${services[@]}")" ]] || {
  echo 'service selection differs from scoped production image dependency closure' >&2; exit 65;
}
# Omitting --vecta-checkout requires successful Postsubmit for this exact commit.
python3 scripts/verify-vecta-postsubmit.py --repo ZenoWangzy/vecta --sha "$candidate" --branch main
echo "Verified PR #$pr, baseline $scope_base, candidate $candidate, services $expected"
