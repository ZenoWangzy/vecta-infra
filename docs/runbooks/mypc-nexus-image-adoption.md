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
   starting with A2A/Directory/Admin/Baidu. WeCom contact sync is the first
   later app candidate because it has no mounts, but its legacy container name,
   aliases, command, resource limits, log rotation, and live channel environment
   must be captured before recreation. Leave Fleet, RAG, and Channel for their
   dedicated bind-path/channel ownership phases.
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

## Application Adoption Evidence

### WeCom Contact Sync - 2026-07-18

`openclaw-wecom-contact-sync` was recreated from
`127.0.0.1:8082/wechat-contact-sync:cache-73d27641f5e9`. The source and Nexus
images had the identical Docker image ID. The cutover retained the legacy
container name, command, environment, aliases, restart policy, resource limits,
and JSON log rotation. Pre/post checks verified the contact-sync process plus
PostgreSQL, Redis, and Fleet Gateway health. No writable mount or Docker volume
is attached to this service.

### RAG Service - 2026-07-18

`openclaw-rag-service` was recreated from
`127.0.0.1:8082/rag-service:cache-761b14870cf1` after the source and Nexus
images were proved identical. The exact production mounts were retained:
`openclaw-enterprise_rag_model_cache:/home/node/.cache/huggingface` and
`/data/ocee/packages/rag-service/knowledge:/app/knowledge`.

Before replacement, compressed backups and checksum evidence were written under
`/data/ocee/backups/app-adoption/rag-service-nexus-adoption-20260718T110311Z`.
The RAG health endpoint, PostgreSQL, Redis, Fleet Gateway, and the full
post-deploy regression passed after replacement.

### RAG Parser Repair - 2026-07-19

A protected mypc build produced the immutable RAG image
`127.0.0.1:8082/rag-service:89da897ec9f7a1b0e1fb59d1ad0238ec46680ecb`
from VectA `main` commit `89da897ec9f7a1b0e1fb59d1ad0238ec46680ecb`.
The RAG task keeps the default identical-image requirement. A changed image is
allowed only when the one-service run explicitly sets
`rag_service_allow_image_upgrade=true` and supplies a full 40-character SHA
tag. It still captures the live contract, creates checksummed backups, and
reuses the original cache volume and knowledge bind exactly.

The build cache cleanup removed the old `cache-*` tag while the live container
continued to reference its image ID. The task therefore compares the source by
`docker inspect` image ID, not its mutable/cleanable tag. The repair backup was
written to
`/data/ocee/backups/app-adoption/rag-service-nexus-adoption-20260719T032032Z`.
The source and target image IDs, backup checksums, RAG/Fleet/PostgreSQL/Redis
health, mount contract, authenticated knowledge list, and the single approved
document reingest all passed. No volume, bind path, or document file was
replaced.

### Fleet Gateway - 2026-07-18

`openclaw-fleet-gateway` now uses
`127.0.0.1:8082/fleet-gateway:cache-c78398cfe144`. The instance bind was backed
up under `/data/ocee/backups/app-adoption/fleet-gateway-nexus-adoption-20260718T110711Z`.
Both production networks, all mount modes, user `100:110`, and the live runtime
environment were retained. The managed runtime count was unchanged and full
regression passed.

### Channel Gateway - 2026-07-18

`openclaw-channel-gateway` now uses
`127.0.0.1:8082/channel-gateway:cache-94a5357394f9`. Its production data bind
was backed up under `/data/ocee/backups/app-adoption/` before replacement. The
post-cutover check confirmed active `mypc` ownership, authenticated WeCom, and
an open Feishu websocket, followed by the platform regression.

### Stateful and Document Services - 2026-07-18

Open WebUI, its nginx proxy, kkFileView, SearXNG, OnlyOffice, Fruit LiteLLM,
and the Fruit Feishu gateway were mirrored from the running local cache into
immutable `cache-<image-id>` Nexus tags, then adopted one container at a time.
Before each recreation, the adoption role archived every existing named volume
and bind mount to `/data/ocee/backups/app-adoption/`, recorded the live inspect
contract, compared Linux filesystem layers, and remounted the original source
strings. OnlyOffice retained all six existing named volumes; Open WebUI retained
its data volume plus wrapper and patch binds. Full post-adoption regression
passed.

### Fleet-managed Hermes Runtime Adoption - 2026-07-18

All 41 running Hermes runtime containers were adopted sequentially. Each runtime
was stopped individually, its original mounts were archived, and it was
recreated with the same ports, command, environment, network, and volume names.
The normal runtime image now uses
`127.0.0.1:8082/vecta-hermes-withopenclaw:v2026.5.16`.

`openclaw-CODXPERM195543` used a distinct legacy image ID. It was mirrored and
adopted from the immutable
`127.0.0.1:8082/vecta-hermes-withopenclaw:cache-26a09247ebe6` tag rather than
being substituted with the newer runtime image. The Fruit industry-pack canary
was similarly mirrored to
`127.0.0.1:8082/fruit-industry-pack:cache-64d0a7c24944` before recreation.

The migration remains data-first: do not bulk restart runtimes in future runs.
Use `playbooks/mypc-adopt.yml` for exactly one runtime, retain its backup under
`/data/ocee/backups/app-adoption/`, and run the platform regression afterward.

## Completed Regression Evidence

The final `scripts/mypc-data-layer-regression.sh --service all --phase after`
run passed PostgreSQL, Redis, MinIO, LiteLLM, Fleet Gateway, Admin Console,
Directory Service, RAG, Channel Gateway, and Open WebUI proxy checks. ClickHouse
was skipped because no production ClickHouse container exists. This is the
mandatory post-adoption regression for every mypc service step.

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

### PostgreSQL Evidence Package - 2026-07-18

The database was verified safe for the reviewed Ansible recreation and was then
adopted through the one-service PostgreSQL run.

- Evidence directory:
  `/data/ocee/backups/postgres-adoption/postgres-adoption-20260718T103713Z`.
  It contains a 28 MiB custom-format dump with `postgres.dump.sha256`, source
  schema/inventory/row-count artifacts, a migration-ledger baseline, schema
  review, additive-delta approval, and restore rehearsal record.
- Isolated restore: `pg_restore` completed in a temporary network-disabled
  container using only an evidence-directory bind; it never mounted or modified
  `openclaw-enterprise_postgres_data`. Temporary restore data was removed after
  verification.
- Restore proof: source and restored database inventories, extension list,
  three-record Drizzle ledger, and invariants for `tenants` (11), `employees`
  (56), `files` (377), `fleet_instances` (53), and `knowledge_documents` (17)
  are identical.
- Schema review: the only five raw `pg_dump` differences are equivalent CHECK
  constraint renderings for `customers_status_chk`,
  `inventory_movements_type_chk`, `products_status_chk`,
  `suppliers_status_chk`, and `employee_skill_installs_status_check`; no
  additive database SQL is required for the identical-image registry change.
- Guarded dry run: the PostgreSQL-only Ansible check verified all evidence
  paths, preserved the existing volume/ports/aliases/credentials, ran automatic
  pre/post regression, and proposed exactly one change: recreate
  `openclaw-postgres` from the Nexus image.
- Final adoption: the actual PostgreSQL-only Ansible run recreated exactly
  `openclaw-postgres` as
  `127.0.0.1:8082/pgvector/pgvector:pg16` while retaining
  `openclaw-enterprise_postgres_data:/var/lib/postgresql/data`, aliases
  `openclaw-postgres` and `postgres-db`, port `5432`, restart policy `always`,
  memory `1g`, and CPU `1.0`. The image ID remains
  `sha256:8d34961969a85159aea1376b91f521084e13e28e87f6f1f3ec17f240924e35c8`.
- Final verification: PostgreSQL is healthy at version 16.3 with 103 public
  tables and three Drizzle ledger records. Full regression passed for
  PostgreSQL, Redis, MinIO, LiteLLM, Fleet, Admin, Directory, RAG, Channel, and
  Open WebUI proxy; ClickHouse remains skipped because there is no production
  container.

Use `scripts/mypc-postgres-adoption-evidence.sh --execute` on mypc to produce a
fresh evidence package before any future recreation. The Ansible run must pass
its reviewed artifact paths and explicit approval as extra variables; do not
persist approval in the default inventory.

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
