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
the separate Fruit-tenant LiteLLM instance.** For the regular employee path
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

```bash
ssh mypc 'cd /data/ocee/deploy/openclaw-litellm && ./provision-database.sh'
```

Idempotent: a no-op if `litellm-database.env` already exists; refuses to
guess (rather than silently create a second role) if the role exists without
a matching env file.

## Deploying LiteLLM with its database wired up

1. **Take the production deploy lock first** (full contract in
   `docs/runbooks/production-deploy-lock.md`):

   ```bash
   ssh mypc 'bash -s -- acquire --holder "<you>" --session "<url>" \
     --containers "openclaw-litellm"' < scripts/deploy-lock.sh
   ```

2. Run the Ansible role with `LITELLM_DATABASE_URL` sourced from the
   provisioned env file, targeting only the LiteLLM service:

   ```bash
   ssh mypc 'cd <vecta-infra checkout> && set -a && \
     . /data/ocee/deploy/openclaw-litellm/litellm-database.env && set +a && \
     uvx --from ansible-core ansible-playbook playbooks/infra.yml \
       -i inventories/mypc/hosts.ini -e ansible_host=mypc \
       -e mypc_deploy_enabled=true --tags infra-services --limit mypc'
   ```

   The role's own assert fails the play before touching the container if
   `litellm_database_url_effective` resolves empty -- it will not silently
   start LiteLLM without a database again.

3. Verify with a real request, not a health check: an actual
   `/key/generate` call against a synthetic instance id, followed by a real
   `/key/delete` of the key it returned, and a real `/v1/chat/completions`
   (or equivalent) round trip proving existing traffic still works. See the
   ticket for the exact evidence recorded.

4. Release the lock.

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
