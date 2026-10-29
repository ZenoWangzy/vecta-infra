# The proxy target requires the image reference from the deployment's infra pin.
# scratch deliberately has no /app: an omitted proxy pin fails at COPY.
ARG OPENWEBUI_IMAGE=scratch
FROM ${OPENWEBUI_IMAGE} AS pinned-upstream

# Source build for issue 2173 / issue 2206 and lazy UI block renderer. Backend patches still run at startup.
FROM node:22-alpine3.20 AS ui-blocks
ARG NPM_REGISTRY
ARG npm_config_registry=${NPM_REGISTRY}
WORKDIR /workspace
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY patches/@modelcontextprotocol__sdk@1.29.0.patch patches/@modelcontextprotocol__sdk@1.29.0.patch
COPY packages/ui-blocks/package.json packages/ui-blocks/package.json
RUN corepack enable && pnpm install --filter @vecta/ui-blocks --frozen-lockfile --ignore-scripts
COPY packages/ui-blocks/ packages/ui-blocks/
RUN pnpm --filter @vecta/ui-blocks build

FROM node:22-alpine3.20 AS frontend
# mypc builds pass the local Nexus npm proxy; the direct or squid path stalled npm ci (2026-10-26).
ARG NPM_REGISTRY
ARG npm_config_registry=${NPM_REGISTRY}
# onnxruntime-node postinstall fetches CUDA binaries from GitHub directly (no proxy support); the frontend build never loads them.
ARG ONNXRUNTIME_NODE_INSTALL_CUDA=skip
ARG ONNXRUNTIME_NODE_INSTALL=skip
ARG ALPINE_MIRROR=
RUN if [ -n "$ALPINE_MIRROR" ]; then \
      alpine_branch="$(cut -d. -f1,2 /etc/alpine-release)"; \
      printf '%s/v%s/main\n%s/v%s/community\n' "$ALPINE_MIRROR" "$alpine_branch" "$ALPINE_MIRROR" "$alpine_branch" > /etc/apk/repositories; \
    fi \
    && apk add --no-cache git patch
WORKDIR /app
RUN git clone --depth 1 --branch v0.11.4 https://github.com/open-webui/open-webui.git . \
    && test "$(git rev-parse HEAD)" = 8bd8b4fac5e059578ac0c74b3c18d11139f88b7d
RUN npm ci --force
COPY --from=ui-blocks /workspace/packages/ui-blocks/package.json /app/node_modules/@vecta/ui-blocks/package.json
COPY packages/ui-blocks/src/ /app/node_modules/@vecta/ui-blocks/src/
COPY --from=ui-blocks /workspace/packages/ui-blocks/dist/ /app/node_modules/@vecta/ui-blocks/dist/
COPY infra/open-webui/source-patches/ /source-patches/
COPY infra/open-webui/apply-source-patches.sh /usr/local/bin/apply-source-patches
RUN sh /usr/local/bin/apply-source-patches /source-patches /app
ENV APP_BUILD_HASH=8bd8b4fac5e059578ac0c74b3c18d11139f88b7d
# vite build of the patched frontend exceeds the default V8 heap (release build 40909421951 OOM, 2026-10-29); Open WebUI docs set this
ENV NODE_OPTIONS=--max-old-space-size=8192
RUN npm run build

# Use the deployed pin's already-built frontend; source patches above belong to
# the separate, pending source candidate. This target never upgrades Open WebUI.
FROM node:22-alpine3.20 AS pinned-assets
ARG OPENWEBUI_IMAGE
WORKDIR /app
COPY --from=pinned-upstream /app/build /app/build
COPY --from=pinned-upstream /app/package.json /app/package.json
COPY infra/open-webui/patches/branding/ /app/build/static/
COPY packages/fleet-gateway/src/routes/open-webui-rewrite.ts /workspace/packages/fleet-gateway/src/routes/open-webui-rewrite.ts
COPY packages/fleet-gateway/src/routes/open-webui-rewrite-version.ts /workspace/packages/fleet-gateway/src/routes/open-webui-rewrite-version.ts
COPY packages/fleet-gateway/src/routes/open-webui-rewrite-version.js /workspace/packages/fleet-gateway/src/routes/open-webui-rewrite-version.js
COPY infra/open-webui/generate-rewrite-version.mjs /workspace/infra/open-webui/generate-rewrite-version.mjs
COPY infra/open-webui/rewrite-build.mjs /workspace/infra/open-webui/rewrite-build.mjs
RUN upstream_ref="${OPENWEBUI_IMAGE%@*}" \
    && node /workspace/infra/open-webui/generate-rewrite-version.mjs /workspace/packages/fleet-gateway/src/routes/open-webui-rewrite.ts \
    && node --experimental-strip-types /workspace/infra/open-webui/rewrite-build.mjs /app/build /app/nginx-assets "${upstream_ref##*:}"

FROM nginx:1.27-alpine AS proxy
COPY --from=pinned-assets /app/nginx-assets/ /usr/share/nginx/open-webui/chat/
COPY --from=pinned-assets /app/rewrite-artifact.json /usr/share/nginx/open-webui/rewrite-artifact.json
COPY infra/open-webui/nginx.conf /etc/nginx/conf.d/default.conf

FROM ghcr.io/open-webui/open-webui:v0.11.4
COPY --from=frontend /app/build /app/build
