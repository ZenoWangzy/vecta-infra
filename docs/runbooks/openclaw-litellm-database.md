# `openclaw-litellm` database

Ticket 1052 (vecta). `openclaw-litellm` -- the LiteLLM proxy every fleet
instance's employee-facing traffic actually goes through (it answers to both
the `openclaw-litellm` and `litellm-proxy` network aliases on
`openclaw-enterprise_openclaw-net`; `fleet-gateway`'s `LITELLM_BASE_URL`
resolves through the `litellm-proxy` alias) -- has never had `DATABASE_URL`
set. Every database-backed LiteLLM endpoint (`/key/generate`, `/key/delete`,
`/key/update`) has therefore always returned
`500 Internal Server Error, Connect Proxy to database to generate keys` for
this instance, which is why `resolveInstanceLlmApiKey`
(`packages/fleet-gateway/src/llm-keys/instance-key.ts`) falls back to the
platform master key for 62 of 64 fleet instances: the fallback is not
laziness, it is the only branch that could ever succeed.

## What this is not

`openclaw-fruit-litellm` is a **separate** LiteLLM instance (its own compose
project `fruit-support-services`,
`deploy/fruit-support-services/docker-compose.yml`, container
`openclaw-fruit-litellm`, port `4001`) for the Fruit industry-pack tenant. It
already has its own working `DATABASE_URL`, pointed at a database literally
named `litellm` in the same `openclaw-postgres` instance. **Do not reuse that
database for this ticket** -- it is live, has real rows, and is owned by a
different service. This ticket adds a *new*, separate database named
`litellm_gateway`, distinct from both `openclaw_poc` (the app's own database)
and the pre-existing `litellm` database.

## Key-revocation history (evidence, not inference)

`llm_virtual_keys` (in `openclaw_poc`, the fleet-gateway app database) has
exactly 3 historical rows. All 3 have `litellm_base_url` pointing at
`http://openclaw-fruit-litellm:4000`, none at `litellm-proxy`/
`openclaw-litellm`:

| instance_id | status | litellm_base_url | created_at |
|---|---|---|---|
| `fruit-canary@openclaw.internal` | `revoked` | `openclaw-fruit-litellm:4000` | 2026-07-16 |
| `shiyao19900807@gmail.com` | `active` | `openclaw-fruit-litellm:4000/v1` | 2026-07-22 |
| `jiechen@vecta.com` | `active` | `openclaw-fruit-litellm:4000/v1` | 2026-08-22 |

Cross-checked against the `litellm` database's own `LiteLLM_VerificationToken`
table: the two `active` rows' tokens are present there; the `revoked` row's
token is **absent** -- i.e. `/key/delete` did not just flip a status flag in
our own bookkeeping, the token was actually removed from LiteLLM's own state.

**Conclusion: key generation and revocation has worked, but only ever for
the separate Fruit-tenant LiteLLM instance, and never (until this ticket) for
`openclaw-litellm` itself.** Proven live on 2026-09-07 after this ticket's fix:
a synthetic key generated against `openclaw-litellm` (`/key/generate`, 200,
real token recorded in its `LiteLLM_VerificationToken`), a real
`/chat/completions` call against it succeeded, and `/key/delete` removed it
(200, and independently confirmed absent from `LiteLLM_VerificationToken`
afterward rather than trusting the response). For the regular employee path
-- `openclaw-litellm` / `litellm-proxy`, the one this ticket is about, the one
`resolveInstanceLlmApiKey` and all 64 fleet instances actually use -- there is
zero evidence any virtual key has ever existed: 0 of 3 rows point there, and
`DATABASE_URL` has been absent for at least the container's current 10-day
uptime (started 2026-08-28, the same day as `cb55c8d`, the most recent commit
to touch `roles/infra-services/tasks/litellm.yml`). No employee key has ever
been issued through this path, so none has ever needed revoking -- and none
*could* have been revoked, because `/key/delete` against this instance has
always 500'd for the same reason `/key/generate` has.

## How `openclaw-litellm` is actually deployed

`openclaw-litellm` is deployed by the Ansible role
`roles/infra-services/tasks/litellm.yml` (`community.docker.docker_container`,
last run 2026-08-28 per `cb55c8d`), **not** by the 33-file `docker compose -f`
chain that also happens to declare a `litellm-proxy` service with a matching
image/port/config-path in `migration-compose.config.yml`. `docker inspect
openclaw-litellm` carries no `com.docker.compose.project` label, confirming
the running container was never created by `docker compose up`. That compose
declaration is stale/vestigial for this service -- worth a follow-up ticket to
either wire it up for real or delete it, but out of scope here. **This fix
goes into the Ansible role**, because that is what actually recreates this
container, and because a fix landed only in the compose chain would get
silently reverted the next time someone reruns `infra.yml` for an unrelated
LiteLLM change (routing, provider keys, etc.).

## Architecture: new database in the existing `openclaw-postgres` instance

Options considered:

1. **New database in `openclaw-postgres` (chosen).** Zero new containers,
   volumes, or hosts. `openclaw-postgres` is already the shared instance for
   `openclaw_poc` and (per the discovery above) for Fruit's own `litellm`
   database -- a third small database here is consistent with existing
   practice, not a new pattern. Cost: shares fate with `openclaw-postgres`'s
   own availability, but every other service that depends on this instance
   already accepts that; LiteLLM's own state is tiny (the existing `litellm`
   database for one tenant is 17 MB against `openclaw_poc`'s 669 MB) and
   backup/restore for it is correspondingly cheap (see below).
2. **A dedicated LiteLLM Postgres instance.** Rejected: a new container, a
   new volume, a new backup/monitoring surface, and a new failure domain, to
   hold what is -- for the employee-facing instance in production today, with
   64 fleet instances behind one master key -- essentially empty bookkeeping
   state. Nothing about LiteLLM's connection-pooling or write volume comes
   close to justifying isolating it from the instance every other tenant
   database already shares.
3. **Reuse the existing `openclaw_poc` role for the new database** (matching
   how `openclaw-fruit-litellm` was set up). Rejected for the *role*, kept for
   the *instance*: reusing `openclaw_poc` would mean either reading its
   existing password (this project's credential rule forbids that outright)
   or re-deriving a `DATABASE_URL` for the new database without ever knowing
   the password -- solvable, but it also hands LiteLLM's Prisma migrations a
   role that can already reach the production app schema. A dedicated role
   (`litellm_gateway`, freshly generated password, owns only its own
   database) costs one more `CREATE ROLE` and buys least-privilege for a
   service whose job is exactly key-management, at no extra infrastructure.

No new named volume is needed: LiteLLM's Prisma-managed rows live inside
`openclaw-postgres`'s existing `openclaw-enterprise_postgres_data` volume
(confirmed via `docker inspect --format '{{json .Mounts}}' openclaw-postgres`
-- one named volume, no anonymous mounts). `litellm-proxy` itself has no data
volume of its own either way (its only mount is the read-only
`config.yaml` bind).

## Files

| Resource | Location |
| --- | --- |
| Role/database provisioning (tracked, idempotent, no values) | `deploy/openclaw-litellm/provision-database.sh` |
| Env key template (tracked, no values) | `deploy/openclaw-litellm/litellm-database.env.example` |
| Real env file (host only, never committed, mode 600) | `mypc:/data/ocee/deploy/openclaw-litellm/litellm-database.env` |
| Ansible wiring | `roles/infra-services/tasks/litellm.yml` (`DATABASE_URL`, fail-closed if unresolved) |
| Ansible var | `inventories/mypc/group_vars/mypc.yml` (`litellm_database_url`, no default) |
| Backup + restore drill | `scripts/litellm-database-backup.sh` |
| Role name | `litellm_gateway` (LOGIN, owns only its own database) |
| Database name | `litellm_gateway` (Postgres instance: `openclaw-postgres`) |

## Provisioning (one time)

This repo has no CI job that ships its contents onto mypc automatically (same
caveat as `rag-service-recovery.md` and `fruit-feishu-gateway-recovery.md`).
Get a checkout of this branch/commit onto the host first -- a throwaway
`git clone` under `/data/ocee/deploy/` is fine, it does not need to persist --
then, on mypc:

```bash
cd <that checkout>/deploy/openclaw-litellm && ./provision-database.sh
```

Idempotent: a no-op if `litellm-database.env` already exists; refuses to
guess (rather than silently create a second role) if the role exists without
a matching env file. Uses the existing `openclaw_poc` role only to issue
`CREATE ROLE`/`CREATE DATABASE` -- it does not touch `postgres.yml`'s own
container-recreation path, so it is not gated by
`mypc_stateful_services_enabled`/`mypc_postgres_adoption_approved`.

## Deploying LiteLLM with its database wired up

1. **Take the production deploy lock first** (full contract in
   `docs/runbooks/production-deploy-lock.md`):

   ```bash
   ssh mypc 'bash -s -- acquire --holder "<you>" --session "<url>" \
     --containers "openclaw-litellm"' < scripts/deploy-lock.sh
   ```

2. Run the Ansible role with `LITELLM_DATABASE_URL` sourced from the
   provisioned env file. This uses the same one-service allowlist gate as
   every other stateful-adoption run in this repo (see
   `docs/runbooks/mypc-nexus-image-adoption.md`'s "Data-Layer Adoption
   Sequence") even though LiteLLM itself is not the thing being gated here --
   `roles/infra-services/tasks/main.yml` only imports `litellm.yml` (and
   skips postgres/redis/minio/clickhouse entirely) when the allowlist is
   exactly `["litellm"]`:

   ```bash
   cd <that checkout>
   set -a && . /data/ocee/deploy/openclaw-litellm/litellm-database.env && set +a
   export PATH=/home/hige/.local/bin:$PATH   # uv/uvx live under this user's home
   uvx --from ansible-core --with docker --with requests ansible-playbook \
     playbooks/infra.yml \
     -i inventories/mypc/hosts.ini -e ansible_host=mypc -e ansible_connection=local \
     -e mypc_deploy_enabled=true \
     -e mypc_stateful_services_enabled=true \
     -e '{"mypc_stateful_service_allowlist":["litellm"]}' \
     --tags infra-services --limit mypc
   ```

   Notes from actually running this (2026-09-07):
   - `ansible_connection=local` is required: the tracked `hosts.ini` targets
     `mypc-host.example.com` with `ansible_host` overridden to `mypc`, but
     root's own `~/.ssh/config` on mypc has no `mypc` alias and self-SSH
     loopback as root is not set up. Running the module locally (we are
     already on mypc) sidesteps that entirely and is the simpler fix.
   - `--with docker --with requests` is required: the bare
     `uvx --from ansible-core` environment does not include the `docker`/
     `requests` Python packages `community.docker.docker_container` needs.
   - **Never add `--diff` to a `--check` (or real) run of this playbook.**
     `docker_container`'s diff output prints the full container environment,
     including every secret in it, straight to your terminal. This was
     learned the expensive way during this ticket -- see the Incident
     section below.
   - The role's own assert fails the play before touching the container if
     `litellm_database_url_effective` resolves empty -- it will not silently
     start LiteLLM without a database again.
   - The post-adoption regression script
     (`scripts/mypc-data-layer-regression.sh --service litellm --phase
     after`) can fail once on a *first* deploy against a fresh database:
     LiteLLM runs all pending Prisma migrations on first connect (dozens of
     files, ~15-20s here), during which `/health/liveliness` is unreachable
     and the regression script's retry budget is shorter than that. Re-run
     the regression script by hand after confirming the container is
     actually healthy (`curl http://127.0.0.1:4000/health/readiness`) rather
     than treating one failed attempt as a broken deploy. This is a one-time
     cost -- later restarts against the same already-migrated database do
     not re-run migrations.

3. Verify with a real request, not a health check: an actual
   `/key/generate` call against a synthetic instance id, a real
   `/chat/completions` round trip proving existing traffic still works, and
   a real `/key/delete` of the generated key -- with the deletion verified by
   querying `LiteLLM_VerificationToken` directly, not assumed from a 200.
   Fetch `LITELLM_MASTER_KEY` with `docker exec openclaw-litellm printenv
   LITELLM_MASTER_KEY` inside the *same* remote script that uses it, and
   mask the `key` field of `/key/generate`'s response before it leaves the
   host -- the `token`/`token_id` field is a lookup id already stored in
   plaintext in `llm_virtual_keys.litellm_token_id`, not a bearer secret, and
   is fine to see. See the ticket for the full recorded evidence.

4. Release the lock.

## Incident: `--diff` leaked live secrets during this ticket's first deploy attempt

The first `--check --diff` run of step 2 above printed the full
`docker_container` environment diff to the operator's terminal, which
included: the freshly-generated `litellm_gateway` database password, and the
pre-existing `LITELLM_MASTER_KEY`, `DEEPSEEK_API_KEY`, `ZAI_API_KEY`, and
`MOONSHOT_API_KEY` values. The `litellm_gateway` role/database (self-owned,
not yet in use by any running container) were immediately dropped and
re-provisioned with a new password before the real deploy ran. The four
pre-existing provider/master-key secrets were **not** rotated as part of this
ticket -- that is a separate, higher-blast-radius action (multiple containers
and, for the provider keys, third-party billing accounts reference them) that
needs an explicit decision, not a unilateral one made mid-deploy. Do not pass
`--diff` to this playbook; if you need to see what would change, diff the
non-secret fields only (image, ports, volumes, restart policy, memory/cpu --
see `docker inspect` structural fields), never the `env` block.

## Backup and restore rehearsal

```bash
# Backup (preflight, then real):
ssh mypc 'cd <vecta-infra checkout> && scripts/litellm-database-backup.sh backup'
ssh mypc 'cd <vecta-infra checkout> && scripts/litellm-database-backup.sh backup --execute'

# Restore drill into a throwaway database, verified by table-count diff, then dropped:
ssh mypc 'cd <vecta-infra checkout> && scripts/litellm-database-backup.sh restore-drill \
  --backup-file /data/ocee/backups/litellm-gateway/litellm-gateway-<ts>.dump --execute'
```

This is a narrower backup story than a whole-instance one: `openclaw-postgres`
itself currently has no systematic backup at all (checked: no cron entry, no
systemd timer, and `scripts/hermes-fleet-state-backup.sh` backs up fleet
instance rows and container filesystem state, not a database dump). That gap
predates this ticket and is out of scope to close for the whole instance
here; this runbook only commits to the same bar for the *new* database this
ticket adds, matching #1036 (declared, with a real restore rehearsal, not
just "the container reports healthy").
