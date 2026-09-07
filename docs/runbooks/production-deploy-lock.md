# Production deploy lock on `mypc`

Ticket 140. `openclaw-fleet-gateway` was pushed to `a22e12f13` by one workflow
at `2026-09-07T03:13:57Z` while a second, unrelated window was mid-deploy on
the same container, and neither side knew the other existed until the second
window reached its own promotion step. It stopped instead of degrading — the
right call — but that was luck, not a mechanism. The conductor tried to reach
the other session directly, twice, and failed both times. **Coordination
between two deploy sessions cannot depend on one being able to message the
other.** This lock is the mechanism that replaces that assumption: it lives
on `mypc` (the one machine that is always in the loop, since it's the one
being deployed to), needs no service, no daemon, and no dependency beyond
`flock(1)` and `bash`, which are already on every host in this project.

Script: `scripts/deploy-lock.sh`. Contract test: `scripts/test_deploy_lock_contract.py`
(runs against a throwaway temp directory, no `mypc` or Docker access needed —
part of the normal `scripts/test_*.py` glob in `pr-contract-checks.yml`).

## Scope

One lock, not one per container. This is collision avoidance between people,
not a scheduler — "don't turn the same valve at the same time" doesn't need
a lock per valve when there are only ever one or two hands near the panel at
once. A single global lock also means a holder's declared `--containers`
list is purely informational (for the next operator to read), never itself
part of the safety property.

Covers, at minimum, any deploy/promotion/rollback touching:

- `openclaw-fleet-gateway`
- `openclaw-channel-gateway`
- `fruit-v4-isolated-uat` (and its `fruit-v4-isolated-setup` migration sibling)
- `openclaw-fruit-feishu-gateway` (added by ticket 132, see below)
- `openclaw-rag-service` (added by ticket 145, see below)
- `openclaw-wecom-contact-sync` (added by ticket 145, see below)
- `vecta-fruit-industry-pack-canary` (added by ticket 150, see below)

**`openclaw-fruit-feishu-gateway` is covered as of ticket 132.** It used to
be deliberately excluded here on the grounds that nothing recreated it, so
there was no operation to serialize against. Ticket 132 changed that: it
added `deploy/fruit-feishu-gateway/docker-compose.yml` and
`docs/runbooks/fruit-feishu-gateway-recovery.md`, a real recreate path. Any
`docker compose -p fruit-feishu-gateway ...` against it, or any
`recreate_container()` run in `scripts/ops/fruit-account-onboard.sh` (vecta
repo) that touches it, takes this lock first.

**`openclaw-rag-service` is covered as of ticket 145, for the same reason.**
It had no recreate path of any kind (not even a fragile self-bootstrapping
one) before this ticket added `deploy/rag-service/docker-compose.yml` and
`docs/runbooks/rag-service-recovery.md`. Any
`docker compose -p rag-service ...` against it takes this lock first.

**`openclaw-wecom-contact-sync` is covered as of ticket 145, for the same
reason.** Same as `openclaw-rag-service`: no recreate path of any kind
before this ticket added `deploy/wecom-contact-sync/docker-compose.yml` and
`docs/runbooks/wecom-contact-sync-recovery.md`. Any
`docker compose -p wecom-contact-sync ...` against it takes this lock
first.

**`vecta-fruit-industry-pack-canary` is covered as of ticket 150, for the
same reason, and despite its name is the highest-real-traffic container in
this group (~330 req/h of live MCP-over-SSE sessions).** No recreate path of
any kind existed before this ticket added
`deploy/fruit-industry-pack/docker-compose.yml` and
`docs/runbooks/fruit-industry-pack-canary-recovery.md` -- read that runbook's
"The name is the trap" section before treating this container as disposable
just because its name contains "canary". Any `docker compose -p
fruit-industry-pack ...` against it, or any `recreate_container()` run in
`scripts/ops/fruit-account-onboard.sh` (vecta repo) that touches it, takes
this lock first.

**Deliberately excludes Hermes per-instance config refresh
(`POST /api/instances/:id/refresh-config`).** See "Judgment call: should
refresh-config take this lock?" below — short answer: no, but the specific
collision that motivates the question is already handled elsewhere.

## The mechanism: a live process is the lock, not a timestamp

`deploy-lock.sh acquire` opens `$DEPLOY_LOCK_DIR/production-deploy.lock` on
fd 200, takes a non-blocking `flock`, and — only once it actually holds the
lock — writes who/what/when into a sibling `.meta` file, then `exec`s into
`sleep "$ttl"` **without closing fd 200**. From that point on, the `sleep`
process *is* the lock: `flock(2)` is released by the kernel the instant that
process exits, for any reason at all — explicit `release`, the ttl elapsing,
the holding SSH session dropping, or `mypc` rebooting. Nothing about this
depends on wall-clock comparison.

This is the answer to the ticket's hardest constraint: telling a stale lock
from a live one apart **without relying on timestamps**, because "an 18
minute build" and "a session that died 18 minutes ago" produce an identical
timestamp. They do not produce an identical process. `acquire`'s non-blocking
`flock` asks the kernel directly, "is a process still holding this
descriptor open" — that question has one correct, immediate answer,
regardless of how long the previous holder had been running when it died.
Demonstrated both directions below.

`status` performs the same non-blocking probe (and immediately releases it
again if it succeeds — it's a read, not a claim) so anyone can check without
disturbing a real holder.

### Why `exec sleep`, not a heartbeat file

A heartbeat requires the checker to decide "how stale is too stale," which
is exactly the timestamp judgment call this ticket rules out (an 18-minute
build's heartbeat can go quiet for a while just from I/O contention). A held
`flock` requires no such judgment: it is binary, kernel-enforced, and answers
"is the holder's process still alive" directly instead of "when did it last
tell me it was alive."

### The ttl is a backstop for rule 3 (forgotten release), not the staleness rule

Default `--ttl-seconds 7200` (2h — generous next to any single runbook
window; the gateway promotion runbook's own worked example finished well
under an hour end to end). If a holder finishes and forgets to call
`release`, the `sleep` exits on its own once the ttl elapses and the lock
frees itself — no one has to notice, judge, or intervene. This is separate
from, and does not weaken, the crash-detection property above: a crash frees
the lock within moments regardless of how much ttl was left (demonstrated
below with a crash at t+11s against a 30s ttl, and again at t+23s against a
60s ttl — both reclaimed immediately, nowhere near expiry).

## Usage

Always stream the script from the exact checkout you're deploying from — it
is never installed as a standing copy on `mypc`, so there is nothing there
that can drift out of sync with this file:

```bash
# Step 0 — before touching anything.
ssh -o ServerAliveInterval=15 -o ServerAliveCountMax=3 mypc \
  'bash -s -- acquire --holder "<your name/session>" \
     --session "<session URL or id — how to reach you>" \
     --containers "openclaw-fleet-gateway,openclaw-channel-gateway"' \
  < scripts/deploy-lock.sh
# ACQUIRED ... → proceed.
# DENIED ...   → read the printed holder/session/containers/acquired_at,
#                go find that session, do not proceed.
```

`-o ServerAliveInterval=15 -o ServerAliveCountMax=3` are client-side options
(no `mypc`-side config change): they make your own `ssh` notice a dead
connection within ~45s instead of waiting on a bare TCP timeout, so a
network partition — not just a clean process exit — frees the lock promptly
too.

```bash
# Anywhere mid-window — check without disturbing a real holder.
ssh mypc 'bash -s -- status' < scripts/deploy-lock.sh

# Last step — after the runbook's own verification passes.
ssh mypc 'bash -s -- release' < scripts/deploy-lock.sh
```

`release` reads the pid out of the `.meta` file and kills it; it does not
require the original SSH connection that acquired the lock to still be the
one calling it, which matters if your own tooling lost track of that
backgrounded connection.

## Evidence (ticket 140 acceptance criteria)

All four run against `DEPLOY_LOCK_DIR=/data/ocee/locks/_demo-ticket-140` on
`mypc` — a throwaway path, cleaned up after, never the real
`/data/ocee/locks/production-deploy.lock`. **The currently-running fruit v4.1
production window was never locked by this exercise** — that lock's first
real use starts with the next deploy round that adopts this runbook edit.

**1. Two concurrent holders — B is denied and sees who A is:**

```
$ ssh mypc '... acquire --holder "agent-A (fruit-v41 window, demo)" \
    --session "https://claude.ai/code/session_DEMO_AAA" \
    --containers "openclaw-fleet-gateway,openclaw-channel-gateway" \
    --ttl-seconds 60' < scripts/deploy-lock.sh
ACQUIRED holder=agent-A (fruit-v41 window, demo) pid=1607181 acquired_at=2026-09-07T04:19:53Z ttl_seconds=60

$ ssh mypc '... acquire --holder "agent-B (peer, demo)" \
    --session "https://claude.ai/code/session_DEMO_BBB" \
    --containers "openclaw-fleet-gateway"' < scripts/deploy-lock.sh
DENIED: production deploy lock is already held.
--- current holder (docs/runbooks/production-deploy-lock.md explains how to judge staleness) ---
holder=agent-A (fruit-v41 window, demo)
session=https://claude.ai/code/session_DEMO_AAA
containers=openclaw-fleet-gateway,openclaw-channel-gateway
acquired_at=2026-09-07T04:19:53Z
ttl_seconds=60
pid=1607181
host=mypc
B exit=1
```

**2a. Stale — holder killed at t+23s against a 60s ttl (not waited out, not
even close to expiry) — second acquirer succeeds immediately:**

```
$ ssh mypc 'ps -o pid,etimes,cmd -p 1607181'
    PID ELAPSED CMD
1607181      23 sleep 60
$ ssh mypc 'kill -9 1607181; sleep 0.3; ps -o pid,cmd -p 1607181 2>&1 || echo "confirmed: pid 1607181 gone on mypc"'
confirmed: pid 1607181 gone on mypc
$ ssh mypc '... acquire --holder "agent-B (peer, demo)" --session "..." \
    --containers "openclaw-fleet-gateway"' < scripts/deploy-lock.sh
ACQUIRED holder=agent-B (peer, demo) pid=1619388 acquired_at=2026-09-07T04:20:20Z ttl_seconds=7200
```

**2b. Alive — reverse direction, so the mechanism isn't just "anything old
looks stale": a holder well inside its ttl is correctly refused, not
reclaimed:**

```
$ ssh mypc '... acquire --holder "agent-D (alive, demo)" --session "..." \
    --containers "fruit-v4-isolated-uat" --ttl-seconds 20' < scripts/deploy-lock.sh
ACQUIRED holder=agent-D (alive, demo) pid=1626978 acquired_at=2026-09-07T04:20:38Z ttl_seconds=20
$ ssh mypc '... acquire --holder "agent-E (peer, demo)" --session "..." \
    --containers "fruit-v4-isolated-uat"' < scripts/deploy-lock.sh
DENIED: production deploy lock is already held.
holder=agent-D (alive, demo)
...
E exit=1  (must be 1/DENIED — D is genuinely still alive, not stale)
```

**3. Forgotten release doesn't block forever — same holder (agent-D above),
nobody ever calls `release`, ttl (20s) elapses on its own:**

```
$ sleep 20   # nobody releases
$ ssh mypc '... status' < scripts/deploy-lock.sh
FREE (kernel confirms no live holder; a prior holder's metadata, if any, is stale)
holder=agent-D (alive, demo)
...
```

Full transcript, plus the local (non-`mypc`) equivalent for all five cases
including explicit `release`, is exercised unattended by
`scripts/test_deploy_lock_contract.py` on every PR.

## How someone could still bypass this, and why that residual risk is accepted

Anyone with `ssh mypc` access and Docker access can run `docker compose`
directly without ever calling `acquire`. This lock is advisory, like every
`flock`-based lock, and like the migration profile's own approval gate in
`fruit-v4-isolated-production-compose.md` ("Compose cannot prevent an
operator who already has Docker access ... from invoking the image or setup
script outside this procedure"). Closing that gap would mean wrapping every
Docker/Compose invocation on the host in an enforcing proxy — a new service,
running with enough privilege to gate all container operations, which is
exactly the "distributed consensus" weight this ticket says not to build for
a two-operator collision problem. The cost of bypass is also the same as the
incident that opened this ticket: doing it without checking `status` first
is indistinguishable from the accident already on record, so the fix for
"someone skips step 0" is the same as for any skipped runbook step —
make the runbook impossible to follow partially in practice (step 0 and the
release step below are now literally that: the runbook's first and last
commands), not add a second enforcement layer to catch people who skip
reading it.

## Judgment call: should refresh-config take this lock?

**No — but an earlier version of this section had the wrong reason for it.**
It originally argued the one real collision was already covered by
`openclaw-enterprise-gateway-image-promotion.md` §1c's "no admin anywhere may
approve a skill" rule. That rule guards a different thing:
`refreshSkillAvailabilityForAllInstances` (reached from `routes/skills.ts`,
`routes/approvals.ts`, `routes/platform-curated-skills.ts`) — a fire-and-forget
loop with no per-tenant filter and no lockout, triggered by *skill*
administration actions, not by deploys. Conflating that with the deploy/refresh
collision below shipped a citation that doesn't hold up. Corrected here
(ticket 140 follow-up), after the coordinator caught it.

**The actual collision** (measured directly in ticket 139, not inferred): a
single employee's `POST /api/instances/:id/refresh-config` copies the new
runtime into that employee's container via `prepareHermesRuntimeInContainer`
(`hermes/runtime-switcher.ts:60-70`) — **not** `resetDir()`, which only ever
touches fleet-gateway's own local staging directory. The employee-container
sequence is four independent `docker exec`/`putArchive` calls, not a
transaction: (1) `rm -rf` the currently-managed plugin directories, (2)
`putArchive` the freshly generated hermes tree in, (3) rebuild
`runtime-skills`, (4) write the `active-runtime` marker. Between (1) and (2),
that employee has "old plugin gone, new one not in yet." If `fleet-gateway`
gets recreated (`up -d`) anywhere in that window, the copy never finishes and
the employee is stuck there. Ticket 139 measured this window directly on a
real refresh: **0.83 seconds** (`16:37:28.126` gone → `16:37:28.953` back).
This can happen on *any* fleet-gateway recreate, not only a pack-version-flip
ceremony — §1c's "empty diff → normal path" framing doesn't exempt it.

**Still shouldn't take this lock — granularity mismatch.** This lock
serializes a rare (a few times a week), high-stakes, always-supervised
operation against itself. `refresh-config` fires per employee instance,
including from an automated self-heal path (`getOrCreateInstance`) with no
human in the loop. Making every one of those acquire-and-release this lock
turns a cheap mechanism into contention (and a forgotten-release surface) on
a hot path, to protect a sub-second window.

**The accepted middle ground: refresh checks, never holds.** Before each
employee's refresh, ticket 139's procedure runs the same non-blocking,
read-only probe `status` already exposes — it doesn't acquire anything, so it
never blocks a deploy, never needs releasing, and can't be forgotten held:

```bash
ssh mypc 'bash -s -- status' < scripts/deploy-lock.sh   # exit 0 = safe to start this refresh, non-zero = don't
```

Non-zero prints the current holder/containers/`acquired_at`; the refresh
procedure stops and does not retry or guess whether that holder is stale (if
that judgment is needed, it's the same one this doc already describes for a
denied `acquire`).

**The residual gap, stated instead of ignored: this only protects one
direction.** It stops "a deploy is in progress, don't start a refresh." It
does **not** stop "a refresh is already in flight, a deploy starts anyway" —
a deploy's own `acquire` has no way to see a refresh that took no lock and
left no record. Closing that direction symmetrically would mean refresh
*does* take (at least a shared/read-mode) lock for its ~1-second copy window,
which reintroduces the exact hot-path/automatic-trigger cost the granularity
argument above rules out — `getOrCreateInstance`'s self-heal path would then
be doing a lock dance on every cold start. The trade accepted here: the
window is sub-second and, per ticket 139's own procedure, refreshes are done
one at a time, in a deliberate sequence, with an explicit pre-check for "no
live session on this employee" — not a background flood. If `refresh-config`
ever becomes a frequent, uncontrolled trigger (rather than the sequenced
manual ceremony ticket 139 describes), this trade should be revisited before
trusting it further.
