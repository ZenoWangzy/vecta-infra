# Contributing To VectA Infrastructure

## Repository Responsibilities

- `vecta` owns product code, schemas, tests, application image definitions, and
  the GitHub workflow that selects branch delivery lanes.
- `vecta-infra` owns Ansible roles, inventories, Nexus registry operations,
  environment deployment contracts, and post-deploy verification.
- A change crossing these repositories must update both contracts in the same
  delivery sequence. Do not make one repository rely on unpublished work in the
  other.

## Branch Delivery Convention

- VectA `develop` is the vtest integration lane. It runs vtest validation,
  image build, deployment, and post-deploy regression only.
- VectA `main` is the production release lane. It runs the same vtest lane and
  the protected mypc image-build lane. A production deploy remains a separately
  approved action.
- Infrastructure workflows and reusable jobs must preserve those branch and
  runner boundaries; do not broaden a vtest trigger into a production deploy.
- Images use immutable full Git SHA tags for normal delivery. Production cache
  adoption uses immutable `cache-<image-id>` tags only when preserving a live
  container image is required.

## Change Rules

1. Read `AGENTS.md` and this file before non-trivial work.
2. Keep a dirty worktree intact. Use a clean worktree for branch merges and
   release validation.
3. Validate the narrowest affected contract, then run broader regression when a
   change crosses application, workflow, registry, or deployment boundaries.
4. Production and vtest state are data-first: preserve existing volume names,
   bind paths, ports, networks, environment contracts, and rollback evidence.
   Never create replacement data volumes or prune state as part of an image
   migration.
5. Commit cohesive changes with a clear scope. Push or merge only when
   explicitly requested or required by an approved delivery step.

## Required Evidence

- Workflow changes: YAML parse, relevant workflow contract tests, and a review
  of branch and runner conditions.
- Deployment changes: Ansible syntax check, pre/post regression, and immutable
  image evidence.
- Stateful adoption: backup/checksum evidence, original mount mapping, service
  health, and full post-adoption regression.
