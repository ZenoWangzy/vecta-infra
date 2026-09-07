# `vecta-fruit-industry-pack-canary` recovery

Ticket 150 (vecta). Fourth service in this pattern:
`openclaw-fruit-feishu-gateway` (ticket 132) -> `openclaw-rag-service`
(ticket 145) -> `openclaw-wecom-contact-sync` (ticket 145) -> this one. This
is also the **last** of the 10 real `DATABASE_URL` holders that had no
resolvable compose definition -- unblocking it is what unblocks the postgres
superuser password rotation (tickets 149/151). Full inventory is in the vecta
repo:
`.scratch/fruit-v41-daily-reconciliation/issues/150-the-only-production-fruit-pack-instance-is-named-canary.md`.
This doc only covers the mechanics; read
`docs/runbooks/fruit-feishu-gateway-recovery.md` first if this is your first
time using this pattern.

## The name is the trap. Read this before touching anything matching `*canary*` on mypc

`vecta-fruit-industry-pack-canary` is **the sole production
fruit-industry-pack instance** -- not a canary in the disposable sense, not a
staging copy. It carries ~330 req/h of real MCP-over-SSE traffic for Fruit
tenant employees, and it is the resolution target of `openclaw-fleet-gateway`'s
own `FRUIT_PACK_MCP_URL` env var (`http://vecta-fruit-industry-pack-canary:8002/mcp`,
confirmed live) as well as a hardcoded MCP tool URL baked into **53 of 75**
live employee Hermes runtime configs on the host
(`/data/ocee/data/instances/*/hermes/config.yaml`). A rename was evaluated
and rejected -- see "Why not just rename it" below. The marker instead: this
runbook, the block comment at the top of
`deploy/fruit-industry-pack/docker-compose.yml`, and the
`vecta.production=true` / `vecta.disposable=false` labels on the container
itself (inspectable with `docker inspect vecta-fruit-industry-pack-canary
--format '{{json .Config.Labels}}'` without needing this repo checked out).
**Before any container-cleanup pass deletes anything matching `*canary*` on
`mypc`, it must check for these labels first.**

## Why not just rename it (cost accounting, not a guess)

Checked, not assumed, every consumer that resolves this container by name:

1. **`scripts/ops/fruit-account-onboard.sh` and
   `scripts/fruit-production-query-soak.mjs`** (vecta repo) each hardcode the
   name as an overridable default (`FRUIT_PACK_CONTAINER`/`FRUIT_CONTAINER`
   env vars) -- one line each, cheap.
2. **`openclaw-fleet-gateway`'s `FRUIT_PACK_MCP_URL` env var** resolves this
   container by Docker network name (`http://vecta-fruit-industry-pack-canary:8002/mcp`,
   read directly off the running container, not inferred). Traced through
   the full 31-file `-f` chain (`docker compose -p openclaw-enterprise
   <chain> config`-style resolution, per F76): only ONE file in the chain
   sets this key, `/data/ocee/migration-compose.config.yml` (the base file;
   none of the 30 release overrides touch it). Renaming means editing that
   base file and recreating `openclaw-fleet-gateway` -- a much
   higher-blast-radius container than this one, shared by every tenant, not
   just Fruit.
3. **53 of 75 live employee Hermes runtime configs**
   (`/data/ocee/data/instances/<employee-id>/hermes/config.yaml`) have this
   container's URL (`http://vecta-fruit-industry-pack-canary:8002/mcp`)
   baked in directly as a tool endpoint, independent of fleet-gateway's own
   env var. This is not a guess -- counted directly:
   `grep -rl "fruit-industry-pack-canary" /data/ocee/data/instances/*/hermes/config.yaml
   | wc -l` returned 53 against 75 total instance directories. Fixing a
   rename here means regenerating or patching 53 live runtime configs and
   running `refresh-config` against every one of them, individually verified
   (F80: a `200` from `refresh-config` proves nothing by itself -- the actual
   effect has to be checked per file), not a bulk find-and-replace fired at
   production.

Item 3 alone makes a rename disproportionate to the benefit (a cosmetic
name). The marker is the correct answer: it costs one compose file, one
label, and this doc, versus a coordinated multi-service production change
touching fleet-gateway and 53 employee runtimes for no functional gain.

## What this is for

Before this ticket, `vecta-fruit-industry-pack-canary` had no compose
project, no systemd unit, no crontab entry --
`RestartPolicy=unless-stopped` only survives a Docker daemon restart for a
container the daemon already knows about. A `docker rm` or a host
reprovision loses it for good. `scripts/ops/fruit-account-onboard.sh`'s
`recreate_container()` looks like a recovery path but is not one: it
bootstraps by `docker inspect`-ing THIS running container, so it fails
exactly when you'd need it -- once the container is gone. Same
self-bootstrapping flaw already fixed for `openclaw-fruit-feishu-gateway`
(ticket 132).

`deploy/fruit-industry-pack/docker-compose.yml` is the fix.

**No prior Ansible declaration exists for this container** (unlike
`rag_service_mypc.yml` / `wechat_contact_sync_mypc.yml`, which existed but
were unreachable and self-referential -- F78). `find roles -iname
"*fruit*"` under `vecta-infra` returns nothing that declares this specific
container; the only "fruit" references in Ansible
(`fleet_gateway_mypc.yml`, `inventories/mypc/group_vars/mypc.yml`) are about
a *bind mount into fleet-gateway*, unrelated to this standalone container.
Nothing to reconcile with or delete -- this is a clean addition, not a
correction.

## Files

| Resource | Location |
| --- | --- |
| Compose file (tracked) | `deploy/fruit-industry-pack/docker-compose.yml` |
| Env key template (tracked, no values) | `deploy/fruit-industry-pack/fruit-industry-pack.env.example` |
| Real env file (host only, never committed) | `mypc:/data/ocee/deploy/fruit-industry-pack/fruit-industry-pack.env` (root:root, `600`) |
| Compose project name | `fruit-industry-pack` |
| Container name | `vecta-fruit-industry-pack-canary` (unchanged -- see "Why not just rename it") |
| Network | `openclaw-enterprise_openclaw-net` (external -- real dependency: resolves `postgres`/`minio`/`fleet-gateway` by container name over it, no compose lifecycle dependency on the 31-file `-f` chain) |
| Port | `127.0.0.1:18002:8002` (loopback only, matches live state) |

The real env file was produced once, on 2026-09-07, by dumping the then-live
container:

```bash
ssh mypc 'docker inspect vecta-fruit-industry-pack-canary \
  --format "{{range .Config.Env}}{{println .}}{{end}}" \
  > /data/ocee/deploy/fruit-industry-pack/fruit-industry-pack.env
chmod 600 /data/ocee/deploy/fruit-industry-pack/fruit-industry-pack.env'
```

That file is now the source of truth going forward. There is no automation
keeping it in sync with future env changes to this container -- re-run the
dump above (or hand-edit the specific key) after any such change, same
caveat as every prior runbook in this series.

## Two things checked before touching anything (do not skip these for the next orphan container)

1. **Is it actually stateless?** `docker inspect --format '{{json .Mounts}}'`
   returns an empty array -- no named volumes, no anonymous volumes. Two env
   keys look like filesystem paths (`FRUIT_MODEL_PATH`,
   `FRUIT_ROLE_MATRIX_PATH`); both were confirmed live to resolve under
   `/app/` (baked into the image), not to any host bind -- checked by
   reading each value into a shell variable on the host and testing the
   prefix, never printed. Simpler than `openclaw-rag-service` (2 real
   mounts) or `onlyoffice` (`lessons/RULES.md` D26, 5 undocumented anonymous
   volumes) -- this one has none at all.
2. **Is a parallel verification instance safe against this specific
   container's connection model?** Per ticket 132's own explicit caveat,
   this does not generalize from one service to the next -- it was
   evaluated for real:
   - **Session model**: `packages/fruit-industry-pack/src/app.ts` keeps MCP
     sessions in `const sessions = new Map<string, FruitMcpSession>()`,
     keyed per-connection, in-process memory. A parallel instance starts
     with its own empty map; it cannot collide with, evict, or steal any
     session the real instance already holds. This is structurally
     different from `openclaw-fruit-feishu-gateway`'s single shared
     app-level Feishu WebSocket (ticket 132 found that risk by watching logs
     in real time) -- there is no equivalent shared external long-lived
     connection here to collide over.
   - **`/healthz` touches postgres for real, not a mock.** Unlike
     `openclaw-rag-service`'s `/healthz` (DB-free), this one's readiness
     checks run `SELECT 1` and two role-shape assertions
     (`assertFruitRuntimeDatabaseRole`, and `validateFruitV4WriterDatabaseRole`
     when the V4 writer pool is configured) against the real
     `DATABASE_URL`. Read the implementation
     (`packages/fruit-industry-pack/src/db/validate-runtime-role.ts`) before
     trusting this: both are pure `SELECT` queries against
     `pg_roles`/catalog views, no `INSERT`/`UPDATE`/`DDL` anywhere in either
     function. A verify instance hitting `/healthz` opens one more
     short-lived read-only connection to the same production database the
     real instance already talks to -- real, not simulated, but provably
     non-mutating.
   - **No eager outbound calls at boot beyond the DB/readiness checks
     above.** `src/cli.ts` is 17 lines: bootstrap, `main()`, register
     SIGINT/SIGTERM. No outbound `fetch`/registration call in `bootstrap.ts`
     or `server.ts` before the HTTP server binds. `FRUIT_PLATFORM_BASE_URL`
     is not referenced anywhere in either file -- it's used elsewhere (skill
     publishing / auth verification on inbound requests), not fired
     automatically on every boot.

## Recreating the container from nothing

1. **Take the production deploy lock first** (full contract in
   `docs/runbooks/production-deploy-lock.md`):

   ```bash
   ssh mypc 'bash -s -- acquire --holder "<you>" --session "<url>" \
     --containers "vecta-fruit-industry-pack-canary"' < scripts/deploy-lock.sh
   ```

2. Confirm the two tracked files above are present on the host at
   `/data/ocee/deploy/fruit-industry-pack/` alongside the real
   (non-`.example`) env file. Copy `docker-compose.yml` there by hand if it
   isn't already -- same as every prior runbook in this series, this project
   has no CI job shipping it automatically.

3. Bring it up:

   ```bash
   cd /data/ocee/deploy/fruit-industry-pack
   docker compose -p fruit-industry-pack -f docker-compose.yml up -d
   ```

4. Verify before trusting it:
   - `docker inspect vecta-fruit-industry-pack-canary` shows the expected
     image, network, port mapping (`127.0.0.1:18002:8002`), restart policy,
     empty mounts, and the `vecta.production=true` / `vecta.disposable=false`
     labels.
   - `curl -sf http://127.0.0.1:18002/healthz` returns `{"ok":true,...}` --
     this is a real postgres round trip, a non-200 here can mean the
     database is actually unreachable, not just a container-level problem.
   - A real MCP-over-SSE round trip against `/mcp` actually completes --
     "the healthcheck passed" is not the bar (this ticket's own criterion,
     echoing 132/145's).

5. Release the lock.

## Status: live since 2026-09-07 (this ticket, real full round trip)

Unlike `openclaw-rag-service` (one round trip only, justified by continuous
live traffic) and like `openclaw-wecom-contact-sync` (both round trips, no
published port), this container got the harder proof directly: **the actual
production `docker rm -f` + recreate-from-declaration round trip**, done
once, under the deploy lock, because this ticket's whole point was proving
zero-self-reference recovery for the last of the 10 real `DATABASE_URL`
holders:

1. `docker inspect vecta-fruit-industry-pack-canary` before: image, entrypoint,
   command, working_dir, user, restart policy, port mapping, network, and 36
   env keys diffed field-for-field against `docker compose -p
   fruit-industry-pack -f docker-compose.yml config --format json` --
   env compared by **key set only** (never values) throughout; every
   non-secret field matched exactly, including ports (`127.0.0.1:18002`,
   never `0.0.0.0`) and the empty mounts/healthcheck.
2. **Parallel instance first** (`vecta-fruit-industry-pack-canary-verify-150`,
   loopback port `127.0.0.1:18092`, no other consumer of that name existed):
   brought up clean from the exact same compose file + real env snapshot,
   `/healthz` returned `{"ok":true,...,"checks":{"model":true,"runtime":true,
   "postgres":true,"processingImage":true}}` -- all four readiness checks,
   including two real (read-only, confirmed by reading
   `assertFruitRuntimeDatabaseRole`/`validateFruitV4WriterDatabaseRole`
   before trusting them) round trips to the production database. Real
   container's `StartedAt`/`RestartCount` were unchanged throughout. Torn
   down cleanly (`docker compose down`); confirmed no orphaned container or
   network.
3. **Then the actual full round trip**: `docker rm -f
   vecta-fruit-industry-pack-canary` (confirmed gone -- `docker inspect`
   returned "no such object"), followed immediately by `docker compose -p
   fruit-industry-pack -f docker-compose.yml up -d` from
   `/data/ocee/deploy/fruit-industry-pack/` with **zero reference to any
   running container** -- the compose file and env file are the only inputs.
   New container got a new ID (`0efc02f9c8...`, was `c1f5aebb00...`),
   `StartedAt` reset, `RestartCount=0`, `com.docker.compose.project=
   fruit-industry-pack` label present, `vecta.production`/`vecta.disposable`
   labels present.
4. **`/healthz` passed immediately** on the recreated container (all four
   checks true again -- same real DB round trip as step 2, this time against
   the container real traffic depends on).
5. **The real bar, not the healthcheck**: within 120 seconds of the
   recreate, `docker logs` on the new container showed **5 distinct real
   employees** completing a genuine `"Fruit V4 MCP SSE connected"` handshake
   -- actual Fruit tenant employees' Hermes runtimes resolving the
   (unchanged) container name over Docker's embedded DNS and reconnecting on
   their own, zero errors (`level>=50` count: 0), `RestartCount` still 0. No
   synthetic test employee or crafted request was used for this check --
   this was real traffic finding its way back on its own, which is the
   actual proof this ticket needed, not a manufactured one.
6. Directly confirmed `openclaw-fleet-gateway` (whose own `FRUIT_PACK_MCP_URL`
   env var resolves this container by the same unchanged name) can still
   reach it: `docker exec openclaw-fleet-gateway wget -qO-
   http://vecta-fruit-industry-pack-canary:8002/healthz` returned the same
   healthy response.
7. Deploy lock released; `status` confirmed `FREE` immediately after.

No rename was needed for any of this to work, because the container name
was deliberately kept unchanged (see "Why not just rename it" above) --
Docker's embedded DNS re-resolved the name to the new container's IP
automatically, so neither `openclaw-fleet-gateway` nor any of the 53 live
employee Hermes configs needed to change.

## Known gap

Tag advance follows the same policy as `openclaw-fruit-feishu-gateway`
(ticket 132's "Known gap"): this file freezes the *current* running state.
Advancing the pinned tag is a separate production change and belongs in its
own follow-up, not bundled into the file that proves the recreate path
works.
