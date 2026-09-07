# Promoting `openclaw-fleet-gateway` / `openclaw-channel-gateway` to a new VectA SHA

This is the highest-frequency, highest-consequence operation this project does on
`mypc`, and until this file it had **zero runbook coverage** — every window did it
ad hoc and recorded what happened afterward as a one-off
`vecta/docs/ops/<date>-*.md`. This file exists to turn that into a checkable
contract. It is being written *while executing it* (2026-09-06, production window
4/continued) — every command and output below is what actually ran, not an
idealized flow. Where a step hasn't been executed yet in this window, it says so
explicitly instead of describing an imagined result.

Scope: the two services in the `openclaw-enterprise` Compose project that carry
live VectA application code — `fleet-gateway` and `channel-gateway` — via the
accumulating `compose.images.yml` override chain under `/data/ocee/releases/`.
It does **not** cover the isolated `fruit-v4-isolated-uat` sidecar (see
`fruit-v4-isolated-production-compose.md`), and it does not cover
`rag-service`/`directory-service`/`a2a-router`/`baidu-search-service`/
`admin-console` (contract-excluded, per the 2026-09-06 `compose.images.yml`
comment: "契约内服务；rag/directory/baidu 仍在契约外，另开票").

## 0. Take the production deploy lock first

Ticket 140: two workflows promoted this exact container at the same time on
2026-09-07 with neither side aware of the other, because coordination had no
mechanism other than "hope nobody else is deploying." Before step 1, acquire
`scripts/deploy-lock.sh` from `vecta-infra` (full contract, staleness rule,
and why it's a single lock rather than one per container:
`docs/runbooks/production-deploy-lock.md`):

```bash
ssh -o ServerAliveInterval=15 -o ServerAliveCountMax=3 mypc \
  'bash -s -- acquire --holder "<your name/session>" \
     --session "<session URL or id>" \
     --containers "openclaw-fleet-gateway,openclaw-channel-gateway"' \
  < scripts/deploy-lock.sh
```

`ACQUIRED` → continue to step 1. `DENIED` → it prints who currently holds
it, what they're touching, and when they took it — go find that session
before doing anything else in this file. Do not proceed on the assumption
that a denial is stale; `production-deploy-lock.md` explains the (non-
timestamp) way to actually tell.

## 1. Which SHA is safe to build

The naive rule — "build whatever `origin/main` HEAD is" — breaks the first time a
`[skip ci]` documentation-only commit lands on `main`: that commit has no
postsubmit run at all, so "HEAD has a green postsubmit" is never true again once
one exists. Hit this exact trap in this window: `origin/main` moved from
`14d544a794c8...` to `def7bf13de84...` without a new CI run, because the two
commits in between were `docs/agents/development-flow.md` and a new
`docs/superpowers/plans/*ablation-and-integration-pilot.md`, both `[skip ci]`.
Verified with `git diff --stat 14d544a794c85d30670c2ec33f92c5e105415c11 origin/main`
— exactly those two files, zero code paths.

The actual rule:

1. Find the most recent commit **G** on `main` where the **`Postsubmit validate`
   job** (not the run's overall conclusion — see the false-green-guards note
   below) is `success`. Use `gh run list --branch main --event push --json
   databaseId,headSha,conclusion,createdAt` plus `gh run view <id> --json jobs`
   to check the job, not just the run.
2. `git diff --name-only G origin/main` and confirm **zero** paths under:
   `packages/`, `apps/`, `scripts/`, `.github/`, `*.lock`, `package.json`, and
   the repo-root files that Dockerfiles actually `COPY` — **do not hand-maintain
   this list**, derive it live each time:
   ```bash
   grep -h '^COPY' packages/fleet-gateway/Dockerfile packages/channel-gateway/Dockerfile \
     | grep -v '\-\-from' | head -1
   # COPY pnpm-workspace.yaml package.json pnpm-lock.yaml tsconfig.base.json .npmrc ./
   ```
   As of this window that adds `pnpm-workspace.yaml`, `tsconfig.base.json`,
   `.npmrc` to the check — none of which are covered by a naive
   `packages/apps/scripts/.github/*.lock/package.json` list. (`Dockerfile*` as a
   standalone top-level entry is redundant for these two images since both
   Dockerfiles live under `packages/` already.) This list can go stale again the
   next time either Dockerfile adds a root-level `COPY` source — re-derive it,
   don't trust this paragraph.
3. Both conditions true → the full 40-char `origin/main` SHA is safe to build,
   **even if** the run's overall conclusion is `failure` for reasons outside the
   diff you just checked.

**Note on `build-mypc-images.yml`'s own postsubmit-evidence gate**
(`scripts/verify-vecta-postsubmit.py`): as of `vecta-infra#73`, this is now
ancestor-aware server-side — it walks tip's ancestry for the newest commit
with real postsubmit evidence and accepts the exact tip SHA only if the diff
from that ancestor to tip touches none of the same production-image paths
(re-derived from `production-image-contract.json` + the Dockerfiles each
build, same principle as step 2 above, not hand-maintained). This closes the
literal `[skip ci]`-tip deadlock this window hit before that PR merged
(`source_sha` must still equal the exact current tip — that requirement is
unchanged — but the tip no longer needs its *own* postsubmit run if a
content-identical ancestor has one). This does not replace the checks in this
section: the workflow's gate only answers "is it safe to build the image
contents," not "does the pack-identity or migration-gap situation for *this*
deploy require the four-step ceremony" (§1e) — keep doing all of it.

**Why job-level, not run-level.** `NUL bytes & lone negative assertions`
(ticket 122's false-green-guards job) is a *test-quality* gate: it fails when a
test in the diff is written so loosely it would pass whether the underlying
behavior is right or silently broken. That is a real defect to fix, but it is
not evidence the code doesn't run — `Postsubmit validate` (build, typecheck,
the actual integration gates, lint) is the job that proves that. Conflating the
two either blocks shipping on a test-hygiene nit, or — the direction this
window actually hit — risks shipping a commit whose *known-not-robust test* is
the only evidence anything was checked. Concretely this window: `main` at
`14d544a794c8...` had `Postsubmit validate: success` and
`NUL bytes & lone negative assertions: failure` (ticket 74's
`v4-stock-check.test.ts:2298` has a lone `not.toContain` with no positive pin
on `summary.totals` — a real gap flagged by ticket 122's own new guard, not a
flake). Conductor's call: still don't build on a run where the guard is red,
precisely *because* shipping would launder "guard caught a weak test" into
"green, ship it" — wait for the guard's own fix to land and the **whole run**
green, then apply the two-step check above (which is what actually matters for
image content).

**A red job on `main` can go green again without the thing that caused it being
fixed — don't read "the run turned green" as "the defect is gone."**
`scripts/check-lone-negative-assertions.mjs` (the guard's implementation) runs
`--new-lines-only --base=${{ github.event.before }}` — it only scans the lines
a given *push* added or changed. Ticket 74's lone assertion
(`v4-stock-check.test.ts:2298`) only shows up as red on the one push that
introduced it (`14d544a794c8...`). The very next push, as long as it doesn't
touch those lines again, gets a clean scan — not because the assertion was
fixed (ticket 129 is doing that separately), but because the scan window slid
past it. The bad assertion is still sitting in the tree either way. **Never
write "waited for the run to go green so it's safe to build" in an evidence
record** — that sentence is true about the run and false about causation, and
this doc exists so the next person doesn't have to rediscover that. State
instead, precisely, which job on which commit was checked and what the diff
since then contained.

**Outcome this window**: `main` moved repeatedly while working through this
(each move re-checked against the two-step rule above — several were clean
doc-only advances, one genuinely carried new code and had to wait for its own
`Postsubmit validate: success`). Final SHA actually built and shipped:
`b1689bd4908b31bb4120e98a34eebb96e6044925`, `Postsubmit validate: success`
confirmed directly on that commit, pack-identity gate (§1c) re-run fresh
against it, PR #999 re-confirmed still draft. Full chronology in this
window's production report.

## 1a. Building a subset of images is a judgment call, not a default

`image_names` empty (build everything) is the safe default. Only build a
subset if you can positively show the other contract images' *source* didn't
change in the diff you just validated in §1 — and check shared/depended-on
packages, not just the service directory a ticket names. Concretely this
window: ticket 126's diff touches `packages/channel-gateway/` directly, but
also `packages/agent-runtime/src/interfaces/agent-provider.ts` — a shared
interface package other services import. A diff that only names one service
directory can still be depended on by others; don't infer "only X needs
rebuilding" from which directory a ticket's description mentions.

## 1b. Container inventory: there are two `channel-gateway` processes, only one is this contract's

Production runs `openclaw-channel-gateway` (this contract, `openclaw-enterprise`
compose project) **and** `openclaw-fruit-feishu-gateway` (a second
`channel-gateway`-image container, no compose project, no systemd unit, no
crontab — nothing recreates it if the host reboots; separately ticketed,
2026-09-06 investigation). A past incident (F68) undercounted containers by
checking only three when a fourth relevant one existed — don't repeat that
shape here: when this runbook's steps say "the gateway is promoted," that
means `openclaw-channel-gateway` only. `openclaw-fruit-feishu-gateway`
deliberately stays on whatever digest it already has; say so explicitly in any
report, don't let silence read as "all channel-gateway instances moved."

## 1c. Pack-identity gate — run this before every build, not just once

`packages/fruit-industry-pack`'s Scenario Pack manifest content pin
(`runtime-manifest-v4-workbench.json`) and its `SKILL.md` are load-bearing for
`assertCompatibleSkill`. If a deployed image's manifest content disagrees with
what the DB's `installed_version_id` points at, `config-generator.ts:452-456`
catches the mismatch and only pushes a warning; `role-change-service.ts` logs
and still returns success. **The refresh reports success while silently
degrading every V4 tenant's employee containers to zero packs** (both V4 and
V3 gone) — see `docs/agents/handoffs/2026-09-01-hash-ban-wave2-halted.md` §1.
This is a silent, fleet-wide regression, so it gets its own gate, ahead of the
build:

```bash
git diff --name-only <currently-running production SHA> <SHA you are about to build> \
  -- '*runtime-manifest-v4-workbench.json' '*SKILL.md'
```

- **Empty → normal path.** Build and deploy as this runbook otherwise
  describes; no pack version publish, no `installed_version_id` change, no
  per-runtime refresh needed.
- **Non-empty → stop.** Do not build normally. Follow
  `docs/agents/handoffs/2026-09-01-hash-ban-wave2-halted.md` §2.3's four-step
  ceremony, in this order — deploying before the version flip is deliberate,
  not a typo, because a failed deploy then costs nothing in the DB, while the
  reverse order leaves a DB-new/gateway-old mismatch that needs manual SQL to
  undo:
  1. Publish the new pack version with `pnpm platform:sync-skill`
     (`packages/fruit-industry-pack/src/platform/sync-platform-skill.ts`) —
     it reads `SKILL.md` and the manifest itself and derives the payload;
     **never hand-write the publish payload**.
  2. Deploy the new `fleet-gateway` image (`runtime-manifest-v4-workbench.json`
     is COPYed into `packages/fleet-gateway/Dockerfile` specifically — that
     handoff's incident only needed a fleet-gateway rebuild, not
     channel-gateway or fruit-industry-pack, though this window rebuilds all
     three together anyway per §1a).
  3. Flip `installed_version_id` with raw SQL: one transaction, print
     before/after, optimistic-lock on the old value, and cover **both** V4
     tenants' install rows in the same statement/transaction.
  4. Refresh every affected runtime individually
     (`POST /api/instances/:id/refresh-config`), least-critical instance
     first, the founder's own last, with a V23 assertion after each one.
  During this whole window, **no admin anywhere may approve a skill** —
  `refreshSkillAvailabilityForAllInstances` reaches every runtime of every
  tenant with no lockout, and would race the manual refresh sequence above.

**Re-run this check every time `main` moves, using the SHA you are actually
about to build against whatever SHA is actually running in production right
now** — not a cached "I checked this an hour ago" conclusion, and not someone
else's `origin/main` snapshot from when they told you the result was clean.
Executed this window:

```
$ git diff --name-only b375e34309b2f4b73effbf71c2e8395c04456b67 5bd4721c47de0bc85fb6929ef1806972487bb193 \
    -- '*runtime-manifest-v4-workbench.json' '*SKILL.md'
(empty)
```
Production SHA at check time: `b375e34309b2f4b73effbf71c2e8395c04456b67`.
Build candidate: `5bd4721c47de0bc85fb6929ef1806972487bb193`. Empty diff →
normal path confirmed for this window.

**Also check for the specific known landmine before picking a SHA**: PR #999
(ticket 111) touches `SKILL.md` and was deliberately converted to draft for
exactly this reason. Confirm it is not merged (`gh pr view 999 --json
state,mergedAt`) before building — if it ever shows merged unexpectedly, stop
and report, don't build through it.

(The build target moved several more times after this check for reasons
unrelated to the pack — see the SHA-selection saga in this window's
production report. Re-ran this exact check against the SHA actually shipped,
`b1689bd4908b31bb4120e98a34eebb96e6044925`: still empty. Re-run it fresh every
time you're about to actually build, not just once early in the window.)

**On the project's hash ban and `FRUIT_V4_IMAGE_DIGEST` specifically**:
`vecta/CLAUDE.md`'s hash ban forbids computing/comparing a digest *as
delivery evidence* (the pack-payload self-verification step this same window
correctly dropped for exactly that reason). It does not forbid reading a
digest Docker or the registry already produced, into a field this contract
structurally requires to route to the right bytes — the same category as
using `SOURCE_SHA` everywhere else in this document. The test isn't whether
the word "digest" appears; it's what question the value answers: "which
build is this" (identity, fine) vs. "did I deliver correctly" (verification
evidence, banned). Founder-confirmed this window.

## 1d. "Checkout infra contract" failed but it wasn't the network — the runner's own message loop can get stuck

Harder to rediscover than anything else in this file, hit this window: three
consecutive dispatch attempts on `mypc-vecta-infra-prod-build-2` all failed,
but only the first was actually a network problem. The second and third
failed with a completely different symptom —
**`The job was not acquired by Runner of type self-hosted even after multiple
attempts`** — an Actions-level annotation on the run, not a step failure, so
`gh run view <id> --log-failed` shows nothing at all for it.

**How to tell this apart from a real network problem, in order:**

1. `gh run view <id>` (no `--log-failed`) — if the annotation says "not
   acquired by Runner," this is not a step failing inside the job; the job
   never started. Different cause, different fix.
2. **`git ls-remote https://github.com/...` only tests `github.com`. Job
   acquisition does not go through `github.com` at all** — it goes through
   `broker.actions.githubusercontent.com` (visible in the runner's own diag
   log: `ServerUrlV2: https://broker.actions.githubusercontent.com/`), and
   the runner's self-updater pulls from yet a third host
   (`objects.githubusercontent.com`/`github.com/actions/runner/releases`).
   Spent three dispatch cycles this window wrongly concluding "not a network
   problem" because `git ls-remote` to `github.com` succeeded — that host has
   nothing to do with the symptom being diagnosed. **Test every host the
   failure could plausibly involve, separately, never merged into one
   "GitHub is/isn't reachable" verdict**:
   ```bash
   for host in github.com api.github.com broker.actions.githubusercontent.com \
     objects.githubusercontent.com codeload.github.com \
     pipelines.actions.githubusercontent.com; do
     echo "=== $host ==="
     curl -sS -o /dev/null -m 15 -w "http_code=%{http_code} time=%{time_total}s\n" "https://$host/"
   done
   ```
   run as `sudo -u github-runner`, 3 samples per host. A 404 on a bare `/` is
   fine and expected for the API-shaped hosts — the signal is "responded
   quickly and consistently," not the status code. This window's actual
   result at one point in time: `github.com` 3/3 timed out, every other host
   (including `broker`/`pipelines`, the two that matter for job acquisition)
   3/3 fast and fine — proving the "not acquired" runs and the git-fetch
   failures were two different, independently-timed problems, not the same
   outage. **Test the specific host the failing operation actually talks to,
   not whichever host is easiest to test with `git`.**
3. `gh api repos/ZenoWangzy/vecta-infra/actions/runners --jq '.runners[] |
   "\(.name) status=\(.status) busy=\(.busy)"'` — both runners can show
   `online`/`busy=false` and still neither picks up the job. That itself is
   the symptom, not proof everything is fine.
4. Read the stuck runner's own diagnostic log (find it fresh each time —
   filename has a session timestamp, don't hardcode it):
   `LOG=$(sudo -u github-runner bash -c 'ls -t
   /home/github-runner/actions-runner-vecta-infra-2/_diag/Runner_*.log | head
   -1')`. **The tell is `tail -n 3 "$LOG"` showing `Skip message deletion for
   job request message '<id>'` repeating continuously** instead of a quiet
   `Listening for Jobs` state — hundreds of lines, still growing between
   checks seconds apart. `systemctl is-active` says `active` throughout this;
   it is not a useful signal here, the service never crashed, its internal
   message loop just isn't getting to new work.
5. Root cause found this window: the runner's own self-updater had tried and
   failed to download a new runner version from
   `github.com/actions/runner/releases/download/...` (`WARN SelfUpdater...
   TaskCanceledException: The request was canceled due to the configured
   HttpClient.Timeout of 100 seconds elapsing`), then threw
   `BrokerServer`/`SocketException (125): Operation canceled` — the same
   underlying GitHub-reachability issue as the network failure, just hitting
   the runner's own control-plane connection instead of a job's git fetch,
   and leaving it stuck afterward rather than cleanly failing.

**Fix: restart just that one runner's systemd service.** This is authorized
as a routine action, not a stop-and-report one — the blast radius is queued
CI jobs, nothing that touches production traffic, production data, or
another operator's in-flight work. The general rule this window settled on:
*who gets affected* decides whether to just do it and report, or stop and
ask first. A stuck CI runner only affects the job queue; restarting it is
routine. Anything that would touch live production traffic, production data,
or something another person is actively using is the kind of thing to stop
and ask about first.

Before restarting, confirm there is nothing actually running on it (don't
infer this from "my dispatches didn't get acquired," check):
```bash
pgrep -af "Runner.Worker"   # empty = nothing executing right now
grep -n "Running job\|Job .* completed" "$LOG" | tail -5   # confirm no in-flight job younger than your last-seen failure
```
Then:
```bash
sudo systemctl restart actions.runner.ZenoWangzy-vecta-infra.mypc-vecta-infra-prod-build-2.service
```

**Judgment criterion — not `systemctl is-active`, which says `active` both
before and after, stuck or not:** find the *new* diag log (restart opens a
fresh session file) and confirm it contains a `Listening for Jobs` line and
its tail is no longer spamming `Skip message deletion`. Recovered cleanly
this window in under a minute: new log opened, `Session created`, `Listening
for Jobs`, no further skip-spam. **Watch whether the self-update recurs and
whether it succeeds** — if the restart just triggers the same doomed
download and gets stuck again, this is a recurring blocking point, not a
one-off, and that's a stop-and-report/new-ticket situation, not something to
route around by restarting in a loop.

## 1e. `SKILL.md` changing does not automatically mean the four-step ceremony

§1c's gate (`git diff ... -- '*runtime-manifest-v4-workbench.json' '*SKILL.md'` non-empty) tells you the pack-identity *risk exists* — it does not by itself tell you the ceremony is *required*. Before ticket 134, it did: `assertCompatibleSkill` re-hashed the rendered Skill content and compared it to a pinned digest, so any wording change at all tripped it. **After ticket 134 removed that recheck, the actual judgment criterion is narrower**: read
`packages/fleet-gateway/src/industry-packs/runtime-manifest.ts`'s `assertCompatibleSkill` at the SHA you're about to build, and check only the fields it still compares — currently `values.packId !== manifest.packId || values.modelVersion !== expectedVersion`, nothing else. Then:

1. Confirm those exact fields (`packId`, `modelVersion`) are unchanged in `runtime-manifest-v4-workbench.json` between the currently-running production SHA and the target (`git diff`, not eyeballing — a real diff, empty or not).
2. Confirm `modelVersion`'s value isn't itself derived from the content that changed — check `render-skill.ts`: it's sourced from `loadFruitModel()` (the domain model YAML), not from `SKILL.md` prose, so a Skill wording/tool-list edit alone can't move it.
3. Read what's actually installed in production (`skills.config->'industryPack'`, read-only SQL) and confirm it already matches `packId`/`modelVersion`.

All three clean → `assertCompatibleSkill` will pass unchanged before and after the deploy, even though `SKILL.md` itself changed. No new pack version, no `installed_version_id` flip, no per-runtime-refresh ceremony — normal deploy path. This is exactly what ticket 134 bought: a Skill-content edit (adding tools, rewording) no longer requires coordinating a DB pointer flip across every V4 tenant. **Re-run all three checks against the actual target SHA every time it changes** (this window it changed four times chasing a moving `main`) — don't assume a conclusion reached against an earlier candidate SHA still holds; re-verify, it's cheap.

If any of the three isn't clean — `modelVersion` did change, or `assertCompatibleSkill` now compares something else, or the installed metadata doesn't already match — that's when `docs/agents/handoffs/2026-09-01-hash-ban-wave2-halted.md` §2.3's four-step ceremony applies, and as of this window there is **no existing tool to execute step 4 (publish) for `fruit-v4-workbench` specifically** — `pnpm platform:sync-skill` (`sync-platform-skill.ts`) is hardcoded to the separate, older `fruit-industry-pack`/`fruit-data-query` pack (hardcoded `BASE_SKILL_SLUG`, throws if the Skill content's frontmatter name isn't `fruit-data-query` — `fruit-v4-workbench`'s own frontmatter declares `fruit-v4-workbench`, so pointing the tool at V4 files via its env overrides fails immediately on that check). Extending it, or writing a V4-specific equivalent, is its own small reviewable change — not something to improvise mid-deployment-window by hand-writing the publish payload.

## 1f. When a target container is already ahead: confirm superset, don't downgrade

Hit this window: dispatched a build for a container already running a newer
revision than the agreed target (another agent/session had deployed to it
concurrently, independently). The instinct is to "fix" it back to the agreed
SHA — **don't.** Moving a running container backward reverts whatever the
other change was; that's a strictly worse failure mode than temporarily
carrying one extra, unplanned commit, because it destroys someone else's
already-shipped work instead of just being slightly imprecise about scope.

The check, in order:
1. `git merge-base --is-ancestor <your target> <what's actually running>` —
   if true, what's running is a **superset** of your target (contains
   everything you need, plus more). If this is false (genuine divergence, not
   just "further ahead"), that's a real conflict — stop and report, don't
   guess which side wins.
2. If it's a clean superset, **retarget your own work to what's actually
   running**, not the other way around — re-run every gate (pack-identity,
   `assertCompatibleSkill`, migration gap) against the new, larger target, the
   same as if it had been the plan from the start.
3. Say so explicitly in the deployment record: which container carries the
   extra commit, what that commit is, and why it's there. Three containers
   ending up on **different revision labels that are provably ancestor/
   descendant of each other with a known, harmless diff** (this window:
   `fleet-gateway` one `[skip ci]` docs-only commit behind the other two) is
   not an inconsistency — it looks like one to the next person unless the
   report says so plainly. Confirm the ancestor relationship and the diff
   content yourself (`git log --oneline <old>..<new>`, `git show --stat` on
   the delta commits) before asserting it's harmless; don't take another
   session's description of what changed as the check.

## 2. Prerequisite: the image must actually exist in Nexus

Before anything below, confirm the target SHA's images exist in the local
registry — the promotion steps have nothing to pull otherwise:

```bash
ssh mypc "docker image ls | grep '<short-sha>'"
```

If empty, build first via `vecta-infra`'s `.github/workflows/build-mypc-images.yml`
(`workflow_dispatch`, runs on the `mypc` self-hosted runner):

- `source_sha`: the full 40-char SHA selected in §1.
- `source_branch`: `main` (only accepted value).
- `docker_cache_prune_until`: default `48h` unless told otherwise.
- `image_names`: empty (builds all contract images).

Dispatch: `gh workflow run build-mypc-images.yml -R ZenoWangzy/vecta-infra -f source_sha=<sha> -f source_branch=main`.
Poll with `gh run list -R ZenoWangzy/vecta-infra --workflow build-mypc-images.yml --limit 1`
+ `gh run watch <id> -R ZenoWangzy/vecta-infra`. Success criterion: run
conclusion `success` **and** the target SHA now appears in
`docker image ls` on `mypc` for each service the window touches — the workflow
run going green is necessary but the actual proof is the image being there,
same "exit 0 isn't a criterion" rule as everywhere else in this doc.

The same real-red-vs-network-blip judgment call from §1 applies here too, on a
different workflow. Hit it this window: the dispatched build's own
`Checkout infra contract` step failed with
`fatal: unable to access 'https://github.com/ZenoWangzy/vecta-infra.git/':
Failed to connect to github.com port 443 ... Couldn't connect to server`,
repeated 3 times (the step has its own internal retry loop, all 3 attempts
failed identically) — this looked exactly like `mypc`'s known-flaky direct
path to GitHub (ticket 50). It was not a contract or content problem, but the
"just add the proxy" fix that looks obvious here is **wrong** — see the
warning immediately below before touching this. A `SOURCE_SHA` mismatch, a
provenance-validator failure, or a Docker build error inside the job remain
genuinely-red and need a real stop-and-report instead.

**Do not add the squid proxy to this workflow. It was deliberately removed
(commit `18b5a46`, `fix(release): harden mypc image admission`) and the
removal is locked by `scripts/test_build_mypc_images_contract.py`**
(`assert "ddns.net" not in workflow`, `"PROXY_USERNAME" not in workflow`,
`"PROXY_PASSWORD" not in workflow`, `"HTTPS_PROXY"`/`"https_proxy" not in
download_script`, `"GIT_HTTP_LOW_SPEED_TIME" not in workflow`). This looks
like an oversight the first time you read it — `ci-git-transport-and-proxy.md`
still says "proxy and lowSpeed settings are injected per job by
`build-mypc-images.yml`", which is stale prose left over from before
`18b5a46` and is simply false today (`grep -c "8888\|geraldsynnas"
.github/workflows/build-mypc-images.yml` is `0`). It is not an oversight.
Measured properly this window, as the actual `github-runner` user, 3 samples
each:
```
direct (GIT_CONFIG_GLOBAL=/dev/null, no proxy): exit=0 (1s), exit=0 (3s), exit=0 (1s)
via the proxy (shared ~/.gitconfig): exit=124 (15s), exit=0 (4s), exit=124 (15s)
proxy's own small-request probe (api.github.com/zen): http_code=200, reachable
```
Direct was 3/3 reliable; the proxy was 1/3 despite passing its own probe —
exactly the "small requests pass, bulk/protracted git protocol stalls" GFW
pattern the transport doc itself describes. Routing this step's fetch through
the proxy would make it *less* reliable, not more. `18b5a46` was right;
treat any future urge to "fix" this by re-adding the proxy as a signal to
re-measure before touching code, not to act on the urge.

**A related trap that will bite silently: `/home/github-runner` is shared by
three runner services** (`mypc-vecta-infra-prod-build`, `-2`, and `mypc-ci`).
`ci.yml`'s own `Configure git proxy` step writes `git config --global
http.proxy` into that shared `~/.gitconfig` when it runs on `mypc-ci`, and
unsets it again in its own fallback branch. **Whether this workflow's bare
`git` calls end up going through the proxy therefore depends on which branch
`ci.yml` last happened to take on a completely unrelated runner** — nobody
decided this, and it appears in no workflow file (tracked as ticket 136).
`build-mypc-images.yml` itself is unaffected only because `Checkout infra
contract` and `Download selected VectA source` both set
`GIT_CONFIG_GLOBAL=/dev/null`/`GIT_CONFIG_SYSTEM=/dev/null`, blinding
themselves to that file on purpose — if you ever remove that isolation for
some other reason, you will inherit whatever `ci.yml` last left behind,
silently. **Before every dispatch, check the shared file's current state
without printing its content** (see the file-reading rule two paragraphs
down) — `sudo -u github-runner git config --global --get http.proxy`, read
into a shell variable, echo only whether it's set. If set, `git config
--global --unset-all http.proxy`/`https.proxy` before dispatching (harmless:
`ci.yml` reconstructs this file from scratch, from a fresh probe, every time
it runs, so clearing it never breaks a concurrent or future `ci.yml` run) and
record in the execution log that you did, and what the prior state was.

**Never print the full content of a file on `mypc` that you did not create,
regardless of what kind of file it looks like.** This bit twice this window —
once on `migration-compose.config.yml` (a compose file, `head -30`), once on
`~/.gitconfig` (a git config file, `cat`) — because both times the rule
being followed was "does this kind of file usually hold secrets," and that
judgment call is exactly the thing that keeps being wrong on the file that
wasn't on the mental list yet. The rule that doesn't depend on guessing a
file's contents ahead of time: `grep -c`/`grep -o` for key names only, or read
a single field into a shell variable and echo just a pass/fail or the one
value you actually need — never `cat`/`head`/`sed -n` a whole file that
isn't something you wrote yourself in this session.

## 2a. The cold-clone trap, and why it's fixed now (ticket 143)

`Download selected VectA source` (the step right after the one discussed
above — a different clone, of `vecta` itself, not `vecta-infra`) tries a
warm local cache first and only falls back to a direct clone if that cache
doesn't qualify for the exact `source_sha`. That fallback clone is a full,
cold, `--depth=1` clone of the whole `vecta` monorepo over the same throttled
link — three attempts, up to ~18 minutes each, so a cold cache costs up to
**38 minutes** and can still fail. Hit this for real this window: run
`34084544530` burned 38 minutes across three genuine multi-minute stalls
(`Connection timed out`, then `GnuTLS recv error (-110)` twice) before giving
up entirely.

**Root cause, found by reading the code, not guessing**: the cache
(`/home/github-runner/.cache/vecta-main.git`, a bare shallow repo) qualifies
only if all four hold — directory exists; `git cat-file -e
$SOURCE_SHA^{commit}` resolves; `git rev-parse refs/heads/main` **equals**
`$SOURCE_SHA` exactly (not just "reachable" — the local `main` ref itself
must point there); `git fsck --connectivity-only $SOURCE_SHA` passes. Its
`origin` remote was `file:///home/gerald/project/vecta` — **a path that only
exists on a developer's WSL2 workstation, not on `mypc`.** Nobody could ever
fetch through it. This is the exact same shape as ticket 52's release-checkout
drift: a remote pointing somewhere unreachable, silent until the one moment
it's needed, and it's needed on every single build. The cache wasn't
"coincidentally cold" — it was configured to be permanently unable to warm
itself.

**Fix, in three pieces, all in this repo now**:

1. **The remote**: repointed to `file:///data/ocee/.git` — `/data/ocee` is
   this host's own production `vecta` checkout, full history, already has
   working SSH auth to the real GitHub remote (`git@github.com:...`, root's
   keys). Considered pointing the cache straight at GitHub instead (SSH or
   HTTPS); rejected because `github-runner` has **no SSH key at all**
   (`~/.ssh` doesn't exist for that user) and HTTPS needs the same
   `VECTA_READ_TOKEN` the build job only has as a job secret, not something
   to hand this user permanently. Reusing `/data/ocee`'s already-working,
   already-audited credential is smaller and safer than provisioning a new
   one.
2. **`scripts/warm-vecta-source-cache.sh`** — the two-hop relay, proven
   manually before being turned into this script: `git -C /data/ocee fetch
   origin main` (small, incremental, real SSH auth — 8.4s/529KiB cold, ~5s
   when already close), then `sudo -u github-runner git --git-dir=$CACHE
   fetch --depth=1 --force file:///data/ocee/.git
   refs/remotes/origin/main:refs/heads/main` (local, no auth, sub-second).
   Two things that will bite anyone reimplementing this: the shallow ref
   update is a **non-fast-forward** as far as git can tell (shallow history
   has no common ancestor to compare), so `--force` is required, not
   optional; and the cross-user local fetch trips git's dubious-ownership
   guard the first time, needing one idempotent, config-only
   `git config --global --add safe.directory /data/ocee/.git` as
   `github-runner` (never touches objects or a working tree).
3. **`scripts/warm-vecta-source-cache.{service,timer}`** — a systemd oneshot
   + timer, installed by hand on `mypc` (`/usr/local/sbin/`,
   `/etc/systemd/system/`, `daemon-reload`, `systemctl enable --now
   warm-vecta-source-cache.timer`), firing every 10 minutes. No existing
   Ansible role manages the runner hosts themselves (they were hand-installed
   the same way the timer now is) — formalizing this into Ansible is a
   reasonable follow-up, not required for the fix to work.

**Also changed**: the direct-clone fallback branch now logs *why* it's
falling back (cache present-but-stale vs. missing entirely, with the cache's
actual current tip) before attempting the network clone — previously a
cold-cache fallback and a genuine mid-clone network failure produced
indistinguishable log output. The literal string
`"Cached VectA source unusable; falling back to GitHub"` (a different,
narrower branch — the cache qualified but the local clone from it itself
failed) is unchanged and still covered by
`scripts/test_build_mypc_images_contract.py`.

**Measured effect** — the number that matters, not exit codes:

| | clone step | whole build |
|---|---|---|
| Cold (the 38-minute run) | 18-38 min, failed | failed |
| Warm, this session, manual two-hop | 4s | 3m40s |
| Warm, **fully automatic** (timer-fired cache, next build dispatched with zero manual cache touch in between) | 4s | 1m30s |

The automatic case is the one that matters: the timer fired on its own
schedule (`OnBootSec=2min` after enable, then `OnUnitActiveSec=10min`), and
a build dispatched afterward with no manual intervention on the cache used
it (`"Using cached exact VectA source"` in the run log) and finished the
clone step in 4 seconds.

**Judgment criteria for "is this actually working," not exit codes**:
`systemctl list-timers warm-vecta-source-cache.timer` shows a real `LAST`
and `NEXT`, not just "enabled"; `journalctl -u warm-vecta-source-cache.service`
shows successive `cache warm: refs/heads/main = <sha>` lines advancing as
`main` advances, un-prompted; and a real `build-mypc-images.yml` run's
`Download selected VectA source` step logs `"Using cached exact VectA
source"` and completes in single-digit seconds without you having touched
the cache that session.

## 3. Reading the live `-f` chain (before you touch anything)

**`/data/ocee/migration-compose.config.yml` — the first file in every version of
this chain — carries live plaintext secrets in its services' `environment:`
blocks** (an `A2A_ROUTER_TOKEN` value and a signed `CHANNEL_PLATFORM_SERVICE_TOKEN`
JWT, confirmed present 2026-09-06; ticket 131 tracks whether those specific
values need rotating). **Never `cat`, `head`, or `sed -n` this file, or any
other file in this chain, in full.** Learned this the hard way this window:
`head -30` on it to see the file's shape printed both secrets straight into an
agent transcript. Get the shape of a chain file with `grep`, scoped to keys or
a single field, e.g. `grep -oE '^\s*[A-Z0-9_]+:' file.yml` for key names only,
or `yq '.services.<name>.image' file.yml` for one non-secret field — never an
unscoped dump.

The chain is not written down anywhere stable — it's whatever the currently
running container's own Compose labels say, and it grows by exactly one file
almost every window. **Measured live 2026-09-06, both services: 29 files.**
Any number written in a prior doc (this project has seen 25, 26, 27, 28 quoted
across different windows) is already stale by the time you read it — always
recount:

```bash
docker inspect openclaw-fleet-gateway \
  --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' \
  | tr ',' '\n' | wc -l
docker inspect openclaw-channel-gateway \
  --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' \
  | tr ',' '\n' | wc -l
```
Judgment criterion: both numbers match each other (they have historically
diverged by one file when a service-specific override like
`compose.images.window-b.yml` was added for only one of the two services —
that's a signal to go read what that extra file does before assuming it's
stale, not to ignore the mismatch).

Reuse from `vecta/docs/ops/2026-09-05-tickets-97-101-52-production-ops.md` §3.1:
grepping the full chain for the *old* tag/digest confirms nothing else in the
chain still pins the version you're moving off of by tag — but note that doc's
own correction (§6.2): a file can still pin an old image **by digest** even
when a tag-string grep finds nothing, and the true resolved image is whatever
the **last** file in the chain says (Compose overrides are last-wins). Check
resolution with `docker compose -p openclaw-enterprise <full -f chain> config
--format json`, not by eyeballing which file "looks newest."

*(Chain recount above is a live measurement already taken this window — see
§8 report. The rest of this section is technique carried over from the
2026-09-05 doc, not yet re-run against the new release this window.)*

## 4. Adding the new release

Convention observed in the current chain's last file,
`/data/ocee/releases/main-b375e34309b2/compose.images.yml` (read directly on
`mypc`, reproduced here — no secrets in this particular file, unlike the
project's root `migration-compose.config.yml`, which does carry live tokens in
its `environment:` block and must never be `cat`/`head`-dumped wholesale):

```yaml
# <date> <one-line reason>
services:
  fleet-gateway:
    image: "127.0.0.1:8082/fleet-gateway:<full 40-char sha>"
  channel-gateway:
    image: "127.0.0.1:8082/channel-gateway:<full 40-char sha>"
```

Steps (mechanical, mirrors the existing pattern; executed and verified this
window for `main-b1689bd4908b`, SHA `b1689bd4908b31bb4120e98a34eebb96e6044925`):

1. `mkdir -p /data/ocee/releases/main-<short-sha>`
2. Write `compose.images.yml` there with the two `image:` lines above.
3. **Do not merge fleet-gateway and channel-gateway into one `up -d` command,
   even though both are in the same Compose project.** Their `-f` chains had
   genuinely diverged this window — read live off each container's own
   labels, not assumed identical: fleet-gateway's chain included
   `main-f0c348a285b/compose.hermes-runtime.yml` and
   `main-a27d88298529/compose.images.yml`; channel-gateway's chain instead had
   `main-a27d88298529/compose.images.window-b.yml` and
   `main-a75917bdd980/compose.images.yml` at the same two positions. Neither
   file's presence is explained anywhere in this doc or the 2026-09-05 ops
   record — that's exactly the point: something upstream made a
   service-specific decision at some point, and unifying the two chains into
   one command would silently overwrite whichever one you didn't know the
   history of. Run two separate `docker compose -p openclaw-enterprise
   <that service's own full chain> -f .../compose.images.yml up -d --no-build
   --no-deps <service>` invocations, one per service, each keeping its own
   prior chain intact plus the one new file. The first person to see two
   services in one Compose project will reflexively want to combine them into
   one command — resist it unless you've confirmed the chains are actually
   identical.
4. Verification criteria (not exit code) — **Docker's own `health: healthy`
   is not one of them, it only proves the configured healthcheck command
   exited 0, which can be checking something shallower than the ticket cares
   about. Use the application's own `/healthz` body.** This window:
   `docker exec openclaw-fleet-gateway node -e "fetch('http://localhost:3000/healthz')..."`
   returned `{"ok":true,"checks":{"postgres":true,"redis":true,"litellm":true,"fruitV4":true}}`
   — a per-dependency breakdown, not a bare 200. `channel-gateway`'s returned
   real connection state (`wecom.authenticated:true`, `feishu` connected,
   `personalChannel.connected:4`, nothing `fenced`) — evidence the process is
   actually talking to real channels, not just that its own liveness probe
   passes.
   - `docker inspect openclaw-fleet-gateway --format
     '{{index .Config.Labels "org.opencontainers.image.revision"}}'` equals the
     new full SHA.
   - Same for `openclaw-channel-gateway`.
   - `docker compose -p openclaw-enterprise <full new chain> config --format
     json` resolves both services to the new tag (proves the new file actually
     wins the override, not just that it exists on disk).
   - Both containers report Docker `healthy`, and the application's own
     `/healthz` (proxied through whatever internal path the ops doc used,
     `{"ok":true,"checks":{...}}`) — a container can be Docker-`healthy` on a
     stale healthcheck definition, the JSON body is the real signal.
   - `docker ps -q | wc -l` unchanged from before the promotion (no incidental
     container loss/gain).

## 4a. Verifying a shipped behavior change, not just a shipped revision label

The checks in §4 prove the *containers* moved. They don't prove the *behavior*
a ticket claims to fix actually changed. Worked example from this window
(ticket 126: gateway now passes an explicit `confirmableText` boundary instead
of letting the Hermes hook guess it by splitting on a marker string a user
could type themselves — see the diff in this window's production report for
the full mechanism):

- **Assert the rendered text, not the payload** (project lesson V45). Finding
  `confirmableText` present as a field in an internal log/trace line is weaker
  evidence than watching an actual confirmation flow through to what the user
  would read — a payload field can exist and still not be the thing that
  decided the outcome.
- The safe way to get that evidence in production is to **watch real organic
  traffic after the promotion**, not to fabricate a user message yourself:
  tail `openclaw-channel-gateway`'s logs for a real multimodal (photo +
  text) turn and confirm `authenticatedOrigin.confirmableText` is populated
  and distinct from the naive `\n\n【图片理解结果】`-joined string; then
  correlate with the Hermes hook's own log for that turn confirming it used
  `turn["confirmableText"]` (the validated-prefix path) rather than the
  `user_text.rstrip()` fallback.
- **This only produces evidence if a real multimodal confirmation turn
  happens to occur during the observation window.** If none does, that is a
  real gap, not a soft pass — say so explicitly in the report rather than
  asserting the fix works from the image revision label alone. Manufacturing
  an adversarial test message (the exact shape of the bug: a plain-text
  message containing the marker string with no photo attached) against a real
  tenant/employee in production is not something to improvise without a
  designated test account and explicit sign-off — this runbook does not
  authorize that as a routine step.
- **A promoted `channel-gateway`/`fleet-gateway` image does not mean every
  employee's Hermes runtime container has the fix.** Discovered this window:
  the Python hook half of ticket 126 lives in
  `packages/fruit-industry-pack/runtime/hermes/fruit-v4-workbench-hooks/__init__.py`,
  which `fleet-gateway`'s own image bundles at
  `/app/industry-packs/fruit-v4/runtime/hermes/...` (confirmed present, 5
  `confirmableText` hits, right after promotion) — but that file only reaches
  an individual employee's *running* Hermes container when that employee's
  config gets (re)generated: new container creation, an explicit `POST
  /api/instances/:id/refresh-config`, or the `getOrCreateInstance` self-heal
  path. None of those happened this window (correctly — the pack-identity
  gate in §1c was clean, so no refresh ceremony was owed). **A currently
  running employee container can still be serving the pre-fix hook logic
  after this promotion, and that is expected, not a failure of the
  promotion.** Checking one specific employee container
  (`openclaw-EMPMT78RY7L`) found no `fruit-v4-workbench-hooks` directory
  there at all — cross-checked against `docs/agents/handoffs/2026-09-01-hash-ban-wave2-halted.md`
  §2.5, which already documented that exact employee/tenant pair as V3-only,
  no V4 hooks installed — a pre-existing, known gap, not something this
  window caused or could have fixed. Verifying the *runtime* half of a hook
  change needs picking an employee container that (a) has the pack installed
  and (b) has been refreshed since the promotion — neither of which this
  window's scope covers on its own.

## 5. Rollback

Not a new mechanism — this is exactly what makes the "no `down --volumes`,
only touch this project's own containers" isolation in
`fruit-v4-isolated-production-compose.md` §"Stop, remove, and additive-migration
rollback" already correct for the isolated sidecar. For fleet-gateway/
channel-gateway specifically: the previous release's `compose.images.yml`
(e.g. `main-b375e34309b2/compose.images.yml`) is still on disk and still
last-but-one in the chain being extended, not replaced — dropping the new
file back out of the `-f` list and re-running `up -d --no-build` on the two
services returns them to the prior image **only if that prior image still
exists locally** (`docker image ls`) — see the 2026-09-05 doc §3.6 for a
concrete case where a Nexus tag deletion silently removed a rollback path
that looked reversible on paper. Check local image presence before relying on
any rollback plan, every time.

## 6. `fruit_v4_writer` password rotation (folded into this window, after image promotion + migration evidence)

Executed and verified this window. Sequencing: after fleet-gateway/
channel-gateway were on the new SHA, after the runbook's own migration-profile
backup + restore rehearsal was done
(`fruit-v4-isolated-production-compose.md` "Mandatory external migration
evidence gate"), then rotated.

### 6.0 Check the schema gap before touching anything (lessons `RULES.md` G12)

Rotating the writer password means running the migration profile (it's the
only profile that accepts `FRUIT_V4_WRITER_PASSWORD`/role-provisioning
inputs) — and every migration-profile run re-executes `setup.js`'s
idempotent migrations too, whether or not this ticket touched schema. Check
first whether there's an actual schema gap, because the fix differs if there
is one (see G12, `lessons/RULES.md`):
```
git diff --stat <current running SHA> <target SHA> -- \
  packages/fruit-industry-pack/migrations packages/fruit-industry-pack/src/db/
# and/or
target_count=$(git show <target SHA>:packages/fruit-industry-pack/migrations/meta/_journal.json | jq '.entries | length')
prod_count=$(docker exec openclaw-postgres psql -U openclaw_poc -d openclaw_poc -tAc \
  'select count(*) from fruit_meta.__drizzle_migrations;')
```
This window: diff empty, `40 == 40`, no gap. Ran the migration profile anyway
(needed regardless, for the password), and it correctly no-ops the actual
migrations while still re-provisioning the roles.

### 6.1 The release checkout can drift out from under you — check it before minting `FRUIT_V4_INFRA_REVISION`

Found this window: `$R`'s own `git rev-parse HEAD` was `dd4cc50...`, while the
*already-recorded* `FRUIT_V4_INFRA_REVISION` in `fruit-v4-production.env` said
`19562ce0...` — which turned out to be genuinely current `vecta-infra`
`origin/main` HEAD, just never applied to `$R`'s checkout. The env file was
right; the checkout was stale. Always check both independently (`git -C "$R"
rev-parse HEAD` vs the env file's recorded value vs a trusted `origin/main`)
before assuming either is correct, and if the checkout is behind, fix it with
this doc's own bundle procedure (§"Exact infra checkout gate" in
`fruit-v4-isolated-production-compose.md`) before proceeding — do not just
copy whichever value looks newer into the env file without moving the actual
checkout to match, or the provenance validator's "must equal `git rev-parse
HEAD`" check becomes a lie one field wide.

### 6.2 The restore rehearsal needs its own env file, and a naive sed will corrupt a DSN username that happens to equal another DSN's database name

Building a rehearsal env (a copy of the production env pointed at the
isolated restore database instead of `/openclaw_poc`) by blanket
`sed 's#/openclaw_poc#/<rehearsal_db>#g'` corrupts more than the path: the
migration DSN's *username* here is also `openclaw_poc`
(`postgres://openclaw_poc:...@host:port/openclaw_poc`), and `//openclaw_poc`
contains the literal substring `/openclaw_poc` too. The blanket sed rewrote
the username into the rehearsal database's name, and `setup.js` failed with
`password authentication failed for user "fruit_v4_restore_rehearsal_..."`
— a username that was never supposed to exist. **Anchor the replacement to
end-of-line** (`sed -E 's#/openclaw_poc$#/<rehearsal_db>#'`) so it only
touches the trailing path component of each `DATABASE_URL`-shaped line, never
a username that happens to share the same string earlier in the same line.

### 6.3 `docker exec ... -h localhost` inside `openclaw-postgres` doesn't check the password at all

The first "does the old password still work" check this window gave a false
positive — old, new, *and* a deliberately wrong password all "worked" via
`docker exec openclaw-postgres psql -h localhost -U fruit_v4_writer ...`.
`pg_hba.conf` has `host all all 127.0.0.1/32 trust`: any connection
resolving to loopback from *inside* the container skips password
authentication entirely; only `host all all all scram-sha-256` (everything
else) actually checks it. **Verify a password rotation from a real network
path** — a throwaway container on the same Docker network, addressing
Postgres by its container hostname (not `localhost`/`127.0.0.1`):
```bash
docker run --rm --network openclaw-enterprise_openclaw-net \
  -e PGPASSWORD="$PW" postgres:16-alpine \
  psql -h openclaw-postgres -U fruit_v4_writer -d openclaw_poc -tAc 'select 1;'
```
This window, that path correctly rejected the old password
(`FATAL: password authentication failed`) and accepted the new one — the
`docker exec -h localhost` path had rejected nothing, ever, regardless of
what password was supplied.

### 6.4 A leftover exited setup container blocks the rehearsal's throwaway compose project

`fruit-v4-isolated-setup` is a fixed `container_name` in
`docker-compose.migration.yml`. An exited setup container from an earlier
window's real migration (left in place deliberately, as evidence — this
window found one from earlier the same day, exit 0) collides with *any*
later `docker compose ... up` targeting that service, even under a
different, throwaway `-p` project name for a rehearsal — Compose respects the
literal `container_name` regardless of project. Confirm the existing
container's own exit code is 0 (a genuinely finished, successful prior run,
not something mid-flight) before `docker rm`-ing it to clear the way.

- **Where the value lives**: `FRUIT_V4_WRITER_ROLE` / `FRUIT_V4_WRITER_PASSWORD`
  in `$R/fruit-v4-production.env`
  (`/data/ocee/releases/fruit-v4-gate-a-ff1e7c879e7acd7f83f878f3f4fb3c64f3000629/fruit-v4-production.env`,
  root:root 0600, 23 keys). It feeds `FRUIT_V4_WRITER_DATABASE_URL` in the
  same file (not a separate DSN — check both are updated consistently, they're
  two views of the same credential).
- **Blast radius to confirm before changing anything**: grep *key names only*
  (never values) for `FRUIT_V4_WRITER` across both `$R/fruit-v4-production.env`
  and `$R/deploy/fruit-v4/.env` (the 13-key compose-dir file) — if the writer
  role/password shows up in more than these two known locations, or in
  anything outside the `fruit-v4-gate-a-*` release directory, **stop and
  report** per the brief; do not rotate until that's accounted for.
- **Who needs to restart**: only `fruit-v4-isolated-setup`/`fruit-v4-isolated-uat`
  consume `FRUIT_V4_WRITER_DATABASE_URL` (per
  `fruit-v4-isolated-production-compose.md` — it's a setup-only /
  controlled-entry-writer input, not consumed by fleet-gateway or
  channel-gateway at all). Rotating it means: update the password in Postgres
  itself first (`ALTER ROLE fruit_v4_writer WITH PASSWORD '<new>'`, or via
  whatever provisioning the migration profile's `FRUIT_V4_RUNTIME_DB_ROLE`/
  `FRUIT_V4_WRITER_ROLE` setup step already does — check
  `packages/fruit-industry-pack/dist/db/setup.js` behavior before assuming
  `ALTER ROLE` outside of it doesn't get clobbered on the next setup run),
  then update both env files, then recreate (not just restart —
  `docker compose up -d --no-build`, since the DSN is baked in at container
  start, not re-read live) `fruit-v4-isolated-uat`.
- **How to verify the old password is actually dead, not just replaced on
  paper**: see §6.3 — must be a real network-path connection, not
  `docker exec ... -h localhost` (trust-authenticated, proves nothing). This
  window: old password → `FATAL: password authentication failed` (rejected,
  confirmed); new password → connects (confirmed); `fruit-v4-isolated-uat`
  recreated on the new digest afterward, `/healthz` reported
  `{"ok":true,...,"checks":{...,"postgres":true,...}}` and its logs showed no
  connection errors since start. Neither password value was ever printed —
  only pass/fail went in the evidence record.
- **Clean up the rehearsal artifacts after the real migration succeeds, not
  before**: the isolated restore database, the rehearsal env file (it embeds
  the real new password), and any temp file holding the generated password
  server-side all get deleted only once the *actual* production migration run
  has also succeeded — deleting the rehearsal evidence before you've proven
  the real run works removes your fallback reference with nothing to show for
  it.

## 7. What this file does not cover

`rag-service`, `directory-service`, `a2a-router`, `baidu-search-service`,
`admin-console` — contract-excluded per the current chain's own comment,
tracked as separate tickets. The `openclaw-fruit-feishu-gateway` container
(no compose project, no systemd, no cron — see the 2026-09-06 investigation
in this window's report) is explicitly out of scope here too; it has its own
follow-up ticket.

## 8. Release the production deploy lock

Last step, after §4/§4a's verification (and §6's, if this window rotated the
writer password) all pass — not before, and not skipped because "it's just a
`rm`":

```bash
ssh mypc 'bash -s -- release' < scripts/deploy-lock.sh
```

If you forget, it self-expires (`production-deploy-lock.md`'s ttl backstop)
— but the next operator's `status` check works whether or not you remembered,
so release explicitly rather than relying on that.
