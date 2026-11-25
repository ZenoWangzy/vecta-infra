#!/usr/bin/env bash
# Use the same source checkout and CLI that the live publisher will use.
set -euo pipefail
cd "${1:?verified VectA checkout is required}"
registry=http://127.0.0.1:8081/repository/vecta-npm-proxy/
clean_env=(env -i "HOME=$HOME" "PATH=$PATH" CI=true "npm_config_registry=$registry")
"${clean_env[@]}" pnpm --filter '@vecta/fruit-industry-pack...' install \
  --frozen-lockfile --prod=false --ignore-scripts --prefer-offline
"${clean_env[@]}" pnpm --silent --filter @vecta/fruit-industry-pack \
  platform:publish-v4-workbench -- --dry-run | sed -n '/^{/,$p' | \
  jq -er 'select(.mode == "dry-run" and .manifest.packId == "fruit-v4-workbench") |
    .manifest.version | strings | select(length > 0) | "Verified workbench publisher for " + .'
