# Contributing To VectA Infrastructure

## Repository responsibilities

- `vecta` owns product code, schemas, application image definitions, and the
  source-SHA contract.
- `vecta-infra` owns Nexus operations, Ansible roles/inventories, deployment
  preflight, backup/rollback, health, and release evidence.
- A cross-repository change must record both candidate commits explicitly.
  Neither repository may rely on an unpublished commit in the other.

## Active delivery lane

The only active trunk is VectA `main`. The release path is:

1. implement on a topic branch;
2. fixed-SHA static review and targeted repair by the same owner;
3. merge to VectA `main`;
4. manually dispatch this repository's single release workflow with the full
   current `main` SHA;
5. retain separate build, immutable digest, backup/contention, exact-digest
   deployment, health, real-user path, business Oracle, and rollback evidence.

Old promotion configuration is transition-only and is removed by the
one-time trunk cutover runbook after both repositories and their consumers
have been reconciled. It is not a new release gate.

## Release workflow contract

`.github/workflows/build-mypc-images.yml` is the release entry point. It is
manual and defaults to inert production execution. Its source checkout must
match the current VectA `main` HEAD, selected images are pushed under that
source SHA, and the resulting `sha256` digest manifest is the only image
reference accepted by Ansible.

The workflow uses the existing Nexus and Ansible roles. Existing application
mounts, networks, environment, tenant/RBAC boundaries, ledger/idempotency
controls, and stateful data owners remain authoritative. Database writes,
migrations, and historical imports are serial and require their own approved
preflight. A failed deterministic step stops with a concrete repair owner;
only transient checkout/fetch/runner/Nexus/registry startup failures may retry
three times at the same SHA.

The default path does not add Vitest, vtest, Testcontainers, or an independent
test stage. Build/typecheck, YAML/static contract, schema/migration/backup,
health, real-user, and business-Oracle evidence are the relevant controls.

The optional history mode validates the checked-out VectA source itself before
using it. It may consume the frozen 0029/0030 migration contract only after
that exact source is selected; it must not require an unpublished future
candidate or its unreconciled runbook for ordinary image builds. If enabled,
the mode remains fail-closed until backup/restore, writer quiesce, exact
digests, serial batch/rollback evidence, row evidence for every loss Action,
and the `v4_documents_type_chk` loss update (in 0030 or a later journal entry)
with its restoring rollback are present. The constraint DDL runs only inside
the quiesced shared release lock window. For the current 0030 contract, the
loss CHECK ALTER is the final executable migration segment and Drizzle's
PostgreSQL migrator holds the migration transaction, including the
`ACCESS EXCLUSIVE` lock, through commit. History execution is ordered as
stop/quiesce writers → bounded `lock_timeout` → migrate → journal/schema
verification → read-only smoke → restart. Rollback is self-contained and
transactional, locks `fruit.v4_documents` before
`fruit.v4_historical_import_batches`, guards before DROP/restore, and requires
the same writer-quiesce boundary.

## Safety boundaries

- Never deploy, migrate, or write production data from a local validation run.
- Never create replacement state volumes or alter live mount paths during an
  image release.
- Keep Ansible credentials in the secret store; do not echo, inspect, or write
  their values. Use `no_log` on credential-bearing tasks.
- Preserve exact prior image digests and backup evidence for rollback.
- Commit cohesive changes. Push, merge, branch deletion, and cutover require
  explicit authorization outside this candidate work.

## Required local evidence

- Workflow changes: YAML parse, `scripts/check-mypc-release-workflow.py`,
  `git diff --check`, and branch/runner/production-guard inspection.
- Deployment changes: Ansible syntax check and `--list-tasks` against the
  empty non-production syntax fixture; the release workflow requires a
  protected external inventory and no production execution is implied.
- Stateful or migration changes: explicit schema/ledger inventory, backup
  checksum, restore/rollback path, and post-change health evidence from the
  approved operator run.
