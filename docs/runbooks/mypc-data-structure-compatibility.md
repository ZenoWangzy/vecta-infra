# mypc Data-Structure Compatibility Report

Date: 2026-07-18

This report records the read-only structure of mypc production. It defines the
compatibility boundary for gradually managing production through Ansible
without moving or replacing production data.

## Summary

mypc is the production state source. Ansible must remain compatible with its
existing data, paths, network attachments, ports, and service ownership before
any service is recreated.

Do not normalize production by creating replacement volumes or paths.

## Container Shape

mypc currently runs the main app containers from local cache tags:

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

| Service | mypc production owner or mount |
|---|---|
| PostgreSQL | `openclaw-enterprise_postgres_data` |
| Redis | `140f0b143751894f82ec1b1ea8e9401051d45bf8fe031bfdffbb5e8557162151` |
| MinIO | `openclaw-enterprise_minio-data` |
| Open WebUI | `openclaw-enterprise_open-webui-data` plus wrapper/patch binds |
| OnlyOffice | `openclaw-enterprise_onlyoffice-logs`, `openclaw-enterprise_onlyoffice-data`, plus anonymous internal service volumes |
| RAG cache | `openclaw-enterprise_rag_model_cache` mounted at `/home/node/.cache/huggingface` |

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

Read-only mypc inventory found `public` 103 tables, `fruit` 8 tables,
`drizzle` 1 table, and `fruit_meta` 1 table. Production-only tables include
ontology/entity/wiki/workflow, share-link, invitation, and conversation
artifact tables.

Compatibility rule: production schema work must be additive and reviewed. Do not
replay an external schema or migration ledger wholesale into production.

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

- mypc sets `deploy_image_tag_requires_full_sha=false` only for the explicit
  `local-cache-20260718` bridge; repository-writer-initiated production image
  builds use the full current VectA main HEAD SHA.
- `deploy_image_tags` maps each mypc app repo to the exact mirrored
  `cache-<image-id>` tag.
- `vecta_app_enabled_services` allows the VectA app role to recreate only the
  approved service subset.
- `search_enabled_services` allows Baidu to be recreated without adopting
  SearXNG ownership in the same step.
- WeCom is intentionally absent from the first stage because the current role
  uses a different target container name than live production.

## First-Stage Cutover Evidence

Executed on 2026-07-18 with `mypc_deploy_enabled=true`,
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

## WebUI/Admin Network Reconcile Boundary

Use only `playbooks/mypc-network-reconcile.yml` for this mypc-only repair. The
thin playbook calls one transaction script, whose hostname, container names,
and network names are hardcoded. It accepts only the exact temporary state
(Proxy on WebUI; Admin on core plus WebUI) or exact canonical state (Proxy on
core plus WebUI; Admin on core). Canonical is a no-op. In-progress, extra, and
missing attachments fail closed.

The script uses only narrow formatted Docker inspection: container ID and
network names, plus network ID. It never requests full inspect output or
`Config.Env`. Before every mutation and rollback mutation it revalidates the
baseline container and network IDs; any ID drift fails closed and never touches
a replacement container. Execute revalidates both approvals internally, so
`--start-at-task` cannot bypass the guard. It attaches Proxy to core, inspects
the exact intermediate state, then detaches Admin from WebUI and inspects exact
canonical state. No image, volume, data, restart, recreate, pull, compose, or
other deployment change is in scope.

`--check` 仅预检：它运行 hostname 和窄格式 Docker inspect，跳过 mutation，
不是成功证据（not success evidence）。真实命令才会执行 temporary ->
canonical 的网络变更。仓库或运行时证据没有证明触发部署、重建或手工变更的
actor；that actor remains unknown。

```bash
bash -n scripts/reconcile-open-webui-admin-network.sh
scripts/reconcile-open-webui-admin-network.sh --self-test
uvx --from ansible-core==2.21.3 ansible-playbook playbooks/mypc-network-reconcile.yml -i inventories/mypc/hosts.ini --syntax-check
uvx --from ansible-core==2.21.3 ansible-playbook playbooks/mypc-network-reconcile.yml -i inventories/mypc/hosts.ini --list-tasks
uvx --from ansible-core==2.21.3 ansible-playbook playbooks/mypc-network-reconcile.yml -i inventories/mypc/hosts.ini --check -e mypc_deploy_enabled=true -e mypc_network_reconcile_approval=true
uvx --from ansible-core==2.21.3 ansible-playbook playbooks/mypc-network-reconcile.yml -i inventories/mypc/hosts.ini -e mypc_deploy_enabled=true -e mypc_network_reconcile_approval=true
```

唯一支持的恢复路径是由 playbook 调用的受保护事务脚本
`scripts/reconcile-open-webui-admin-network.sh --execute`。禁止按
network/container 名称手工执行 rollback mutation，也不得对 replacement
自动操作。脚本只允许从精确 temporary baseline 进入 transaction；canonical
是 no-op。

脚本在 hostname、窄格式 inspect、topology、forward mutation 或 rollback
failure 时 fail-closed：停止自动操作，标记 baseline not proven，交给人工
处置。只有四个 baseline IDs（Core/WebUI network、Proxy/Admin container）与
保存的 baseline topology 都通过精确重验，才可声称 restored；任一 ID drift
都禁止自动恢复 replacement。

## History 0030 migration lock and rollback contract

This is an opt-in contract for a selected VectA source that has passed the
history consumer checker. It does not authorize production execution by
itself.

The forward sequence is strict:

1. Confirm every controlled Fruit writer is running, then stop/quiesce the
   writers with the history preflight.
2. Capture the fresh PostgreSQL backup and complete the isolated restore
   rehearsal before applying the selected digest.
3. Run the exact Fruit digest once with a bounded PostgreSQL `lock_timeout`.
   The selected source must use the Drizzle PostgreSQL migrator, which owns one
   transaction for the migration; the `ACCESS EXCLUSIVE` lock taken by the
   final journal-owned loss CHECK migration (0030 or later) `v4_documents_type_chk` ALTER therefore remains held until that
    transaction commits. Do not split this into independent client statements.
4. Verify the 0029 → 0030 journal, five audit tables, forced RLS, and the
   first-class `loss` document CHECK while writers remain stopped.
5. Run a read-only schema smoke, then restart the writers only after every
   migration, journal/schema verification, and smoke step succeeds. Any
   failure keeps the writers stopped and emits the repair/rollback entry.

The operator rollback path is also writer-quiesced and uses a self-contained
rollback SQL transaction. It must lock `fruit.v4_documents` first and
`fruit.v4_historical_import_batches` second, run the non-empty-history guard,
then DROP/restore `v4_documents_type_chk` in that same transaction. The SQL
must contain `BEGIN;`, `SET LOCAL lock_timeout = '5s';`, and `COMMIT;`. The
rollback playbook is the only execution surface: it materializes the exact
journal-owned rollback file from the validated prior manifest and exact Fruit
image, verifies its hash, and invokes the protected PostgreSQL client inside
the playbook's shared-lease, writer-quiesced task. The SQL path is never
selected by migration number or exposed as a standalone operator command.
If audit or ledger facts exist, refuse schema rollback and use the append-only
compensation path instead.

The rollback playbook is operator-gated with
`mypc_deploy_enabled=true`, `history_rollback_enabled=true`, and
`history_rollback_approved=true`, plus the prior source SHA, manifest, and the
existing target-host lease. A failed workflow emits a non-secret
`RELEASE_CONTINUATION_ID` reference and records the workflow run, source SHA,
owner capability, and holder PID/start identity only in the protected host
record `/data/ocee/backups/release-continuations/<run>-<source-sha>.env`.
Operators must pass that reference to the canonical lease or rollback
playbook; the playbook reads the owner capability under `no_log` and never
requires an operator to guess or print the state token. Acquire or
operator-gated recover of that owner-token lease is a separate controlled
action; recovery is allowed only after the helper proves the recorded holder
is dead and the kernel lock is free. A live holder is never killed by
`recover`; terminal `release` is the only lifecycle action that may terminate
the verified owner holder. Its history play runs under the same host lease and
keeps writers stopped until the exact rollback SQL and schema verification
both succeed.

With the protected inventory, pinned Ansible core, and a previously validated
manifest already selected, the complete operator invocation is:

```bash
PRIOR_MANIFEST_PATH="/data/ocee/backups/release-manifests/$PRIOR_SOURCE_SHA.json"
python3 scripts/validate-mypc-digest-manifest.py \
  --require-history-provenance "$PRIOR_MANIFEST_PATH"
ANSIBLE_COLLECTIONS_PATHS="$ANSIBLE_COLLECTIONS_PATH" \
uvx --from ansible-core==2.21.3 ansible-playbook \
  -i "$MYPC_INVENTORY_FILE" \
  playbooks/mypc-release-rollback.yml \
  --limit mypc \
  -e mypc_deploy_enabled=true \
  -e history_rollback_enabled=true \
  -e history_rollback_approved=true \
  -e "release_continuation_id=$RELEASE_CONTINUATION_ID" \
  -e "rollback_manifest_selector_sha=$PRIOR_SOURCE_SHA"
```

`PRIOR_SOURCE_SHA`, `RELEASE_CONTINUATION_ID`, `MYPC_INVENTORY_FILE`, and
`ANSIBLE_COLLECTIONS_PATH` are operator-selected non-secret inputs. The
manifest must already be staged as
`/data/ocee/backups/release-manifests/$PRIOR_SOURCE_SHA.json`; the playbook
derives that path, checks realpath containment, validates JSON/provenance, and
only then loads it. The continuation record is similarly resolved from its
fixed protected root and checked against its workflow/source reference before
the lease owner is consumed. Do not put database credentials or API tokens in
the command or its logs. This playbook is the only history schema rollback
execution surface; do not copy the SQL into a standalone operator command or
run a standalone PostgreSQL file command.

Only after the rollback playbook returns success and its schema/health evidence
is recorded may the same owner release the lease through the canonical lease
playbook:

```bash
uvx --from ansible-core==2.21.3 ansible-playbook \
  -i "$MYPC_INVENTORY_FILE" \
  playbooks/mypc-release-lease.yml \
  --limit mypc \
  -e mypc_deploy_enabled=true \
  -e release_lock_action=release \
  -e "release_continuation_id=$RELEASE_CONTINUATION_ID"
```

If rollback or its evidence fails, do not run this release action: keep the
lease and writers stopped for the next operator-gated recovery. To verify a
continuation before rollback, use the same lease playbook with
`release_lock_action=verify` and `release_continuation_id`; to recover only a
stale lease, use `release_lock_action=recover`, the same reference, and the
independent `release_lock_recovery_approved=true` input. Both actions are
fail-closed when a live holder or held kernel lock is present.
