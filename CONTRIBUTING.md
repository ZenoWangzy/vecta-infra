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

- Normal VectA changes start on a topic branch and merge to `develop` first.
  That merged SHA must complete the required postsubmit and promotion evidence
  before it can promote through `develop -> main`.
- A production repair starts on `hotfix/<name>` from VectA `main` and merges to
  `main`. Once the main SHA is verified, the exact change returns through
  `main -> develop` before later promotion.
- VectA `main` is the production release lane. It runs the protected mypc
  release checks. A production deploy remains a separately approved action.
- Infrastructure workflows must preserve those branch and runner boundaries,
  policy checks, and promotion evidence. Do not broaden an image build into a
  production deploy.
- Images use immutable full Git SHA tags for normal delivery. Production cache
  adoption uses immutable `cache-<image-id>` tags only when preserving a live
  container image is required.

## Production Image Build Evidence

The production image build is manually dispatched from `vecta-infra` main and
requires production-environment approval on the `prod-build` runner. The
operator supplies a full `source_sha`, `source_branch=main`, and an optional
`image_names` subset. The workflow rejects any SHA that is not the current
VectA main HEAD.

A successful run is independent exact-SHA image-build evidence. It is not a
VectA postsubmit result, merge result, production deployment, or production
health claim.

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

## Required Evidence

- Workflow changes: YAML parse, relevant workflow contract tests, and a review
  of branch and runner conditions.
- Deployment changes: Ansible syntax check, pre/post regression, and immutable
  image evidence.
- Stateful adoption: backup/checksum evidence, original mount mapping, service
  health, and full post-adoption regression.
