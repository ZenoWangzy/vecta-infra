# Fruit V4 Isolated Production Compose Contract

This contract is the `vecta-infra` owner for one isolated Fruit V4 UAT
instance. It does not modify, restart, route, or remove any V3 service. The
Compose project publishes no host port and joins only the operator-supplied
canonical production Docker network.

## Owned resources and provenance

| Resource | Contract value |
| --- | --- |
| Compose file | `deploy/fruit-v4/docker-compose.yml` |
| Compose project | `fruit-v4-isolated-production` |
| setup service/container | `fruit-v4-setup` / `fruit-v4-isolated-setup` |
| UAT service/container | `fruit-v4-uat` / `fruit-v4-isolated-uat` |
| UAT Docker DNS alias | `fruit-v4-isolated-uat` on the canonical network |
| image input | `${FRUIT_V4_IMAGE_REGISTRY}/fruit-industry-pack@sha256:${FRUIT_V4_IMAGE_DIGEST}` |
| image source | `ZenoWangzy/vecta` at `FRUIT_V4_SOURCE_SHA` |
| deployment source | `ZenoWangzy/vecta-infra` at `FRUIT_V4_INFRA_REVISION` |

Setup and UAT consume the same digest-only image. The Compose file contains no
`build`, floating tag, host `ports`, `env_file`, volume, or second network.
The setup service first performs an image-internal Node.js preflight and then
executes `node packages/fruit-industry-pack/dist/db/setup.js`. UAT executes the
exact image's Fruit CLI.

`FRUIT_V4_SOURCE_SHA` is always the full VectA `main` source SHA represented
by the image. It must never contain the infra revision. The independent
`FRUIT_V4_INFRA_REVISION` label identifies the full `vecta-infra` deployment
contract revision used by the operator.

## Required live inputs

The operator supplies these values from approved secret, release, backup, and
change-control systems. They are not committed here and secret values must not
be printed in evidence:

- `FRUIT_V4_IMAGE_REGISTRY`: registry host (and optional port) without a path,
  tag, or digest; the contract fixes the repository to `fruit-industry-pack`.
- `FRUIT_V4_IMAGE_DIGEST`: exactly 64 lowercase hexadecimal characters; the
  Compose file supplies the `sha256:` prefix.
- `FRUIT_V4_SOURCE_SHA`: full 40-character VectA `main` SHA represented by
  the image.
- `FRUIT_V4_INFRA_REVISION`: full 40-character `vecta-infra` deployment
  revision containing this contract.
- `FRUIT_V4_CANONICAL_NETWORK`: pre-existing canonical production network.
- `FRUIT_V4_WRITER_DATABASE_URL`: setup-only migration/writer DSN.
- `FRUIT_V4_RUNTIME_DATABASE_URL`: least-privilege runtime DSN. Setup receives
  it only to compare its destination and username with the writer DSN.
- `FRUIT_V4_EXPECTED_DATABASE_HOST`, `FRUIT_V4_EXPECTED_DATABASE_PORT`, and
  `FRUIT_V4_EXPECTED_DATABASE_PATH`: independently approved database endpoint
  components. Both DSNs must explicitly match all three values.
- `FRUIT_V4_RUNTIME_DB_ROLE` and `FRUIT_V4_RUNTIME_DB_PASSWORD`: setup-only
  runtime-role provisioning inputs.
- `FRUIT_V4_BACKUP_SHA256`: checksum of the fresh pre-migration custom dump.
- `FRUIT_V4_RESTORE_REHEARSAL_ID`: evidence ID for the isolated restore and
  exact-image setup rehearsal.
- `FRUIT_V4_OPERATOR_APPROVAL_ID`: approval authorizing this one setup run.
- `FRUIT_V4_MIGRATION_GATE`: must equal `approved-one-shot` for setup.
- `FRUIT_V4_SERVICE_SECRET`: UAT service credential.
- `FRUIT_V4_ALLOWED_TENANT_IDS` and `FRUIT_V4_ALLOWED_EMPLOYEE_IDS`:
  approved UAT allowlists.

Compose uses `:?` interpolation for every required input. Missing inputs fail
during `docker compose config`. The setup preflight additionally rejects an
invalid full SHA/digest/checksum, a gate other than `approved-one-shot`, a DSN
that is not PostgreSQL, either DSN not matching the approved host/explicit
port/path, an empty username, or equal writer/runtime usernames.

Only the setup container receives the writer DSN and runtime-role password.
Only UAT receives the service secret and allowlists. V3 containers receive none
of these values.

## Mandatory migration evidence gate

Setup is a forward migration and authorization operation. The following gate is
mandatory before setting `FRUIT_V4_MIGRATION_GATE=approved-one-shot`:

1. Quiesce the approved migration window and create a fresh, custom-format
   database dump using the approved backup tooling and protected destination.
2. Compute the dump's SHA-256 checksum, store the dump and checksum in the
   approved backup system, and set `FRUIT_V4_BACKUP_SHA256` to that recorded
   checksum.
3. Restore that exact dump into an isolated, non-production database.
4. Against the isolated restore, run
   `${FRUIT_V4_IMAGE_REGISTRY}/fruit-industry-pack@sha256:${FRUIT_V4_IMAGE_DIGEST}`
   and execute its exact
   `node packages/fruit-industry-pack/dist/db/setup.js`.
5. Verify the isolated restore and setup rehearsal, then record the immutable
   evidence as `FRUIT_V4_RESTORE_REHEARSAL_ID`.
6. Obtain a separate operator approval for the exact VectA source SHA, image
   digest, backup checksum, rehearsal ID, approved database host/port/path, and
   infra revision. Record it as `FRUIT_V4_OPERATOR_APPROVAL_ID`.

The Compose preflight proves only that required identifiers are present and
well-formed and that the two DSNs match the approved endpoint inputs. It cannot
prove that a backup is fresh or authentic, that a restore rehearsal happened,
or that an approval is valid. Those facts remain external hard-gate evidence
and must be independently accepted before setup.

## Preflight, pull, setup, and UAT start

Run from the checkout containing this file. Do not run these commands against a
V3 Compose project.

```bash
set -euo pipefail
contract=deploy/fruit-v4/docker-compose.yml

: "${FRUIT_V4_IMAGE_REGISTRY:?required}"
: "${FRUIT_V4_IMAGE_DIGEST:?required}"
: "${FRUIT_V4_SOURCE_SHA:?required}"
: "${FRUIT_V4_INFRA_REVISION:?required}"
: "${FRUIT_V4_CANONICAL_NETWORK:?required}"
: "${FRUIT_V4_WRITER_DATABASE_URL:?required}"
: "${FRUIT_V4_RUNTIME_DATABASE_URL:?required}"
: "${FRUIT_V4_EXPECTED_DATABASE_HOST:?required}"
: "${FRUIT_V4_EXPECTED_DATABASE_PORT:?required}"
: "${FRUIT_V4_EXPECTED_DATABASE_PATH:?required}"
: "${FRUIT_V4_RUNTIME_DB_ROLE:?required}"
: "${FRUIT_V4_RUNTIME_DB_PASSWORD:?required}"
: "${FRUIT_V4_BACKUP_SHA256:?required}"
: "${FRUIT_V4_RESTORE_REHEARSAL_ID:?required}"
: "${FRUIT_V4_OPERATOR_APPROVAL_ID:?required}"
: "${FRUIT_V4_MIGRATION_GATE:?required}"
: "${FRUIT_V4_SERVICE_SECRET:?required}"
: "${FRUIT_V4_ALLOWED_TENANT_IDS:?required}"
: "${FRUIT_V4_ALLOWED_EMPLOYEE_IDS:?required}"

case "$FRUIT_V4_IMAGE_REGISTRY" in
  */*|*@*|'') echo 'image registry must not contain a path or digest' >&2; exit 1 ;;
esac
test "$FRUIT_V4_MIGRATION_GATE" = approved-one-shot
printf '%s' "$FRUIT_V4_IMAGE_DIGEST" | grep -Eq '^[0-9a-f]{64}$'
printf '%s' "$FRUIT_V4_SOURCE_SHA" | grep -Eq '^[0-9a-f]{40}$'
printf '%s' "$FRUIT_V4_INFRA_REVISION" | grep -Eq '^[0-9a-f]{40}$'
printf '%s' "$FRUIT_V4_BACKUP_SHA256" | grep -Eq '^[0-9a-f]{64}$'
docker network inspect "$FRUIT_V4_CANONICAL_NETWORK" >/dev/null

# Validate setup and UAT interpolation without printing DSNs or secrets.
docker compose --profile migration -f "$contract" config --quiet

# First deployment pulls the approved immutable reference before inspecting it.
image_ref="${FRUIT_V4_IMAGE_REGISTRY}/fruit-industry-pack@sha256:${FRUIT_V4_IMAGE_DIGEST}"
docker compose --profile migration -f "$contract" pull fruit-v4-setup fruit-v4-uat
docker image inspect "$image_ref" \
  --format 'id={{.Id}} repo_digests={{join .RepoDigests ","}}'
```

The inspect result must contain the approved exact digest. Only after the
external migration evidence gate and pull/inspect evidence are independently
accepted may the one-shot setup profile run:

```bash
docker compose --profile migration -f deploy/fruit-v4/docker-compose.yml \
  up --no-build --no-deps --abort-on-container-exit \
  --exit-code-from fruit-v4-setup fruit-v4-setup

docker inspect fruit-v4-isolated-setup \
  --format 'image_ref={{.Config.Image}} image_id={{.Image}} status={{.State.Status}} exit={{.State.ExitCode}}'
```

Default `docker compose up` does not execute setup: setup is behind the
`migration` profile, UAT has no dependency that activates it, and the setup
command requires the explicit one-shot gate. After setup exits successfully,
start only UAT:

```bash
docker compose -f deploy/fruit-v4/docker-compose.yml up -d --no-build fruit-v4-uat
docker compose -f deploy/fruit-v4/docker-compose.yml ps fruit-v4-uat
```

`GET /healthz` must report HTTP 200 and Docker must report `healthy`. There
is no host-port curl path; approved callers on the canonical network use
`http://fruit-v4-isolated-uat:8002/mcp`.

## Digest, inspect, and readiness evidence

Capture these commands after setup completion and UAT readiness. They expose
references, image IDs, labels, network membership, command, and health without
printing environment values:

```bash
docker compose -f deploy/fruit-v4/docker-compose.yml ps

for container in fruit-v4-isolated-setup fruit-v4-isolated-uat; do
  docker inspect "$container" \
    --format 'name={{.Name}} image_ref={{.Config.Image}} image_id={{.Image}} status={{.State.Status}} exit={{.State.ExitCode}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
  docker inspect "$container" --format '{{json .Config.Labels}}' | jq .
done

uat_image_id="$(docker inspect -f '{{.Image}}' fruit-v4-isolated-uat)"
docker image inspect "$uat_image_id" \
  --format 'id={{.Id}} repo_digests={{join .RepoDigests ","}}'
docker inspect fruit-v4-isolated-uat \
  --format '{{json .NetworkSettings.Networks}}' | jq .
```

Both containers must resolve to the same image ID and approved digest. Labels
must independently identify VectA source repository/SHA and
`vecta-infra` deployment repository/revision. Evidence must not print
`.Config.Env`, because setup and UAT contain secret live inputs.

## Stop, remove, and additive-migration rollback

Rollback is isolated to this project. Never use `down --volumes`, `docker
network prune`, broad container/image cleanup, or any command that stops or
restarts V3.

1. Suspend UAT callers or routing, then prevent further append-only UAT facts.
2. Capture digest, labels, health, exit status, and database/audit evidence.
3. Stop and remove UAT first.
4. Stop and remove the exited setup container second.
5. Leave the canonical external network and immutable image untouched.

```bash
docker compose -f deploy/fruit-v4/docker-compose.yml stop fruit-v4-uat
docker compose -f deploy/fruit-v4/docker-compose.yml rm -f fruit-v4-uat
docker compose --profile migration -f deploy/fruit-v4/docker-compose.yml \
  stop fruit-v4-setup
docker compose --profile migration -f deploy/fruit-v4/docker-compose.yml \
  rm -f fruit-v4-setup
```

The setup migration is additive and has no destructive Compose rollback. If
setup has written schema/grant changes but no append-only UAT facts have been
written, an independently approved database rollback may restore the fresh
pre-migration backup after V4 is isolated. Once any append-only UAT fact exists,
do **not** restore over the database: that would overwrite audit history.
Suspend/isolate/stop V4, preserve the facts and evidence, and use a separately
approved forward corrective migration.

A rollback image digest, network, DSN, secret, tenant, employee, backup, restore
rehearsal, or approval value is a live input and must remain outside Git.
