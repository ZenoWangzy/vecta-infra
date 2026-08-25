#!/usr/bin/env bash
# Fail closed unless a Nexus group tag resolves to the approved OCI digest.
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <nexus-group-tag-ref> <expected-sha256-digest>" >&2
  exit 2
fi

group_ref="$1"
expected_digest="$2"

if ! printf '%s' "$expected_digest" | grep -Eq '^sha256:[a-f0-9]{64}$'; then
  echo "expected digest must be a lowercase sha256 digest" >&2
  exit 2
fi

if ! actual_digest="$(skopeo inspect --no-tags --tls-verify=false \
  --format '{{.Digest}}' "docker://${group_ref}")"; then
  echo "could not inspect a manifest digest from Nexus group: $group_ref" >&2
  exit 1
fi

if ! printf '%s' "$actual_digest" | grep -Eq '^sha256:[a-f0-9]{64}$'; then
  echo "could not read a manifest digest from Nexus group: $group_ref" >&2
  exit 1
fi

if [ "$actual_digest" != "$expected_digest" ]; then
  echo "Nexus digest mismatch for $group_ref: expected=$expected_digest actual=$actual_digest" >&2
  exit 1
fi
