# mypc Nexus Image Adoption Runbook

This runbook covers the gradual replacement of direct/local production image
references with the production-local Nexus Docker registry. It is not a deploy
approval.

## Current Boundary

- Build into the mypc-local Nexus at `127.0.0.1:8082`.
- Keep the first production increment application-only.
- Do not let Ansible create, replace, mount, prune, or migrate stateful data
  volumes during image-cache adoption.
- Keep PostgreSQL, Redis, MinIO, ClickHouse, LiteLLM, Open WebUI, OnlyOffice,
  per-user runtime volumes, and current ingress owner under their existing
  production owner until a service-specific adoption phase is approved.

## mypc / vtest Data-Structure Compatibility

The production host is not a copy of vtest. Treat vtest as the Ansible target
shape and mypc as the stateful source of truth.

Observed differences on 2026-07-18:

- Compose ownership differs. vtest has active `/data/ocee/docker-compose.yml`
  and `/data/ocee/docker-compose.override.yml`; mypc live containers reference
  those paths in labels, but `/data/ocee/docker-compose.yml` was absent. Fleet
  also references a backup Compose bundle and a fruit runtime overlay.
- Image shape differs. vtest app containers run Nexus SHA tags; mypc app
  containers still run local cache tags such as `openclaw-enterprise-*` and
  `vecta-channel-gateway:latest`, mirrored to Nexus only as `latest` and
  `cache-<image-id>` bridge tags.
- Stateful mounts differ. mypc PostgreSQL, Redis, MinIO, Open WebUI,
  OnlyOffice, and RAG cache use existing production Docker volumes; vtest uses
  shorter Ansible-created names or bind paths in several places.
- Application bind paths differ. mypc Fleet keeps `/data/ocee/data/instances`,
  `/data/ocee/templates`, `/data/ocee/deploy/instances/shared/plugins`,
  `/data/ocee/packages/rag-service/knowledge`, and the fruit industry pack bind
  mounted. vtest Fleet uses `/data/ocee/deploy` and upload paths.
- Open WebUI differs. mypc mounts production wrapper/patch paths under
  `/data/ocee/infra/open-webui`; vtest currently uses the plain data volume and
  infra-rendered nginx config.
- Database shape differs. mypc has 103 public tables plus `drizzle`, `fruit`,
  and `fruit_meta` schemas; vtest has 81 public tables and newer fruit
  processing tables. mypc also has production-only ontology/entity/workflow,
  share-link, invitation, and conversation artifact tables. Do not replay the
  vtest schema wholesale into production.

The mypc inventory now pins the existing production bind paths so the Ansible
roles can render a production-compatible container spec before any runtime
replacement is approved.

## Gradual Recreation Sequence

1. **Inventory parity only**: keep `mypc_deploy_enabled=false` and
   `mypc_stateful_services_enabled=false`; verify Ansible renders syntax with
   mypc-specific volume and bind-path variables.
2. **Image cache bridge**: mirror current local app images to mypc Nexus with
   `latest` and `cache-<image-id>` tags. This is already done for the current
   app cache and does not recreate containers.
3. **Compose/source recovery**: restore or generate an authoritative mypc
   service spec from live `docker inspect` plus the known backup Compose files.
   This spec must preserve every port, env key, bind path, network alias, and
   volume name before a container is replaced.
4. **Application-only dry run**: render/check the Ansible app role against mypc
   with the recovered spec and Nexus bridge tags. Do not include PostgreSQL,
   Redis, MinIO, Open WebUI, OnlyOffice, ClickHouse, or per-user runtimes.
5. **Stateless recreation**: recreate one non-stateful app container at a time,
   starting with A2A/Directory/Admin/Baidu and leaving Fleet, RAG, Channel, and
   WeCom for later because they carry bind-path or channel ownership risk.
6. **Fleet/RAG/Channel/WeCom recreation**: replace only after health probes,
   channel ownership checks, runtime-start checks, and rollback commands are
   prepared. Channel remains last and must keep `owner=mypc`, `primary`, and
   active semantics.
7. **48-hour observation**: no stateful adoption until application-only
   recreation stays healthy for the full window.
8. **Stateful service adoption**: adopt Redis, MinIO, Open WebUI/OnlyOffice,
   ClickHouse if required, and PostgreSQL service-by-service with backup,
   restore proof, mount map, rollback, and post-adoption observation.

## Image Mapping

`inventories/mypc/group_vars/mypc.yml` must point image variables at
`{{ nexus_docker_registry }}`. Application images use `{{ deploy_sha }}`.
Third-party and runtime images keep pinned tags and are synced into Nexus before
the corresponding service is eligible for adoption.

GitHub Actions must not reuse one shared Nexus admin secret across vtest and
mypc. The mypc production-image workflow reads the repository secret
`MYPC_NEXUS_ADMIN_PASSWORD` and maps it to the job-local environment variable
`NEXUS_ADMIN_PASSWORD` for Docker login and Nexus API calls. vtest workflows use
`VTEST_NEXUS_ADMIN_PASSWORD` separately.

Current mypc sync status from 2026-07-18:

- Verified usable from Nexus: PostgreSQL, Redis, MinIO, MinIO client,
  ClickHouse, LiteLLM `main-latest`, SearXNG, Wren engine, Open WebUI, nginx,
  kkFileView, OnlyOffice, and `vecta-hermes-withopenclaw:v2026.5.16`.
- Blocked: `alpine/openclaw:2026.5.18`. The upstream/local tag resolves to an
  OCI image-index descriptor that Docker cannot push as a platform manifest:
  `image ... was found but does not provide any platform`. Do not enable the
  mypc OpenClaw role from the Nexus image until this image is rebuilt or copied
  from a registry source that provides a valid linux/amd64 manifest.

Current mypc local application-cache sync status from 2026-07-18:

- Command: `scripts/sync-mypc-local-app-images.sh --execute` on mypc with
  `NEXUS_DOCKER_REGISTRY=127.0.0.1:8082`.
- Verified pullable from Nexus: `a2a-router`, `admin-console`,
  `baidu-search-service`, `directory-service`, `fleet-gateway`, `rag-service`,
  `channel-gateway`, and `wechat-contact-sync`.
- Each image is pushed with `latest` for compatibility and with an immutable
  `cache-<image-id>` tag for rollback/audit of the exact local image cache.
- mypc inventory now uses the immutable `cache-<image-id>` tags for the first
  estimated app-only recreation stage instead of floating `latest` tags.
- Running production containers were not recreated during this step. Runtime
  replacement must wait until the active Compose source is restored or captured:
  live container labels reference `/data/ocee/docker-compose.yml`, but that file
  was not present during the 2026-07-18 inspection; Fleet also references a
  backup Compose bundle plus fruit runtime Compose overlay.

The mypc inventory intentionally keeps:

- `mypc_stateful_services_enabled: false`
- `wren_engine_enabled: false`
- `fruit_vtest_enabled: false`

## Safe Increment Order

1. Build and push the selected VectA SHA into mypc Nexus.
2. Dry-run the approved third-party/runtime image sync:

   ```bash
   scripts/sync-mypc-nexus-images.sh --dry-run
   ```

3. After review, sync only image cache inputs into Nexus:

   ```bash
   scripts/sync-mypc-nexus-images.sh --execute
   ```

   To mirror the current local application image cache into Nexus without
   changing running containers:

   ```bash
   scripts/sync-mypc-local-app-images.sh --execute
   ```

4. Verify required application and approved third-party image manifests in
   Nexus.
5. Replace only application-container image references through the app cutover
   path after approval. The first mypc stage is allowlisted to A2A, Directory,
   Admin, and Baidu only.
6. Observe the application-only cutover for the required health window.
7. Adopt stateful services one at a time only after each service has:
   backup evidence, restore rehearsal, exact old-to-new mount mapping,
   rollback steps, and an observation gate.

## Data-Layer Adoption Sequence

Data-layer migration is service-by-service. Do not set
`mypc_stateful_services_enabled=true` by itself. Every production run must also
set `mypc_stateful_service_allowlist` to exactly one reviewed service for that
phase. The `infra-services` role runs the read-only regression script before
and after the allowlisted service; a regression failure stops the play before
the service changes or fails the play afterward.

Recommended order:

1. Redis: lowest persistence coupling; validates cache/session reachability.
2. MinIO: object storage; requires bucket/object inventory and upload/download
   regression evidence.
3. ClickHouse: only if a live production owner is found or a new audit-store
   decision is approved. Current mypc inspection found no live ClickHouse owner.
4. LiteLLM: infra-owned but not a stateful data store; adopt only after model
   routing and provider-secret checks are reviewed.
5. PostgreSQL: last. Requires schema inventory, migration-ledger baseline,
   fresh full backup, isolated restore rehearsal, additive-delta review, and
   application compatibility proof.

For every phase, perform the read-only preflight first, then run the Ansible
command. The role repeats the same selected-service regression immediately
before and after the adoption:

```bash
scripts/mypc-data-layer-regression.sh --service <service> --phase before

ansible-playbook playbooks/infra.yml \
  -i inventories/mypc/hosts.ini \
  -e ansible_host=mypc \
  -e mypc_deploy_enabled=true \
  -e mypc_stateful_services_enabled=true \
  -e '{"mypc_stateful_service_allowlist":["<service>"]}' \
  --tags infra-docker,infra-services

scripts/mypc-data-layer-regression.sh --service <service> --phase after
```

The regression script is read-only. It checks the selected data service and the
dependent application health endpoints. If either the before or after run fails,
stop and do not continue to the next service. Each successful service starts its
own observation window; PostgreSQL adoption must additionally pass schema and
restore gates before the Ansible run is allowed.

### Redis Adoption Evidence - 2026-07-18

Completed first data-layer step: Redis image source was moved from Huawei SWR to
the mypc-local Nexus image while preserving the existing production data volume.

- Before backup:
  `/data/ocee/backups/data-layer/redis-volume-before-nexus-adoption-20260718T093429Z.tgz`
- Preserved volume:
  `140f0b143751894f82ec1b1ea8e9401051d45bf8fe031bfdffbb5e8557162151:/data`
- Preserved runtime contract: host port `6379`, restart policy `always`, memory
  `128m`, CPU `0.25`.
- Required production aliases: `redis` and `openclaw-redis` on
  `openclaw-enterprise_openclaw-net`. Fleet uses
  `REDIS_URL=redis://:...@redis:6379`; losing the `redis` alias makes Fleet
  health fail even when `openclaw-redis` is reachable.
- Final image: `127.0.0.1:8082/library/redis:7-alpine`, same image digest as
  the pre-adoption SWR image.
- Post-regression passed for Redis plus PostgreSQL, MinIO, Fleet, Admin,
  Directory, RAG, Channel, and Open WebUI proxy. ClickHouse remains skipped
  because no production ClickHouse container exists.

During the run Fleet briefly returned 503 after Redis recreation because its
Redis client held stale sockets and the first role version had omitted the
production `redis` network alias. The alias contract was added to the mypc
inventory and Fleet was restarted once after Redis adoption to clear the stale
client state. Treat this as part of the Redis rollback/verification contract for
future reruns.

### MinIO Adoption Evidence - 2026-07-18

Completed second data-layer step: MinIO now runs from the mypc-local Nexus
image while retaining the existing production object-storage volume and runtime
contract.

- Before backup:
  `/data/ocee/backups/data-layer/minio-volume-before-nexus-adoption-20260718T094909Z.tgz`
  (181 MiB, SHA-256
  `24e3634e0e6a76e6b878d0c32c45dd76bc998f7b35c18a736e5c8cffee5a244d`).
- Preserved volume: `openclaw-enterprise_minio-data:/data`.
- Before and after inventory: buckets `files` and `openclaw-files`, 383 objects,
  199 MiB. The role does not run `minio-init` or write its marker on mypc when
  the mounted volume already contains `.minio.sys` and data.
- A bounded S3 regression wrote, read, checksummed, and removed one unique
  object under `local/files/.vecta-regression/`; the final inventory returned to
  383 objects. Confirm the final `mc du local` count after cleanup rather than
  relying only on the `mc rm` exit status.
- Preserved runtime contract: `29000:9000`, `29090:9090`, console `9090`,
  restart policy `unless-stopped`, memory `512m`, CPU `0.5`, Docker healthcheck,
  and aliases `openclaw-minio` and `minio` on
  `openclaw-enterprise_openclaw-net`.
- Preserved configuration: root credentials are read from the existing MinIO
  container with `no_log` during an adoption; the webhook enable flag, endpoint,
  and auth token remain configured. Do not substitute the legacy
  `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` values for the live root credentials.
- Final image:
  `127.0.0.1:8082/minio/minio:RELEASE.2024-01-31T20-20-33Z`
  (`sha256:342d8678117878a63daea9f217d48b60231757d31ce21aee9ce0ea3a4137b10e`).
  Its Linux/amd64 filesystem layers match the pre-adoption image, whose config
  digest was `sha256:4092433a77e510826874b36f369696df43407a763d7f901a61d74e83e6fd95bc`;
  keep that direct image available as the rollback reference.
- Targeted MinIO and full post-adoption regression passed: PostgreSQL, Redis,
  MinIO, Fleet, Admin, Directory, RAG, Channel, and Open WebUI proxy returned
  healthy checks. ClickHouse remains skipped because no production container
  exists.

### LiteLLM Adoption Evidence - 2026-07-18

Completed the next eligible infra-service step after ClickHouse was skipped:
LiteLLM now uses the mypc-local Nexus image. LiteLLM has no stateful Docker data
volume; its production state contract is the existing config bind and provider
environment, both retained during the image-source replacement.

- ClickHouse remains skipped: there is no `openclaw-clickhouse` production
  container or existing data owner to adopt.
- Before backup:
  `/data/ocee/backups/data-layer/litellm-config-before-nexus-adoption-20260718T101706Z.yaml`
  (1057 bytes, SHA-256
  `af921956edbb14c939e0538b0e2cdd364ac272e66ec9869101203989acdee662`).
- Preserved bind contract: `/data/ocee/infra/litellm/config.yaml` remains
  mounted read-write at `/app/config.yaml`; its post-adoption checksum equals
  the backup checksum.
- Preserved runtime contract: host port `4000`, command
  `--config /app/config.yaml --port 4000`, restart policy `always`, memory
  `2g`, CPU `2.0`, no Docker healthcheck, and aliases `openclaw-litellm` and
  `litellm-proxy` on `openclaw-enterprise_openclaw-net`.
- Preserved provider contract: `LITELLM_MASTER_KEY`, `ZAI_API_BASE`,
  `ZAI_API_KEY`, `DEEPSEEK_API_KEY`, `MOONSHOT_API_BASE`, and
  `MOONSHOT_API_KEY` are captured from the running container with `no_log` for
  an adoption. Do not rely on the Ansible controller environment to supply
  production provider credentials.
- Final image: `127.0.0.1:8082/berriai/litellm:main-latest`
  (`sha256:bbb422d4c47ff21a73513740f7d3e5dbf1aba9a4adbfcaf2ac2e66bcf4dd6798`),
  identical to the pre-adoption direct image ID.
- Automatic pre/post LiteLLM regression and a full post-adoption regression
  passed for PostgreSQL, Redis, MinIO, LiteLLM, Fleet, Admin, Directory, RAG,
  Channel, and Open WebUI proxy.

### PostgreSQL Adoption Gate - 2026-07-18

PostgreSQL is deliberately **not adopted** in this phase. The Nexus image and
direct image share image ID
`sha256:8d34961969a85159aea1376b91f521084e13e28e87f6f1f3ec17f240924e35c8`,
and the existing volume is `openclaw-enterprise_postgres_data`, but the data
governance gates are incomplete.

- Read-only baseline: database `openclaw_poc`, PostgreSQL 16.3, 103 public
  tables, four extensions, schemas `drizzle`, `fruit`, `fruit_meta`, and
  `public`; the Drizzle ledger contains only three entries.
- No current PostgreSQL dump or isolated restore rehearsal was found under
  `/data/ocee/backups` during the gate review.
- The role now preserves production restart policy `always`, aliases
  `openclaw-postgres` and `postgres-db`, resource limits, existing
  `openclaw-enterprise_postgres_data` volume, and the live credentials captured
  with `no_log`.
- A PostgreSQL allowlist run now fails before touching the container unless all
  five reviewed evidence artifacts exist on mypc: schema inventory,
  migration-ledger baseline, fresh backup, isolated restore rehearsal, and
  additive-delta approval. Do not bypass this with a manual container command.

## Hard Stops

- Do not run `playbooks/infra.yml` against mypc for stateful services while
  `mypc_stateful_services_enabled` is false.
- Do not run `playbooks/infra.yml` against mypc with
  `mypc_stateful_services_enabled=true` unless
  `mypc_stateful_service_allowlist` names exactly the approved service.
- Do not reuse vtest Nexus for mypc. The mypc registry is production-local.
- Do not prune production data volumes as part of image-cache cleanup.
- Do not switch tags such as LiteLLM or Hermes to vtest values unless that is a
  reviewed production release decision.
- Do not flatten/import `alpine/openclaw:2026.5.18` just to force it into Nexus;
  that would create a behaviorally different runtime image.
