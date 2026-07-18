# Vtest And mypc Migration Workflow

## Purpose

Use vtest to validate source and deployment changes first. Use mypc only for a
separately approved production image adoption that preserves production data.
Never use the vtest registry or volumes as a production source.

## 1. Vtest Delivery

1. Merge reviewed code to `develop`.
2. GitHub builds the complete image set with the immutable full Git SHA and
   pushes it to vtest Nexus.
3. `vecta-infra` deploys that same SHA to vtest.
4. Run the post-deploy regression, smoke checks, and public ingress checks.
5. Record the workflow URL, deployed SHA, regression result, and any explicit
   schema migration in the deploy audit.

Failure rule: fix forward on `develop`, rebuild a new immutable SHA, and repeat
the complete vtest gate. Do not copy vtest volumes or use a floating image tag.

## 2. mypc Image Preparation

1. Inspect the live mypc container contract: image ID, mounts, ports, network,
   restart policy, resources, command, and non-secret environment keys.
2. Mirror the exact running image into mypc Nexus under `cache-<image-id>`.
   For protected repositories, authenticate with `MYPC_NEXUS_ADMIN_PASSWORD`.
3. Verify the Nexus image has matching Linux filesystem layers. A different
   upstream floating tag is not eligible for adoption.
4. Keep `huoke-*` outside this workflow unless it has an independently approved
   migration plan.

## 3. mypc Data-first Adoption

1. Run read-only regression before the selected service.
2. Use `playbooks/mypc-adopt.yml` for one container only.
3. Archive every existing mount under `/data/ocee/backups/app-adoption/` and
   retain its inspect contract and checksums.
4. Recreate using the Nexus cache image while preserving the original mount
   strings, ports, networks, command, environment, restart policy, and limits.
5. For a changing state volume, quiesce only that container before the archive.
6. Verify the container starts and its service-specific health probe passes.
7. Run `scripts/mypc-data-layer-regression.sh --service all --phase after`.

Failure rule: stop the sequence. Restore the previous image reference and exact
container contract; do not create replacement volumes or prune any image/volume.

## 4. Stateful Order And Exceptions

- Redis, MinIO, LiteLLM, and PostgreSQL use their existing data volumes and
  retain their service-specific backup and restore gates. PostgreSQL remains
  last.
- Open WebUI, OnlyOffice, RAG, Fleet, Channel, and Hermes retain their original
  production mounts. Hermes migration is strictly one runtime at a time.
- Channel Gateway remains the single active `primary` owner for `mypc`.
- No ClickHouse service is adopted while production has no current owner.

## 5. Completion Criteria

The migration is complete only when every in-scope VectA container uses an
immutable mypc Nexus reference, the original stateful mounts remain attached,
backups/checksums exist, and the full mypc regression passes. Vtest and mypc
evidence are recorded independently because they use different registries,
data, and release provenance.
