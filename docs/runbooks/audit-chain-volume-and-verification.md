# `AUDIT_DIR` volume, chain-survival rehearsal, and retention (vecta#1036)

Executed 2026-09-07. Every command and output below is what actually ran on
`mypc` and in an isolated rehearsal — not an idealized flow.

## 0. What was wrong

`fleet-gateway`'s signed audit chain (`packages/fleet-gateway/src/audit/{writer,consumer,integrity}.ts`
in `vecta`) writes HMAC-chained JSONL lines to `AUDIT_DIR`. `AUDIT_DIR` was never a
declared mount anywhere in the `openclaw-enterprise` compose chain, so it lived in
`openclaw-fleet-gateway`'s writable container layer and was destroyed on every
`docker rm` + recreate — routine during the declarative-recovery work this project
has been doing (see #1026). The chain re-signs cleanly after each loss, so nothing
about it ever looked wrong. Full defect writeup: `vecta` issue #1036 (`vecta-infra`
has Issues disabled, hence the cross-repo tracking).

## 1. Investigation — read-only, before touching anything

`docker inspect --format '{{json .Mounts}}' openclaw-fleet-gateway` (lessons/RULES.md
D26 — get the full mount table before writing any declarative recreate config, don't
infer it from the existing IaC):

```
[bind /data/ocee/deploy/instances/shared/plugins -> /app/shared-plugins,
 bind /data/ocee/packages/rag-service/knowledge -> /app/knowledge,
 bind /data/ocee/infra/litellm/config.yaml -> /app/litellm-config.yaml,
 bind /var/run/docker.sock -> /var/run/docker.sock,
 bind /data/ocee/data/instances -> /app/data/instances,
 bind /data/ocee/templates -> /app/templates]
```

Six bind mounts. **Zero volume-type mounts — not even an anonymous one** at
`/app/data/audit`. D26's "reference the existing anonymous volume by its current
hashed name" doesn't apply here: there is no pre-existing volume of any kind to
preserve. `AUDIT_DIR` itself is unset in the container's env — confirmed both via
`docker exec openclaw-fleet-gateway printenv AUDIT_DIR` (empty) and via the
*merged* config over the live 31-file `-f` chain (`docker compose -p
openclaw-enterprise <chain> config --format json | jq '.services["fleet-gateway"].environment.AUDIT_DIR'`
→ `null`), per F76 (an override file elsewhere in the chain could in principle set
it — confirmed none does). The app falls back to its own code default (`./data/audit`),
which resolves under the container's cwd (`/app`) to `/app/data/audit`.

Data actually present in the container at investigation time: one file,
`/app/data/audit/2026-09-07.jsonl`, 14 lines spanning `15:03:04.951Z`–`15:25:38.492Z`
(container had been up 29 minutes). This is the data the fix had to carry forward,
not discard.

## 2. Isolated rehearsal — proving the mechanism before touching production

Ticket boundary requires proving container recreation survives the fix *before*
applying it in production. Rehearsed with the **real, already-built** fleet-gateway
image's own compiled `integrity.js` (`computeEntryHash`) — not a stand-in hash
function — so the rehearsal chain is genuine HMAC-chained output from the same
code that runs in production, just without needing the full Postgres/Redis/HMAC-secret
boot path (which isn't what's being tested; the mechanism under test is Docker's
volume-vs-writable-layer behavior across `docker rm` + recreate, not the app's
audit logic, which is already covered by `integrity.test.ts`). Isolated name,
isolated volume, isolated port irrelevant (no listening service needed):

```
$ docker volume create ticket1036-audit-rehearsal
ticket1036-audit-rehearsal

$ docker run --rm -v ticket1036-audit-rehearsal:/audit --user 0 \
    --entrypoint chown 127.0.0.1:8082/fleet-gateway:60f14660ea87... -R 1000:1000 /audit

# generation 1 (container A), writes 5 real HMAC-chained lines using the image's
# own dist/audit/integrity.js, then exits (--rm)
$ docker run --rm --name ticket1036-gen1 --user 1000:1000 \
    -v ticket1036-audit-rehearsal:/audit ... /script.js 5
wrote 5 lines, pid 1

$ docker run --rm --user 1000:1000 -v ticket1036-audit-rehearsal:/audit \
    --entrypoint node <image> -e "<print lineCount + first/last timestamp, no hash fields>"
line_count=5
first=2026-09-07T15:44:31.665Z seq=0
last=2026-09-07T15:44:31.786Z seq=4

$ docker ps -a --filter name=ticket1036-gen1
(empty — container A is fully gone, exactly like docker rm during a real recreate)

# generation 2 (container B — a brand-new container, new container ID),
# appends 3 more lines to the SAME volume, chaining from the last hash it
# reads out of the file itself (real code, never printed)
$ docker run --rm --name ticket1036-gen2 --user 1000:1000 \
    -v ticket1036-audit-rehearsal:/audit ... /script.js 3
wrote 3 lines, pid 1

$ docker run --rm --user 1000:1000 -v ticket1036-audit-rehearsal:/audit \
    --entrypoint node <image> -e "<same print>"
line_count=8
first=2026-09-07T15:44:31.665Z seq=0
last=2026-09-07T15:44:32.617Z seq=2
```

**Judgment criteria, not exit codes**: line count went 5 → 8 (not reset to 3), and
the *first* timestamp after the recreate is byte-identical to the first timestamp
before it (`15:44:31.665Z`), proving the five pre-recreation entries survived in
place rather than being regenerated. No hash value was ever printed or compared —
survival is demonstrated by entry count and timestamp range only, per this
project's hash ban.

A real gotcha surfaced during the rehearsal and folded into the production steps
below: the **first** attempt (before the `chown` step) failed with `EACCES:
permission denied` — a freshly created Docker volume's mount point is owned by
`root`, but fleet-gateway runs as `uid:gid 1000:1000`
(`docker inspect --format '{{.Config.User}}' openclaw-fleet-gateway`). This is
exactly lessons/RULES.md **D1** ("need a named volume, chown via an init
container, not `docker exec` into the running container and not the main
entrypoint") — reproduced here rather than assumed, then handled per D1 in
production (§3).

Cleanup: `docker rm -f ticket1036-gen1 ticket1036-gen2 2>/dev/null; docker volume rm
ticket1036-audit-rehearsal` — confirmed `docker volume ls -f
name=ticket1036 -q` empty afterward. Nothing rehearsal-related left on the host.

## 3. Production adoption (deploy-lock window `15:59:50Z`–`16:07:14Z`, 2026-09-07)

Boundary: adding a mount to a running service is a production change. Took
`scripts/deploy-lock.sh` (`status` → `FREE` first), holder `ticket-1036-agent`,
containers `openclaw-fleet-gateway`.

**3.1 Fresh re-snapshot immediately before mutating anything** (time had passed
since §1; F76/D15 discipline — re-verify, don't trust an earlier read): still 6
bind mounts, still zero volume mounts. Audit file had grown to **40 lines**,
`15:03:04.951Z`–`15:50:02.646Z` (this is the real "before" baseline the fix had
to preserve, not the smaller one from the read-only investigation).

**3.2 Create the volume and seed + chown it as a one-shot init container (D1)**,
never via `docker exec` into the live container and never relying on the image's
own entrypoint:

```
$ docker volume create fleet_gateway_audit_data
$ docker create --name vecta1036-audit-seed --user 0 \
    -v fleet_gateway_audit_data:/audit <image> chown -R 1000:1000 /audit
$ docker cp openclaw-fleet-gateway:/app/data/audit/. /tmp/vecta1036-audit-seed/
$ docker cp /tmp/vecta1036-audit-seed/. vecta1036-audit-seed:/audit/
$ docker start -a vecta1036-audit-seed
init container exit code: 0
```

(`docker cp` doesn't support container-to-container copies directly, hence the
host-tmp staging step — the staged content is audit JSONL data, not credentials,
so a brief host tmp file is not a hash-ban or credential-discipline concern; it
was removed immediately after.)

Verified post-chown ownership (`node:node`, matching the app's runtime user) and
content — **40 lines, same `15:03:04.951Z`–`15:50:02.646Z` range** — before the
volume ever touched the live container.

**3.3 Add the new overlay to the live `-f` chain and validate before applying**,
same mechanics as `docs/runbooks/gateway-healthchecks-and-watch.md` §2:

```
$ scp deploy/gateways/compose.audit-volume.yml \
    mypc:/data/ocee/releases/gateway-audit-volume/compose.audit-volume.yml
$ CHAIN=$(docker inspect openclaw-fleet-gateway --format \
    '{{index .Config.Labels "com.docker.compose.project.config_files"}}' \
    | tr ',' '\n' | sed 's,^, -f ,' | tr -d '\n')   # 33 files, live chain grew since §1
$ docker compose -p openclaw-enterprise $CHAIN \
    -f /data/ocee/releases/gateway-audit-volume/compose.audit-volume.yml config -q
(exit 0)
```

Resolved config confirmed (structural fields only, `jq`, no secrets) before
applying: `fleet-gateway.volumes` includes `{type: volume, source:
fleet_gateway_audit_data, target: /app/data/audit}` alongside the six existing
bind mounts; `fleet-gateway.environment.AUDIT_DIR` resolves to
`/app/data/audit`; top-level `volumes.fleet_gateway_audit_data` resolves to
`{name: fleet_gateway_audit_data, external: true}` — confirms Compose uses the
bare key as the external volume's literal name when no `name:` override is
given (verified empirically, not assumed).

**3.4 Apply**:

```
$ docker compose -p openclaw-enterprise $CHAIN \
    -f /data/ocee/releases/gateway-audit-volume/compose.audit-volume.yml \
    up -d --no-deps fleet-gateway
 Container openclaw-fleet-gateway  Recreated
 Container openclaw-fleet-gateway  Started
```

**3.5 Verification (judgment criteria, not exit codes)**:

- Mounts: **7** now (was 6) — the 7th is `volume
  /var/lib/docker/volumes/fleet_gateway_audit_data/_data -> /app/data/audit`.
- `config_files` label: now includes `compose.audit-volume.yml` (count 1).
- `docker inspect`: `Status=running Health=healthy
  StartedAt=2026-09-07T16:04:28Z` — a genuinely new container instance, not the
  same one restarted.
- Real `/healthz` body (not Docker's healthcheck, which only proves the
  configured command exited 0):
  `{"ok":true,"checks":{"postgres":true,"redis":true,"litellm":true,"fruitV4":true}}`.
- **The chain**: post-recreation content is `2026-09-07.jsonl: lineCount=40
  first=2026-09-07T15:03:04.951Z last=2026-09-07T15:50:02.646Z` — identical to
  the pre-recreation baseline in §3.1. Zero entries lost across a real
  production `docker rm` + recreate.
- **Live writes continue**: one harmless unauthenticated `GET /api/audit`
  against the recreated container (expected, got, `401`) appended a real new
  `auth.unauthorized` line — `lineCount` went `40 → 41`,
  `last=2026-09-07T16:06:59.955Z` — proving the write path is live on the new
  mount, not just that old data survived.

Deploy lock released `16:07:14Z` (`RELEASED`, `status` confirms `FREE`
afterward). Window: **`15:59:50Z`–`16:07:14Z`, ~7.5 minutes.**

## 4. Verification that something in production checks this chain

Before this ticket, `verifyChain()` (`packages/fleet-gateway/src/audit/integrity.ts`)
had exactly one caller in the whole codebase: `integrity.test.ts`. No route, CLI,
or scheduled job in production ever read a JSONL file back, so a broken chain
could only be inferred, never reported.

`vecta` PR #1041 (https://github.com/ZenoWangzy/vecta/pull/1041) adds:

- `GET /api/audit/verify-chain?date=YYYY-MM-DD` — RBAC L4-only, defaults to
  yesterday, returns `{date, file, exists, lineCount, valid, brokenAtLine?}`.
- A daily scheduled check in `server.ts` (same `setInterval` idiom the file
  already uses for other periodic jobs) that verifies the most recently
  *completed* day and raises a `proactive_outbox` admin alert on failure,
  matching the existing `enqueueAdminAlertIfNeeded` row shape.

**This code is not yet live on the running production container.** Both new
code paths ship inside the `fleet-gateway` image; activating them needs a full
image rebuild + promotion via
`docs/runbooks/openclaw-enterprise-gateway-image-promotion.md` — a separate,
already-documented, high-frequency operation this ticket deliberately does not
trigger on its own (this ticket's own production change, §3, reused the
*already-running* image and only touched the mount). The next routine gateway
promotion carries this fix forward automatically.

## 5. Retention expectation and who actually consumes this chain

This is answerable, not a "nobody knows" — `vecta`'s
`docs/adr/ADR-035-audit-log-dual-write-sla.md` (Accepted, 2026-06-07) already
made this decision; it was just never applied to the JSONL file specifically.
Quoting its own framing of why the audit system exists at all: "VectA 卖给金融、
政府、央国企的核心壁垒之一是完整、可信、可回放的审计日志" (a complete, trustworthy,
replayable audit log is a core sales requirement for financial/government/SOE
customers) — and names the consumer directly: "合规侧：N 年后审计署回查" (compliance
side: regulators reviewing years later).

**ADR-035 §6 retention table** (PostgreSQL / ClickHouse, by event severity):

| severity | PostgreSQL | ClickHouse |
|---|---|---|
| `critical` | 7 years | permanent |
| `warn` | 2 years | 5 years |
| `info` | 6 months | 2 years |

**The gap this ticket surfaces**: ADR-035 §3 explicitly calls the JSONL file the
*authoritative* copy for compliance replay and forensic/legal use ("HMAC 链落点；
离线导出 / 司法取证 / 防 DB 篡改的第三方副本" / "合规审计回放时以 JSONL 链为权威") — but
never states a retention duration for the file itself, only for the two database
copies. A day's JSONL file mixes all three severities, so there is no single
number to point at.

**What's actually true in production today**, verified by code, not inferred:

- **Nothing prunes or archives the JSONL files, ever.** No code path in
  `packages/fleet-gateway/src/audit/` deletes, rotates, or archives a `.jsonl`
  file under `AUDIT_DIR`. Today's de facto retention is "indefinite, by
  omission" — which happens to be consistent with ADR-035's intent for
  `critical` events, but is an accident of nothing having been built to prune
  it, not a deliberate policy.
- **Consumption today is zero, mechanically** — before PR #1041, nothing read
  the file back at all; after it, the route and daily check *verify* the
  chain's integrity but don't extract business value from its contents. The
  *intended* consumer, per ADR-035, is an occasional, on-demand one (compliance
  audit / e-discovery), not a continuous automated reader — that shape (rarely
  read, must never silently lose data) is exactly why retention should default
  to "keep," not "prune."
- **A related, pre-existing gap found while checking this** (not fixed here —
  out of scope for an `AUDIT_DIR` mount ticket, flagged for its own ticket):
  the PostgreSQL side of ADR-035's retention table is not actually enforced
  either. `packages/fleet-gateway/src/audit/retention.ts` implements
  `ensureNextPartition()`/`pruneExpiredPartitions()` for the partitioned
  `audit_log` table, but **neither function is ever called anywhere in the
  codebase** (confirmed by `vecta`'s own `docs/superpowers/plans/2026-05-03-audit-system-hardening.md`,
  which already tracked this as open work: "Runtime code does not call either
  function"). Migration `0012_audit_partition.sql` only pre-created monthly
  partitions through `2026-08`; with no scheduler ever calling
  `ensureNextPartition()`, every `audit_log` row since **2026-09-01** has been
  landing in the `audit_log_default` catch-all partition instead of a proper
  monthly partition — silently, with no error. This means ADR-035's 6-month/
  2-year/7-year PostgreSQL retention windows are currently theoretical: nothing
  creates the partitions the pruning logic needs, and nothing calls the
  pruning logic either.

**Recommendation** (for the ticket owner to confirm — this is a retention
*policy* decision, not something to unilaterally set from an infra ticket):
state explicitly, next to `AUDIT_DIR` in this repo, that JSONL audit files are
retained **indefinitely / until a documented legal-hold or compliance review
process says otherwise** — matching ADR-035's `critical`-tier intent and the
file's role as the authoritative forensic copy — rather than leaving the
"nobody's pruning it yet" default undocumented. The Postgres partition gap
above is real but is a separate, already-partially-scoped follow-up (the
2026-05-03 plan doc already names the fix: wire `ensureNextPartition()` into a
scheduled job and confirm `0012_audit_partition.sql`'s partition set is
current) — not something this ticket's volume-mount fix should also absorb.

## 6. Rollback

Same mechanics as the healthcheck overlay: drop the one `-f
.../compose.audit-volume.yml` from the chain and `up -d --no-deps fleet-gateway`
again. `fleet_gateway_audit_data` is `external: true`, so it is never touched by
`docker compose down` (with or without `-v`) — rolling the mount back does not
delete the volume or its contents; the data stays available for the next
attempt.
