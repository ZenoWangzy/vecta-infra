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

if ! manifest_json="$(docker manifest inspect --verbose "$group_ref")"; then
  echo "could not inspect Nexus group manifest: $group_ref" >&2
  exit 1
fi

if ! actual_digest="$(printf '%s' "$manifest_json" | node -e '
  let raw = "";
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", (chunk) => { raw += chunk; });
  process.stdin.on("end", () => {
    try {
      const digest = JSON.parse(raw)?.Descriptor?.digest;
      if (!/^sha256:[a-f0-9]{64}$/.test(digest || "")) process.exit(1);
      process.stdout.write(digest);
    } catch {
      process.exit(1);
    }
  });
')"; then
  echo "could not read a manifest digest from Nexus group: $group_ref" >&2
  exit 1
fi

if [ "$actual_digest" != "$expected_digest" ]; then
  echo "Nexus digest mismatch for $group_ref: expected=$expected_digest actual=$actual_digest" >&2
  exit 1
fi
