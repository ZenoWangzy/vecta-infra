#!/usr/bin/env bash
# Sync approved production image inputs into the mypc-local Nexus registry.
# This script only pulls/tags/pushes images. It never starts containers, changes
# volumes, or prunes data.
set -euo pipefail

registry="${NEXUS_DOCKER_REGISTRY:-127.0.0.1:8082}"
group_registry="${NEXUS_DOCKER_GROUP_REGISTRY:-127.0.0.1:8083}"
sync_only="${NEXUS_SYNC_ONLY:-}"
matched_target_paths=","
mode="dry-run"

if [ "${1:-}" = "--execute" ]; then
  mode="execute"
elif [ "${1:-}" != "" ] && [ "${1:-}" != "--dry-run" ]; then
  echo "usage: $0 [--dry-run|--execute]" >&2
  exit 2
fi

log() {
  printf '[mypc-nexus-sync] %s\n' "$*"
}

run() {
  if [ "$mode" = "execute" ]; then
    "$@"
  else
    printf '+ '
    printf '%q ' "$@"
    printf '\n'
  fi
}

push_image() {
  target="$1"
  if [ "$mode" != "execute" ]; then
    run docker push "$target"
    return 0
  fi

  if docker push "$target"; then
    return 0
  fi

  if command -v skopeo >/dev/null 2>&1; then
    log "docker push failed; retry local image copy with skopeo"
    skopeo copy --dest-tls-verify=false "docker-daemon:${target}" "docker://${target}"
  else
    return 1
  fi
}

image_present() {
  docker image inspect "$1" >/dev/null 2>&1
}

target_selected() {
  [ -z "$sync_only" ] && return 0

  local selected
  local selected_targets
  IFS=',' read -r -a selected_targets <<< "$sync_only"
  for selected in "${selected_targets[@]}"; do
    if [ "$selected" = "$1" ]; then
      matched_target_paths+="$1,"
      return 0
    fi
  done
  return 1
}

sync_image() {
  target_path="$1"
  sources="$2"
  local_only="${3:-false}"
  target="${registry}/${target_path}"

  if ! target_selected "$target_path"; then
    log "skip ${target}; not selected"
    return 0
  fi

  log "target ${target}"
  IFS=';' read -r -a source_list <<< "$sources"
  selected_source=""
  for source in "${source_list[@]}"; do
    if image_present "$source"; then
      selected_source="$source"
      break
    fi
  done

  if [ -z "$selected_source" ]; then
    if [ "$local_only" = "true" ]; then
      if [ "$mode" != "execute" ]; then
        log "local-only source required before execute: ${sources}"
        return 0
      else
        echo "local-only source image missing for ${target}: ${sources}" >&2
        return 1
      fi
    else
      selected_source="${source_list[0]}"
      log "local source missing; pull ${selected_source}"
      run docker pull "$selected_source"
    fi
  else
    log "use local source ${selected_source}"
  fi

  run docker tag "$selected_source" "$target"
  push_image "$target"
}

sync_manifest_index() {
  local target_digest
  target_path="$1"
  source_repository="$2"
  source_tag="$3"
  source_digest="$4"
  target="${registry}/${target_path}"
  source_tag_ref="${source_repository}:${source_tag}"
  source_audit_ref="${source_tag_ref}@${source_digest}"
  source_digest_ref="${source_repository}@${source_digest}"

  if ! target_selected "$target_path"; then
    log "skip ${target}; not selected"
    return 0
  fi

  if [ -z "$source_repository" ] || [ -z "$source_tag" ] \
    || ! printf '%s' "$source_digest" | grep -Eq '^sha256:[a-f0-9]{64}$'; then
    echo "manifest-index source must supply repository, tag, and sha256 digest" >&2
    return 2
  fi

  if [ "$mode" = "execute" ] && ! command -v skopeo >/dev/null 2>&1; then
    echo "skopeo is required to preserve the manifest index for ${target}" >&2
    return 1
  fi

  log "target ${target}"
  # Keep the reviewed tag in the audit line, but feed skopeo a digest-only
  # source reference: skopeo rejects Docker references that include both.
  log "manifest index ${source_audit_ref} -> ${target}"
  if [ "$mode" = "execute" ]; then
    if target_digest="$(skopeo inspect --no-tags --tls-verify=false \
      --format '{{.Digest}}' "docker://${target}" 2>/dev/null)"; then
      if [ "$target_digest" = "$source_digest" ]; then
        log "skip ${target}; manifest digest already matches ${target_digest}"
        return 0
      fi
      log "target digest mismatch; expected ${source_digest}, found ${target_digest}; copying"
    else
      log "target digest query failed; copying ${target}"
    fi
  fi

  run skopeo copy --all --preserve-digests \
    --src-tls-verify=false --dest-tls-verify=false \
    "docker://${source_digest_ref}" "docker://${target}"

  if [ "$mode" = "execute" ]; then
    if ! target_digest="$(skopeo inspect --no-tags --tls-verify=false \
      --format '{{.Digest}}' "docker://${target}" 2>/dev/null)"; then
      echo "could not inspect target digest after copy: ${target}" >&2
      return 1
    fi
    if [ "$target_digest" != "$source_digest" ]; then
      echo "target digest mismatch after copy for ${target}: expected=${source_digest} actual=${target_digest}" >&2
      return 1
    fi
    log "verified ${target} manifest digest ${target_digest}"
  fi
}

# Preserve the reviewed bootstrap default for existing unscoped manual syncs.
# A production workflow always supplies its verified candidate's complete ref.
hermes_target='nousresearch/hermes-agent:v0.21.6-9774f4f3'
hermes_source_tag='v0.21.6'
hermes_digest='sha256:9774f4f39a9bb8c2f68ce728ed5e99ddbad282163be56764afacf88ed952b784'
if [ "${NEXUS_SYNC_HERMES_REF+x}" = x ]; then
  if [ "$registry" != '127.0.0.1:8082' ] || [ "$group_registry" != '127.0.0.1:8083' ]; then
    echo 'candidate Hermes sync requires the production local Nexus registries' >&2
    exit 2
  fi
  hermes_ref_pattern='^127\.0\.0\.1:8083/nousresearch/hermes-agent:([A-Za-z0-9_][A-Za-z0-9_.-]{0,127})@(sha256:[a-f0-9]{64})$'
  if ! [[ "$NEXUS_SYNC_HERMES_REF" =~ $hermes_ref_pattern ]]; then
    echo 'candidate Hermes ref must use the production repository with a pinned sha256 digest' >&2
    exit 2
  fi
  hermes_source_tag="${BASH_REMATCH[1]}"
  hermes_digest="${BASH_REMATCH[2]}"
  hermes_target="nousresearch/hermes-agent:${hermes_source_tag}"
  if [ -n "$sync_only" ]; then
    case ",$sync_only," in
      *,"$hermes_target",*) ;;
      *) echo 'NEXUS_SYNC_ONLY must select the supplied candidate Hermes target' >&2; exit 2 ;;
    esac
  fi
fi

node_runtime_enabled=false
node_runtime_target='node:22.16.0-bookworm-slim'
node_runtime_source_tag='22.16.0-bookworm-slim'
node_runtime_digest=''
if [ "${NEXUS_SYNC_NODE_RUNTIME_REF+x}" = x ]; then
  if [ "$registry" != '127.0.0.1:8082' ] || [ "$group_registry" != '127.0.0.1:8083' ]; then
    echo 'candidate Node runtime sync requires the production local Nexus registries' >&2
    exit 2
  fi
  node_runtime_ref_pattern='^127\.0\.0\.1:8083/(node:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})@(sha256:[a-f0-9]{64})$'
  if ! [[ "$NEXUS_SYNC_NODE_RUNTIME_REF" =~ $node_runtime_ref_pattern ]]; then
    echo 'candidate Node runtime ref must use the production repository with a pinned sha256 digest' >&2
    exit 2
  fi
  node_runtime_target="${BASH_REMATCH[1]}"
  node_runtime_source_tag="${node_runtime_target#node:}"
  node_runtime_digest="${BASH_REMATCH[2]}"
  node_runtime_enabled=true
  if [ -n "$sync_only" ]; then
    case ",$sync_only," in
      *,"$node_runtime_target",*) ;;
      *) echo 'NEXUS_SYNC_ONLY must select the supplied candidate Node runtime target' >&2; exit 2 ;;
    esac
  fi
fi

log "mode ${mode}; registry ${registry}"

sync_image 'pgvector/pgvector:pg16' 'swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/pgvector/pgvector:pg16;pgvector/pgvector:pg16'
sync_image 'library/redis:7-alpine' 'swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/redis:7-alpine;redis:7-alpine'
sync_image 'minio/minio:RELEASE.2024-01-31T20-20-33Z' 'minio/minio:RELEASE.2024-01-31T20-20-33Z'
sync_image 'minio/mc:latest' 'minio/mc:latest'
sync_image 'clickhouse/clickhouse-server:24.3.4.147' 'clickhouse/clickhouse-server:24.3.4.147'
sync_image 'berriai/litellm:main-latest' 'ghcr.io/berriai/litellm:main-latest;swr.cn-north-4.myhuaweicloud.com/ddn-k8s/ghcr.io/berriai/litellm:main-latest;berriai/litellm:main-latest'
sync_image 'searxng/searxng:latest' 'searxng/searxng:latest'
sync_image 'canner/wren-engine-ibis:0.24.6' 'ghcr.io/canner/wren-engine-ibis:0.24.6;canner/wren-engine-ibis:0.24.6'
sync_image 'open-webui/open-webui:v0.9.2' 'swr.cn-north-4.myhuaweicloud.com/ddn-k8s/ghcr.io/open-webui/open-webui:v0.9.2;ghcr.io/open-webui/open-webui:v0.9.2'
sync_image 'library/nginx:alpine' 'swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/nginx:alpine;nginx:alpine'
sync_image 'keking/kkfileview:latest' 'swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/keking/kkfileview:latest;keking/kkfileview:latest'
sync_image 'onlyoffice/documentserver:8.2' 'swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/onlyoffice/documentserver:8.2;onlyoffice/documentserver:8.2'
# Hermes is a reviewed OCI index. Copy it through the Nexus group
# with its original descriptor intact: a Docker pull/tag/push would reduce the
# index to the runner platform manifest and invalidate the digest-pinned build
# contract. The hosted tag remains audit-friendly; the workflow verifies the
# group-resolved digest before it builds from that contract.
sync_manifest_index "$hermes_target" \
  "${group_registry}/nousresearch/hermes-agent" \
  "$hermes_source_tag" \
  "$hermes_digest"
if [ "$node_runtime_enabled" = true ]; then
  sync_manifest_index "$node_runtime_target" \
    "${group_registry}/node" \
    "$node_runtime_source_tag" \
    "$node_runtime_digest"
fi
sync_image 'vecta-hermes-withopenclaw:v2026.5.16' 'vecta-hermes-withopenclaw:v2026.5.16' true
sync_image 'alpine/openclaw:2026.5.18' 'alpine/openclaw:2026.5.18'


# An unregistered target used to skip every entry and report success. Report
# unknown selections explicitly; every registered sync still retains its own
# copy/inspection failure status and its existing per-image behavior.
if [ -n "$sync_only" ]; then
  IFS=',' read -r -a requested_targets <<< "$sync_only"
  for requested_target in "${requested_targets[@]}"; do
    case "$matched_target_paths" in
      *,"$requested_target",*) ;;
      *) echo "unknown Nexus sync target: $requested_target" >&2; exit 2 ;;
    esac
  done
fi
