#!/usr/bin/env bash
# Mirror the currently approved mypc local application image cache into the
# production-local Nexus registry. This script changes image tags only; it never
# starts containers, changes Compose files, mounts volumes, or prunes data.
set -euo pipefail

registry="${NEXUS_DOCKER_REGISTRY:-127.0.0.1:8082}"
mode="dry-run"

if [ "${1:-}" = "--execute" ]; then
  mode="execute"
elif [ "${1:-}" != "" ] && [ "${1:-}" != "--dry-run" ]; then
  echo "usage: $0 [--dry-run|--execute]" >&2
  exit 2
fi

log() {
  printf '[mypc-local-app-sync] %s\n' "$*"
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

image_id_tag() {
  docker image inspect "$1" --format '{{.Id}}' | sed 's/^sha256:/cache-/' | cut -c1-18
}

sync_local_image() {
  source_image="$1"
  target_repo="$2"
  compatibility_tag="$3"

  if ! docker image inspect "$source_image" >/dev/null 2>&1; then
    echo "missing local source image: ${source_image}" >&2
    return 1
  fi

  immutable_tag="$(image_id_tag "$source_image")"
  log "source ${source_image} -> ${registry}/${target_repo}:{${compatibility_tag},${immutable_tag}}"
  run docker tag "$source_image" "${registry}/${target_repo}:${compatibility_tag}"
  run docker push "${registry}/${target_repo}:${compatibility_tag}"
  run docker tag "$source_image" "${registry}/${target_repo}:${immutable_tag}"
  run docker push "${registry}/${target_repo}:${immutable_tag}"
}

log "mode ${mode}; registry ${registry}"

sync_local_image 'openclaw-enterprise-a2a-router:latest' 'a2a-router' 'latest'
sync_local_image 'openclaw-enterprise-admin-console:latest' 'admin-console' 'latest'
sync_local_image 'openclaw-enterprise-baidu-search-service:latest' 'baidu-search-service' 'latest'
sync_local_image 'openclaw-enterprise-directory-service:latest' 'directory-service' 'latest'
sync_local_image 'openclaw-enterprise-fleet-gateway:latest' 'fleet-gateway' 'latest'
sync_local_image 'openclaw-enterprise-rag-service:latest' 'rag-service' 'latest'
sync_local_image 'vecta-channel-gateway:latest' 'channel-gateway' 'latest'
sync_local_image 'vecta-wecom-contact-sync:latest' 'wechat-contact-sync' 'latest'
# Stateful/document/ingress services use the exact current local image cache.
# Their floating upstream tags may resolve to a newer image, so the immutable
# cache tag is the only eligible source for a later data-first adoption.
sync_local_image 'ghcr.io/open-webui/open-webui:v0.9.2' 'open-webui/open-webui' 'latest'
sync_local_image 'nginx:alpine' 'library/nginx' 'mypc-webui-proxy-latest'
sync_local_image 'onlyoffice/documentserver:8.2' 'onlyoffice/documentserver' 'mypc-onlyoffice-latest'
sync_local_image 'keking/kkfileview:latest' 'keking/kkfileview' 'mypc-kkfileview-latest'
sync_local_image 'searxng/searxng:latest' 'searxng/searxng' 'mypc-searxng-latest'
sync_local_image 'ghcr.io/berriai/litellm:main-latest' 'berriai/litellm' 'mypc-fruit-litellm-latest'
sync_local_image 'vecta-channel-gateway:7aa0a673' 'fruit-channel-gateway' 'latest'
