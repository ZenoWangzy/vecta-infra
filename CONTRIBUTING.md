# Contributing To VectA Infrastructure

## Repository Responsibilities

- `vecta` owns product code, schemas, tests, application image definitions, and
  the GitHub workflow that selects branch delivery lanes.
- `vecta-infra` owns Ansible roles, inventories, Nexus registry operations,
  environment deployment contracts, and post-deploy verification.
- A change crossing these repositories must update both contracts in the same
  delivery sequence. Do not make one repository rely on unpublished work in the
  other.

## vtest Retirement Merge Order

vtest is permanently retired. Its removal is a two-repository delivery with one
safe order:

1. The VectA caller-removal hotfix must merge first and publish a state with no
   caller of the retired infrastructure workflow.
2. The matching `vecta-infra` removal merges only after that caller-free VectA
   state exists.

Do not restore a compatibility workflow, wrapper, fallback, or runner because a
local dirty worktree or unpublished branch still contains an old caller. The
cross-repository synchronization principle above remains mandatory for future
contract changes.

## Branch Delivery Convention

- VectA runs a single trunk: `main` is its only long-lived branch. Normal
  changes start on a topic branch and merge to `main` through a PR. There is no
  integration branch, no promotion PR, and no return leg.
- A production repair is an ordinary topic branch (`hotfix/<name>`) into VectA
  `main`, not a separate lane.
- The merged VectA `main` SHA must complete its own exact-SHA
  `Postsubmit validate` job before it is eligible for a production image build.
- VectA `main` is the production release lane. It runs the protected mypc
  release checks. A production deploy remains a separately approved action.
- Infrastructure workflows must preserve those branch and runner boundaries,
  policy checks, and release evidence. Do not broaden an image build into a
  production deploy.
- Images use immutable full Git SHA tags for normal delivery. Production cache
  adoption uses immutable `cache-<image-id>` tags only when preserving a live
  container image is required.

## Production Image Build Evidence

The production image build is manually dispatched from `vecta-infra` main and
is authorized by the dispatching repository writer. The `production`
environment is an audit label; it currently has no required reviewers or
protection rules. Existing workflow secrets remain repository-level and are not
migrated by this contract. The operator supplies a full `source_sha`,
`source_branch=main`, and an optional `image_names` subset. The workflow rejects
any SHA that is not the current VectA main HEAD.

A successful run is independent exact-SHA image-build evidence. It is not a
VectA postsubmit result, merge result, production deployment, or production
health claim.

### Why the gates live inside the workflow

Branch protection and rulesets are unavailable on both repositories' plan — the
REST API answers 403 "Upgrade to GitHub Pro or make this repository public" —
and the `production` environment has an empty `protection_rules` list with
`can_admins_bypass=true`. No gate here may be written as if a protected branch
or an environment approval existed.

`build-mypc-images.yml` therefore enforces its own preconditions at run time and
exits non-zero when it cannot prove them:

| Check | Where |
| --- | --- |
| Only this repository, only `workflow_dispatch`, only `refs/heads/main` | the `build` job's `if:` |
| `source_branch` can only be `main` | the input's `choice` options |
| `source_sha` is a full 40-hex lowercase SHA | `Validate selected SHA` |
| `source_sha` equals the live `ZenoWangzy/vecta` `main` HEAD, read from the API during the run | `Download selected VectA source` |
| That SHA has a completed, successful `Postsubmit validate` job on a `push` run | `Require exact-SHA VectA Postsubmit evidence` -> `scripts/verify-vecta-postsubmit.py` |

The single-trunk change requires no modification to these checks:
`verify-vecta-postsubmit.py` already rejects any branch other than `main`, and
the SHA freshness check already reads `refs/heads/main` live. Removing or
weakening any of them removes the only mechanical control that exists.

## Change Rules

1. Read `AGENTS.md` and this file before non-trivial work.
2. Keep a dirty worktree intact. Use a clean worktree for branch merges and
   release validation.
3. Validate the narrowest affected contract, then run broader regression when a
   change crosses application, workflow, registry, or deployment boundaries.
4. Production/myPC state is data-first: preserve existing volume names, bind
   paths, ports, networks, environment contracts, and rollback evidence. Never
   create replacement data volumes or prune state as part of an image migration.
5. Commit cohesive changes with a clear scope. Push or merge only when
   explicitly requested or required by an approved delivery step.
6. Never delete or weaken the `build` job's `if:` guard in
   `build-mypc-images.yml`. It runs on `[self-hosted, mypc, prod-build]` — the
   production machine — and the guard restricting it to this repository,
   `workflow_dispatch`, and `refs/heads/main` is the only thing preventing an
   arbitrary branch push from scheduling work there.
7. Never reference the `runner` context in a job-level `env:` block. GitHub
   rejects the workflow file outright, and because the rejection surfaces as a
   failed run on the pushing branch rather than a normal job failure, it reads
   like a build failure instead of a syntax error.

## Required Evidence

- Workflow changes: YAML parse, relevant workflow contract tests, and a review
  of branch and runner conditions. Confirm the changed file still parses on
  GitHub itself — a rejected workflow file produces a failed run with zero jobs
  and the message "This run likely failed because of a workflow file issue.".
- Deployment changes: Ansible syntax check, pre/post regression, and immutable
  image evidence.
- Stateful adoption: backup/checksum evidence, original mount mapping, service
  health, and full post-adoption regression.
