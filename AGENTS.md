# AGENTS.md

Read `CONTRIBUTING.md` before non-trivial work. This repository owns the
production Nexus, Ansible deployment, backup/rollback, health, and release
evidence boundary for VectA.

The active delivery lane is one VectA `main` SHA: fixed-SHA static review,
then the single manual `Release mypc production` workflow. The workflow must
build the selected images, record immutable digests, run contention and backup
preflight, deploy exact digests only when its explicit production switch is
enabled, verify health, and expose the optional Hero E2E and business Oracle
interfaces. A workflow run, image digest, deployment, health result, and real
user/Oracle result remain separate evidence.

Historical migration mode is conditional on the selected VectA checkout. It
does not make an unpublished migration candidate or future authority a
dependency of the default build/release path. When selected source admission
passes, the mode consumes the 0029 -> 0030 journal, fresh backup/isolated
restore, writer quiesce, exact Fruit digest, and serial batch interface. It
also requires every loss Action to retain its frozen source-row evidence and
the `v4_documents_type_chk` loss vocabulary update in 0030 or a later journal
entry with a restoring rollback. The DDL stays inside the quiesced, shared
release lock window; a blocked source or unresolved business plan fails
closed. The forward loss CHECK update must be the final executable segment of
0030 and is applied by the Drizzle PostgreSQL migrator's whole migration
transaction, so its `ACCESS EXCLUSIVE` lock is held until that transaction
commits. The sequence is stop/quiesce writers, bounded `lock_timeout`, migrate,
verify journal/schema, run a read-only smoke, then restart writers. Schema
rollback is a separate self-contained `BEGIN`/`COMMIT` transaction with
`SET LOCAL lock_timeout`, locks `fruit.v4_documents` before
`fruit.v4_historical_import_batches`, runs its guard before DROP/restore, and
also requires writers to be quiesced.

The old branch-promotion and independent test/container-test paths are not
active release gates. Historical files may remain for audit context, but do
not add new callers or make them required. Do not read, print, or commit
credentials; keep secret values in the CI secret store and use `no_log` for
Ansible tasks that handle them.

Production execution is fail-closed: the default workflow input is disabled,
the repository variable `MYPC_RELEASE_ENABLED=true` is also required, and a
real host plus explicit operator authorization are required before a deploy.
Transient checkout/fetch/runner/Nexus/registry failures may retry at most
three times at the same source SHA. Deterministic build, schema, auth, health,
E2E, or Oracle failures stop and identify the repair owner.

Keep this file as the agent entry point. Put durable repository conventions in
`CONTRIBUTING.md`, not generated indexes or repeated detail here.
