# AUDIT_DIR volume, chain survival, and verification

This runbook defines the vecta#1036 deployment contract. The Ansible role is
the only owner of the Fleet gateway audit volume and its mount.

## Evidence boundary

The material recorded from 2026-09-07 is 历史生产记录（historical production
record），不是当前 live 证据. Re-check the target host and the deployed image before
making any current production claim.

- VectA PR #1070 merged the audit-chain verifier into VectA main.
- VectA PR #1076 merged separately for Redis PEL recovery; it does not replace
  the vecta#1036 persistence contract.
- vecta#1036 still requires the vecta-infra deployment and current production
  persistence, verifier, and alert evidence.
- VectA #1054 is the retention follow-up. Its current main-branch boundary is
  one year for audit_log and indefinite retention for usage records.

The merged state of #1070 or #1076, an old production transcript, and a
successful static contract check are not proof that the current host is
deployed or healthy.

## 1. Failure being fixed

Fleet gateway audit JSONL was written below /app/data/audit while the path was
inside the container writable layer. Recreating or rolling back the image
could therefore discard the audit chain. The deployment must expose:

- AUDIT_DIR=/app/data/audit;
- one external Docker named volume at that path;
- the same validated runtime uid:gid for volume initialization, container
  execution, and the writeability check.

The inventory value fleet_gateway_user is the source configuration. The role
first verifies that the running container reports the same Config.User, then
uses that live value as the validated runtime identity. The role must fail
closed if the two values differ.

## 2. Canonical implementation

The canonical owner is:

roles/vecta-app/tasks/fleet_gateway_mypc.yml

The relevant inventory values are:

- fleet_gateway_audit_volume: fleet_gateway_audit_data
- fleet_gateway_audit_dir: /app/data/audit
- fleet_gateway_user: 1000:1000

The inventory uid:gid matches the Fleet gateway image runtime defaults. The
role still validates the live container before using it; the role does not
hardcode a uid:gid.

The role contract is:

1. Preflight the current container and allow either no audit mount or exactly
   the expected named-volume mount and AUDIT_DIR value.
2. Check whether the running container already has the expected named volume,
   AUDIT_DIR, validated runtime identity, and successful writeability
   postcondition. If it already satisfies all four, a normal rerun skips stop,
   recursive chown, volume preparation, and forced recreation.
3. Only when that check is not compliant, stop the running gateway first so all
   audit writers are quiesced. Do not inspect or repair the audit volume before
   this stop.
4. Ensure the named Docker volume exists with
   community.docker.docker_volume. This volume is external to the container
   lifecycle; it is not owned by a Compose overlay.
5. As root, inspect whether the stopped volume is empty or non-empty, then
   inspect ownership against the validated runtime identity. `find` failures
   are fatal. Repair ownership only when it differs; a successful no-op check
   must remain unchanged and a failed `chown` is fatal.
6. Seed only when the stopped volume was confirmed empty. Stage the existing
   audit directory, copy it into the named volume, and set the validated
   runtime ownership. `mktemp`, `docker cp`, copy, and chown failures fail
   closed. A non-empty volume never enters the seed path.
7. Recreate the gateway with AUDIT_DIR and the named-volume mount, using the
   validated runtime identity.
8. Require the recreated container to report the expected Config.User,
   AUDIT_DIR, named-volume destination, read-write mode, and successful write
   and cleanup as that identity.

There is deliberately no second owner under deploy/gateways and no second
Compose overlay. Adding a second deployment path would let
the role and the overlay disagree about the volume, user, or rollback
behavior.

## 3. Data preservation and first-use migration

The first-use path is a one-time data migration from the container writable
layer into the external named volume. The gateway is stopped before the empty
check, ownership inspection/repair, or copy, so writers are quiesced while the
source is staged. A non-empty volume is retained in place and is never seeded.
A failed find, ownership repair, copy, or postcondition fails the role and does
not delete the source container or the named volume.

The 2026-09-07 rehearsal and any earlier host transcript are historical
production records, not current live evidence. They can explain the original
failure and the migration shape, but they cannot establish the current
container, volume, image, or alert state.

## 4. Minimum verification contract

scripts/test_fleet_gateway_audit_volume_contract.py is included by the
existing .github/workflows/pr-contract-checks.yml loop over scripts/test_*.py.
It must cover all of the following without adding a dependency:

- the role is the only owner and the old Compose overlay is absent;
- the external named volume maps to /app/data/audit;
- AUDIT_DIR is set to that same path;
- preflight, initialization, recreation, and postcondition use one validated
  uid:gid input;
- a compliant normal rerun skips stop, recursive chown, and forced recreation;
- migration stops the gateway before the empty check and ownership repair;
- an empty-volume check is fail-closed;
- ownership repair runs only when the ownership check reports a mismatch;
- a non-empty volume never enters the seed path;
- the recreated container must have the exact volume type, name, destination,
  read-write mode, runtime identity, and writeability behavior.

The related Fruit host-bind contract remains part of the same PR gate.

## 5. Retention boundary

Retention is not part of vecta#1036. VectA #1054 owns the retention decision
and follow-up: audit_log has the current one-year policy, while usage records
remain indefinite. This change only makes JSONL durable at AUDIT_DIR.

The role must not prune the named volume, add a scheduler, change database
retention, or introduce a legal-hold/export policy. Any future JSONL
retention, export, legal hold, or data migration is a separately approved
stop-writers and recovery operation.

## 6. Rollback boundary

Normal image rollback must reuse the same role contract. It retains the named
volume, AUDIT_DIR, and validated runtime identity while changing only the
image revision and other image-specific runtime settings.

Removing persistence is not a normal image rollback. It requires a separately
approved operation that:

1. stops all audit writers;
2. exports or migrates the named-volume data;
3. verifies restore and readability;
4. applies the explicitly approved persistence change; and
5. recreates the gateway and re-runs the writeability and chain checks.

An image rollback must never remove the audit mount or delete the named
volume.
