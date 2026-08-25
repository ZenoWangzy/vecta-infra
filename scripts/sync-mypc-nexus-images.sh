#!/usr/bin/env bash
# Sync approved production image inputs into the mypc-local Nexus registry.
# This script only pulls/tags/pushes images. It never starts containers, changes
# volumes, or prunes data.
set -euo pipefail

registry="${NEXUS_DOCKER_REGISTRY:-127.0.0.1:8082}"
sync_only="${NEXUS_SYNC_ONLY:-}"
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
# Hermes v2026.8.19 is the reviewed upstream runtime base for the corresponding
# VectA production image. The hosted tag includes the locked digest prefix so a
# Docker Hub tag cannot shadow the audited copy through the Nexus group.
sync_image 'nousresearch/hermes-agent:v2026.8.19-3811ed13' 'nousresearch/hermes-agent:v2026.8.19@sha256:3811ed13da874fba2ac99b6d492db9a203d34cb6dccf90d886948c00d0ccec09'
sync_image 'vecta-hermes-withopenclaw:v2026.5.16' 'vecta-hermes-withopenclaw:v2026.5.16' true
sync_image 'alpine/openclaw:2026.5.18' 'alpine/openclaw:2026.5.18'
