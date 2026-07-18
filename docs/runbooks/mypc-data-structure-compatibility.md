# mypc Data-Structure Compatibility Report

Date: 2026-07-18

This report records the read-only comparison between mypc production and vtest.
It defines the compatibility boundary for gradually recreating production under
Ansible without moving or replacing production data.

## Summary

mypc is not a vtest clone. vtest is the cleaner Ansible/Nexus target shape;
mypc is the production state source. The migration must make Ansible compatible
with mypc first, then recreate services one at a time.

Do not normalize production by creating new vtest-style volumes or paths.

## Container Shape

vtest runs most VectA services from `127.0.0.1:8082/...:<full-sha>` images and
has an Ansible-managed ClickHouse container. mypc currently runs the main app
containers from local cache tags:

- `openclaw-enterprise-fleet-gateway`
- `openclaw-enterprise-rag-service`
- `openclaw-enterprise-a2a-router`
- `openclaw-enterprise-directory-service`
- `openclaw-enterprise-admin-console`
- `openclaw-enterprise-baidu-search-service`
- `vecta-channel-gateway:latest`
- `vecta-wecom-contact-sync:latest`

Those local app images have been mirrored into mypc Nexus as `latest` and
`cache-<image-id>` bridge tags. They are not source-build `deploy_sha` release
tags.

## Stateful Volumes

Preserve these mypc Docker volumes exactly:

| Service | mypc production owner | vtest shape |
|---|---|---|
| PostgreSQL | `openclaw-enterprise_postgres_data` | `postgres_data` |
| Redis | `140f0b143751894f82ec1b1ea8e9401051d45bf8fe031bfdffbb5e8557162151` | `redis_data` |
| MinIO | `openclaw-enterprise_minio-data` | `/data/ocee/data/minio` bind |
| Open WebUI | `openclaw-enterprise_open-webui-data` plus wrapper/patch binds | `openclaw-open-webui-data` |
| OnlyOffice | `openclaw-enterprise_onlyoffice-logs`, `openclaw-enterprise_onlyoffice-data`, plus anonymous internal service volumes | `onlyoffice_logs`, `onlyoffice_data`, anonymous internal volumes |
| RAG cache | `openclaw-enterprise_rag_model_cache` mounted at `/home/node/.cache/huggingface` | `rag_model_cache` mounted at `/app/model_cache` |

No mypc ClickHouse production container was found in the production inspection.
Do not create one as part of app recreation unless a separate data-owner decision
approves it.

## Bind Paths

Preserve these production bind paths during any app recreation:

| Service | Host path | Container path |
|---|---|---|
| Fleet | `/data/ocee/data/instances` | `/app/data/instances` |
| Fleet/RAG uploads | `/data/ocee/data/fleet-gateway/uploads` | `/app/data/uploads` |
| Fleet knowledge | `/data/ocee/packages/rag-service/knowledge` | `/app/knowledge` |
| Fleet templates | `/data/ocee/templates` | `/app/templates` |
| Fleet shared plugins | `/data/ocee/deploy/instances/shared/plugins` | `/app/shared-plugins` |
| Fleet LiteLLM config | `/data/ocee/infra/litellm/config.yaml` | `/app/litellm-config.yaml` |
| Fleet fruit pack | `/data/ocee/packages/fruit-industry-pack` | `/app/industry-packs/fruit` |
| Channel Gateway | `/data/ocee/packages/channel-gateway/data` | `/app/packages/channel-gateway/data` |
| Open WebUI wrapper | `/data/ocee/infra/open-webui/entrypoint-wrapper.sh` | `/app/backend/entrypoint-wrapper.sh` |
| Open WebUI patches | `/data/ocee/infra/open-webui/patches` | `/patches` |
| Open WebUI nginx | `/data/ocee/infra/open-webui/nginx.conf` | `/etc/nginx/conf.d/default.conf` |

The mypc inventory now carries these paths explicitly.

## Database Shape

Read-only table inventory found:

- mypc: `public` 103 tables, `fruit` 8 tables, `drizzle` 1 table,
  `fruit_meta` 1 table.
- vtest: `public` 81 tables, `fruit` 14 tables, `fruit_meta` 1 table.

mypc has production-only tables including ontology/entity/wiki/workflow,
share-link, invitation, and conversation artifact tables. vtest has newer fruit
processing tables absent from mypc.

Compatibility rule: production schema work must be additive and reviewed. Do not
replay the vtest schema or migration ledger wholesale into production.

## Compose Ownership Blocker

Live mypc container labels reference `/data/ocee/docker-compose.yml` and
`/data/ocee/docker-compose.override.yml`, but `/data/ocee/docker-compose.yml`
was not present during inspection. Fleet also references a backup Compose bundle
and fruit runtime overlay.

Runtime recreation is blocked until the active service spec is restored or
generated from live `docker inspect` plus backup Compose evidence.

## Gradual Recreation Plan

1. Keep `mypc_deploy_enabled=false` and `mypc_stateful_services_enabled=false`.
2. Keep mirroring local cache images into mypc Nexus for rollback/audit.
3. Recover an authoritative mypc service spec preserving every env key, port,
   network, alias, bind path, and volume.
4. Render Ansible app roles against mypc with production path variables and the
   `deploy_image_tags` cache-bridge map.
5. Recreate stateless services first: A2A, Directory, Admin, Baidu. The mypc
   inventory now defaults to this first stage through
   `vecta_app_enabled_services` and `search_enabled_services`; Fleet, RAG,
   Channel, and WeCom remain excluded until their bind-path/channel risks are
   reviewed.
6. Recreate bind/path-sensitive services next: Fleet, RAG, WeCom.
7. Recreate Channel Gateway last, preserving `primary`, `active`, and
   `owner=mypc` semantics.
8. Observe for 48 hours before any stateful service adoption.
9. Adopt Redis, MinIO, Open WebUI/OnlyOffice, optional ClickHouse, and
   PostgreSQL only through service-specific backup, restore, rollback, and
   observation gates.

## Implemented Compatibility Controls

- `deploy_image_tag_requires_full_sha` defaults to true for vtest. mypc sets it
  to false only for the explicit `local-cache-20260718` bridge.
- `deploy_image_tags` maps each mypc app repo to the exact mirrored
  `cache-<image-id>` tag.
- `vecta_app_enabled_services` allows the VectA app role to recreate only the
  approved service subset.
- `search_enabled_services` allows Baidu to be recreated without adopting
  SearXNG ownership in the same step.
- WeCom is intentionally absent from the first stage because the current role
  uses a different target container name than live production.

## First-Stage Cutover Evidence

Executed on 2026-07-18 with `mypc_deploy_enabled=true`, `vtest_allow_migrate=0`,
`--tags search,vecta-app`, and the mypc service allowlists.

Recreated from mypc Nexus cache tags:

- `openclaw-baidu-search-service` ->
  `127.0.0.1:8082/baidu-search-service:cache-fdfbc005133b`
- `openclaw-a2a-router` -> `127.0.0.1:8082/a2a-router:cache-3134bcb4484a`
- `openclaw-directory-service` ->
  `127.0.0.1:8082/directory-service:cache-d38d9f4d3636`
- `openclaw-admin-console` ->
  `127.0.0.1:8082/admin-console:cache-19ca33c7e758`

Skipped by design: Fleet, RAG, Channel Gateway, WeCom contact sync, Open WebUI,
OpenClaw runtime containers, PostgreSQL, Redis, MinIO, OnlyOffice, ClickHouse,
and migrations.

Post-cutover checks:

- `http://127.0.0.1:9200/healthz` -> 200
- `http://127.0.0.1:8001/healthz` -> 200
- `http://127.0.0.1:5173/` -> 200
- `http://127.0.0.1:3000/healthz` -> 200
- `http://127.0.0.1:9000/healthz` -> 200
- `http://127.0.0.1:3002/` -> 200
- `https://vecta.matrix-ai.com.cn/` -> 200
- `https://vecta.matrix-ai.com.cn/admin/` -> 200
- `https://vecta.matrix-ai.com.cn/chat/` -> 302

During the run, two missing production contracts were found and fixed in the
roles: mypc uses `openclaw-enterprise_open-webui-net`, and Admin/Directory must
publish host ports `5173` and `8001` respectively.
