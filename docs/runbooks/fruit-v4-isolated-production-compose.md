# Fruit V4 Isolated Production Compose Contract

This contract owns one isolated Fruit V4 UAT instance. It does not modify,
restart, route, or remove any V3 service. It publishes no host port and joins
only the operator-supplied canonical production Docker network.

## Files and owned resources

| Resource | Contract value |
| --- | --- |
| UAT Compose file | `deploy/fruit-v4/docker-compose.yml` |
| migration override | `deploy/fruit-v4/docker-compose.migration.yml` |
| image provenance validator | `scripts/validate_fruit_v4_image_provenance.py` |
| Compose project | `fruit-v4-isolated-production` |
| setup service/container | `fruit-v4-setup` / `fruit-v4-isolated-setup` |
| UAT service/container | `fruit-v4-uat` / `fruit-v4-isolated-uat` |
| UAT Docker DNS alias | `fruit-v4-isolated-uat` on the canonical network |
| image input | `${FRUIT_V4_IMAGE_REGISTRY}/fruit-industry-pack@sha256:${FRUIT_V4_IMAGE_DIGEST}` |

The base file contains only UAT. Setup and all setup-only high-authority inputs
exist only in the explicit migration override. Both services consume the same
digest-only image reference and contain no `build`, floating tag, host
`ports`, `env_file`, or second network. UAT mounts nothing. Setup carries
exactly one mount, and only in the migration override: a read-only bind of
`${FRUIT_V4_BACKUP_PATH}` onto the same absolute path inside the container, with
`create_host_path: false`. It exists because the setup preflight verifies the
pre-migration dump by opening it, and a preflight cannot open a file the
container cannot see.

## Image and deployment provenance

`FRUIT_V4_SOURCE_SHA` is the full VectA `main` SHA represented by the image.
`FRUIT_V4_INFRA_REVISION` is the independent full `vecta-infra` deployment
contract revision. Both registry-only and full provenance validation require it
to be a 40-character lowercase SHA equal to `git rev-parse HEAD` in the current
checkout. Validation also fails when any tracked or untracked file belonging to
this contract is dirty.

Compose service labels beginning with
`com.vecta.expected.image.source.*` record operator expectations only. They
are not image provenance and must never be accepted as proof. After the exact
digest is pulled, the validator reads immutable data from `docker image
inspect` and requires all of the following:

- `org.opencontainers.image.source=https://github.com/ZenoWangzy/vecta`;
- `org.opencontainers.image.revision=${FRUIT_V4_SOURCE_SHA}`;
- `RepoDigests` contains the exact
  `${FRUIT_V4_IMAGE_REGISTRY}/fruit-industry-pack@sha256:${FRUIT_V4_IMAGE_DIGEST}`.

Missing or mismatched labels and digest evidence fail closed. The validator
does not fall back to Compose labels, image tags, container labels, or a local
image ID.

The VectA final-image build contract must emit the two OCI labels. If its
current Fruit Dockerfile/build command does not do so, no current image is
eligible for this deployment. The minimum upstream change is:

```dockerfile
ARG VECTA_SOURCE_SHA
LABEL org.opencontainers.image.source="https://github.com/ZenoWangzy/vecta" \
      org.opencontainers.image.revision="$VECTA_SOURCE_SHA"
```

The production build command must pass the independently validated full
`SOURCE_SHA` as `--build-arg VECTA_SOURCE_SHA=${SOURCE_SHA}`, or set the
same immutable labels directly during the final image build. This infra
contract does not synthesize those image labels after build.

## UAT-only inputs

The following inputs are sufficient for base UAT `config` and `up`:

- `FRUIT_V4_IMAGE_REGISTRY`: registry `host[:port]` only. Schemes, paths,
  digests, credentials, whitespace, and invalid/out-of-range ports are rejected.
- `FRUIT_V4_IMAGE_DIGEST`: 64 lowercase hexadecimal characters.
- `FRUIT_V4_SOURCE_SHA`: full 40-character VectA `main` SHA.
- `FRUIT_V4_INFRA_REVISION`: full 40-character `vecta-infra` revision equal to
  the current contract checkout HEAD.
- `FRUIT_V4_CANONICAL_NETWORK`: pre-existing canonical production network.
- `FRUIT_V4_RUNTIME_DATABASE_URL`: least-privilege runtime DSN.
- `FRUIT_V4_WRITER_DATABASE_URL`: controlled-entry writer DSN using a database
  identity distinct from the runtime identity.
- `FRUIT_V4_EXPECTED_DATABASE_HOST`, `FRUIT_V4_EXPECTED_DATABASE_PORT`, and
  `FRUIT_V4_EXPECTED_DATABASE_PATH`: independently approved endpoint shared by
  the runtime and writer DSNs.
- `FRUIT_V4_SERVICE_SECRET`: UAT service credential.
- `FRUIT_V4_ALLOWED_TENANT_IDS` and `FRUIT_V4_ALLOWED_EMPLOYEE_IDS`:
  approved UAT allowlists.

The base Compose file pins the V4 model and enables controlled entry. It
contains no migration-authority DSN, role-provisioning password, backup
checksum, rehearsal ID, operator approval ID, or migration gate. Missing
setup-only values therefore cannot fail or leak into base UAT interpolation.

## Additional migration-only inputs

The migration override additionally requires:

- `FRUIT_V4_MIGRATION_DATABASE_URL`: setup-only migration-authority DSN.
- `FRUIT_V4_RUNTIME_DB_ROLE` and `FRUIT_V4_RUNTIME_DB_PASSWORD`: setup-only
  runtime-role provisioning inputs.
- `FRUIT_V4_WRITER_ROLE` and `FRUIT_V4_WRITER_PASSWORD`: setup-only
  controlled-entry writer-role provisioning inputs.
- `FRUIT_V4_BACKUP_PATH` and `FRUIT_V4_BACKUP_BYTES`: the absolute path of the
  fresh pre-migration custom-format dump and its byte size. The preflight reads
  the file at that path and rejects the migration unless both agree with what is
  on disk; see "Backup evidence the preflight verifies" below.
- `FRUIT_V4_RESTORE_REHEARSAL_ID`: isolated restore and exact-image setup
  rehearsal evidence ID.
- `FRUIT_V4_OPERATOR_APPROVAL_ID`: fresh approval for this exact migration
  execution and its recorded evidence.
- `FRUIT_V4_MIGRATION_GATE=approved-migration`.

When migration is enabled, omission of any one of these values fails during
`docker compose config`.

The base UAT preflight rejects query-bearing DSNs, requires explicit credentials,
requires distinct decoded runtime/writer usernames, and binds both DSNs to the
approved host, port, and path before starting the CLI. The migration preflight
applies the same DSN rules and fails before setup unless:

- the migration, runtime, and writer DSNs use PostgreSQL and explicitly match
  the approved host, port, and path;
- migration, writer, and runtime decoded usernames are non-empty and distinct;
- the decoded runtime username equals `FRUIT_RUNTIME_DB_ROLE`;
- the decoded runtime password is non-empty and equals
  `FRUIT_RUNTIME_DB_PASSWORD`;
- the decoded writer username and password equal `FRUIT_V4_WRITER_ROLE` and
  `FRUIT_V4_WRITER_PASSWORD`;
- source SHA, infra revision, and image digest have their exact required
  lowercase hexadecimal lengths;
- rehearsal ID, approval ID, and the exact migration gate are present;
- the declared backup file passes every check in the next section.

Only after those checks does the image execute
`node packages/fruit-industry-pack/dist/db/setup.js`.

## Backup evidence the preflight verifies

The backup gate answers one question: did somebody actually take a dump before
this migration. It answers it by opening the file, not by accepting a recorded
value. Both declared inputs are checked against the mounted dump, last,
immediately before setup runs:

1. `FRUIT_V4_BACKUP_PATH` must be absolute and must not be `/app` or under it
   (mounting there would shadow the image contents).
2. `FRUIT_V4_BACKUP_BYTES` must be a positive integer.
3. The path must exist and be a regular file. A missing path fails the bind
   mount first (`create_host_path: false`), and the preflight fails naming the
   path if anything else is there.
4. The file must be readable by the image's `node` user (UID 1000). Reading it
   is what proves the operator put a real dump somewhere the gate can see it.
   A root-owned `0600` dump will fail here: give it a mode the container can
   read before the migration window, and do not loosen the directory beyond what
   that requires.
5. Its first five bytes must be the literal `PGDMP` magic of a PostgreSQL
   custom-format dump. This separates a real dump from a truncated, empty, or
   wrong file of the same size, and it needs no external tool.
6. Its declared byte size must equal the size on disk.

Every one of those runs unconditionally, using nothing but the runtime image.
On success the preflight prints, on stdout:

```
Fruit V4 setup preflight verified backup evidence at <path>: <n> bytes, PGDMP custom-format dump
```

Capture that line with the rest of the migration evidence; its absence means the
backup block did not run.

There is deliberately no archive table-of-contents input here. Counting entries
needs a `pg_restore` that `node:20-slim` does not ship, and an input the gate
declares but never compares is exactly the defect this section replaced. The
archive is verified with a real `pg_restore` in the restore rehearsal below,
which proves the dump can be restored — a stronger claim than its entry count.

This replaced an earlier `FRUIT_V4_BACKUP_SHA256` input. That input was never
compared against any file, so it proved only that someone typed 64 hexadecimal
characters. Measured on the pre-change override: a 64-hex value naming a backup
that did not exist produced no preflight refusal at all and went straight on to
run the migration. The checks above are strictly stronger and are exercised by
`scripts/test_fruit_v4_isolated_compose_contract.py`.

## Mandatory external migration evidence gate

Setup is a forward migration and role-provisioning operation. Before every
production execution and before setting
`FRUIT_V4_MIGRATION_GATE=approved-migration`:

1. Quiesce the approved migration window and create a fresh custom-format dump
   using approved backup tooling and a protected destination.
2. Record its absolute path and its byte size (`stat -c %s <file>`) into
   `FRUIT_V4_BACKUP_PATH` and `FRUIT_V4_BACKUP_BYTES`, and make the file readable
   by UID 1000 (`chmod a+r <file>`; confirm with
   `sudo -u '#1000' head -c 5 <file>`, which must print `PGDMP`).
3. Read the archive with a real `pg_restore` and record what it says. The
   production host has no `pg_restore`, so borrow one from an image that has it:

   ```bash
   docker run --rm -v "$DUMP:$DUMP:ro" pgvector/pgvector:pg16 \
     pg_restore -l "$DUMP" | tee /tmp/fruit-v4-backup-toc.txt | wc -l
   ```

   A `pg_restore -l` that fails, or a table-of-contents that is missing the
   `fruit` schema objects this migration touches, stops the window here. This is
   where the archive is verified: the Compose preflight deliberately declares no
   entry-count input, because it could not check one in `node:20-slim`.
4. Restore that exact dump into an isolated non-production database.
5. Against the isolated restore, use the exact approved
   `fruit-industry-pack@sha256` image and execute its exact setup script.
6. Verify the isolated restore and setup result, then record
   `FRUIT_V4_RESTORE_REHEARSAL_ID`.
7. Obtain a fresh separate approval covering this execution's exact VectA SHA,
   image digest,
   backup path/size and recorded table of contents, rehearsal ID, expected
   database endpoint, and infra revision. Record
   `FRUIT_V4_OPERATOR_APPROVAL_ID`.

Compose validates input presence, format, DSN relationships, the migration gate
value, and the declared backup file itself. It still cannot prove that the dump
is *fresh* — the file it opens could be last month's — nor rehearsal completion
or approval validity, and it cannot atomically consume an operator approval.
Those remain independently accepted external hard-gate evidence.

The setup script is an idempotent, repeatable migration and provisioning
command: it applies idempotent migrations and creates or corrects the runtime
role. Repeatability is not authorization. Every production execution requires
a fresh operator approval and a fresh evidence record.
The profile is a Compose service selector only; it is not an authorization
boundary. Compose cannot
prevent an operator who already has Docker access and the writer DSN from
invoking the image or setup script outside this procedure, and it cannot prevent
approval reuse or replay. Those controls remain external operational governance;
this contract deliberately adds no local approval database, lock file, or
workflow engine.

## Exact infra checkout gate

Run every command below from the exact approved `vecta-infra` checkout, with
`FRUIT_V4_INFRA_REVISION` set to that checkout's `git rev-parse HEAD`. The
provenance validator checks both registry-only and full modes. It fails closed
if the revision is malformed or differs from HEAD, or if any tracked or
untracked contract file is dirty. The bounded dirty check covers the Compose
files, this runbook, the provenance validator, the two contract tests, and the
CI workflow that runs them; unrelated repository paths are outside this
deployment contract's checkout check.

On the production host that checkout is the release directory itself. It is a
real `vecta-infra` working tree — it has its own `.git`, `deploy/`, `scripts/`
and `docs/` — and it sits next to the environment file the migration reads:

```
/data/ocee/releases/fruit-v4-gate-a-<id>/
├── .git/                        <- this is the approved checkout
├── deploy/fruit-v4/*.yml
├── scripts/
├── fruit-v4-production.env      <- root:root 0600
└── fruit-v4-production.env.pre-*  <- historical snapshots, never edited
```

Moving the contract forward therefore means moving **both**, in this order.
Editing only the environment file leaves the old Compose file in place and the
migration fails on inputs it no longer has; editing only the checkout leaves the
environment file missing the inputs the new preflight requires.

```bash
set -euo pipefail
R=/data/ocee/releases/fruit-v4-gate-a-<id>
E="$R/fruit-v4-production.env"
DUMP=<absolute path of the fresh pre-migration custom-format dump>

# 1. Move the approved checkout to the merged contract revision.
git -C "$R" fetch origin
git -C "$R" checkout --detach origin/main
git -C "$R" status --porcelain --untracked-files=all \
  -- deploy/fruit-v4 docs/runbooks scripts .github          # must print nothing
git -C "$R" rev-parse HEAD                                  # FRUIT_V4_INFRA_REVISION

# 2. Capture the environment file's before-state.
cp -a "$E" "$E.pre-<change>-$(date -u +%Y%m%dT%H%MZ)"
grep -c '=' "$E"
grep -n '^FRUIT_V4_BACKUP' "$E" | sed -E 's/=.*/=<redacted>/'

# 3. Apply the input change. Retiring FRUIT_V4_BACKUP_SHA256 looked like this:
sed -i '/^FRUIT_V4_BACKUP_SHA256=/d' "$E"
printf 'FRUIT_V4_BACKUP_PATH=%s\nFRUIT_V4_BACKUP_BYTES=%s\n' \
  "$DUMP" "$(stat -c %s "$DUMP")" >> "$E"

# 4. sed -i rebuilds the file, so restore its contract explicitly.
chown root:root "$E"
chmod 600 "$E"

# 5. The dump must be readable by the image's node user.
chmod a+r "$DUMP"
sudo -u '#1000' head -c 5 "$DUMP"                           # must print PGDMP

# 6. Capture the after-state, then re-render before running anything.
grep -c '=' "$E"
grep -n '^FRUIT_V4_BACKUP' "$E"
docker compose --profile migration --env-file "$E" \
  -f "$R/deploy/fruit-v4/docker-compose.yml" \
  -f "$R/deploy/fruit-v4/docker-compose.migration.yml" config --quiet
```

Never edit a `fruit-v4-production.env.pre-*` snapshot; they are the rollback
evidence for earlier windows and still contain retired keys on purpose.

## UAT-only config and start

Run from the exact clean contract checkout described above. Do not include the
migration override for ordinary UAT configuration or lifecycle commands.

```bash
set -euo pipefail
uat_contract=deploy/fruit-v4/docker-compose.yml

: "${FRUIT_V4_IMAGE_REGISTRY:?required}"
: "${FRUIT_V4_IMAGE_DIGEST:?required}"
: "${FRUIT_V4_SOURCE_SHA:?required}"
: "${FRUIT_V4_INFRA_REVISION:?required}"
: "${FRUIT_V4_CANONICAL_NETWORK:?required}"
: "${FRUIT_V4_RUNTIME_DATABASE_URL:?required}"
: "${FRUIT_V4_WRITER_DATABASE_URL:?required}"
: "${FRUIT_V4_EXPECTED_DATABASE_HOST:?required}"
: "${FRUIT_V4_EXPECTED_DATABASE_PORT:?required}"
: "${FRUIT_V4_EXPECTED_DATABASE_PATH:?required}"
: "${FRUIT_V4_SERVICE_SECRET:?required}"
: "${FRUIT_V4_ALLOWED_TENANT_IDS:?required}"
: "${FRUIT_V4_ALLOWED_EMPLOYEE_IDS:?required}"

# Fail before pull if the registry is not host[:port] or release IDs are invalid.
python3 scripts/validate_fruit_v4_image_provenance.py --registry-only
docker network inspect "$FRUIT_V4_CANONICAL_NETWORK" >/dev/null
docker compose -f "$uat_contract" config --quiet

# Pull first, then inspect immutable image provenance and exact RepoDigests.
docker compose -f "$uat_contract" pull fruit-v4-uat
python3 scripts/validate_fruit_v4_image_provenance.py

# Start only after provenance has passed.
docker compose -f "$uat_contract" up -d --no-build fruit-v4-uat
docker compose -f "$uat_contract" ps fruit-v4-uat
```

The health readiness gate is `GET /healthz` returning HTTP 200, the response
reporting `modelVersion=fruit-v4`, and Docker reporting `healthy`. There is no
host-port curl path. Approved callers on the canonical network use
`http://fruit-v4-isolated-uat:8002/mcp`.

## Explicit migration execution

The canonical migration command supplies both Compose files and selects the
setup service with the migration profile:

```bash
set -euo pipefail
uat_contract=deploy/fruit-v4/docker-compose.yml
migration_contract=deploy/fruit-v4/docker-compose.migration.yml

: "${FRUIT_V4_MIGRATION_DATABASE_URL:?required}"
: "${FRUIT_V4_EXPECTED_DATABASE_HOST:?required}"
: "${FRUIT_V4_EXPECTED_DATABASE_PORT:?required}"
: "${FRUIT_V4_EXPECTED_DATABASE_PATH:?required}"
: "${FRUIT_V4_RUNTIME_DB_ROLE:?required}"
: "${FRUIT_V4_RUNTIME_DB_PASSWORD:?required}"
: "${FRUIT_V4_WRITER_ROLE:?required}"
: "${FRUIT_V4_WRITER_PASSWORD:?required}"
: "${FRUIT_V4_BACKUP_PATH:?required}"
: "${FRUIT_V4_BACKUP_BYTES:?required}"
: "${FRUIT_V4_RESTORE_REHEARSAL_ID:?required}"
: "${FRUIT_V4_OPERATOR_APPROVAL_ID:?required}"
: "${FRUIT_V4_MIGRATION_GATE:?required}"

test "$FRUIT_V4_MIGRATION_GATE" = approved-migration
docker compose --profile migration \
  -f "$uat_contract" -f "$migration_contract" config --quiet

# Explicit pull precedes immutable OCI label and RepoDigests inspection.
docker compose --profile migration \
  -f "$uat_contract" -f "$migration_contract" \
  pull fruit-v4-setup fruit-v4-uat
python3 scripts/validate_fruit_v4_image_provenance.py

# Run setup only after all external and image-provenance gates are accepted.
docker compose --profile migration \
  -f "$uat_contract" -f "$migration_contract" \
  up --no-build --no-deps --abort-on-container-exit \
  --exit-code-from fruit-v4-setup fruit-v4-setup

docker inspect fruit-v4-isolated-setup \
  --format 'image_ref={{.Config.Image}} image_id={{.Image}} status={{.State.Status}} exit={{.State.ExitCode}}'
```

Default UAT commands use only the base file, so they expose only the
least-privilege runtime and controlled-entry writer DSNs required by the V4
process. They do not interpolate the migration-authority DSN or role-
provisioning credentials. Supplying the migration override makes those inputs
required, and the in-image preflight rejects a missing or incorrect gate and
missing approval ID. The migration profile remains only the canonical service
selector described above, not a security control.

## Running image and readiness evidence

```bash
image_ref="${FRUIT_V4_IMAGE_REGISTRY}/fruit-industry-pack@sha256:${FRUIT_V4_IMAGE_DIGEST}"
python3 scripts/validate_fruit_v4_image_provenance.py
docker image inspect "$image_ref" \
  --format 'id={{.Id}} source={{index .Config.Labels "org.opencontainers.image.source"}} revision={{index .Config.Labels "org.opencontainers.image.revision"}} repo_digests={{join .RepoDigests ","}}'

for container in fruit-v4-isolated-setup fruit-v4-isolated-uat; do
  docker inspect "$container" \
    --format 'name={{.Name}} image_ref={{.Config.Image}} image_id={{.Image}} status={{.State.Status}} exit={{.State.ExitCode}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
  docker inspect "$container" --format '{{json .Config.Labels}}' | jq .
done

docker inspect fruit-v4-isolated-uat \
  --format '{{json .NetworkSettings.Networks}}' | jq .
```

Setup and UAT must resolve to the same image ID and approved digest. Do not
print `.Config.Env`; both containers contain live secret inputs.

## Stop, remove, and additive-migration rollback

Rollback is isolated to this project. Never use `down --volumes`, `docker
network prune`, broad cleanup, or any command that stops or restarts V3.

1. Suspend UAT callers/routing and prevent further append-only facts.
2. Capture image, labels, health, exit, database, and audit evidence.
3. Stop and remove UAT first.
4. Stop and remove the exited setup container second.
5. Leave the canonical network and immutable image untouched.

```bash
docker compose -f deploy/fruit-v4/docker-compose.yml stop fruit-v4-uat
docker compose -f deploy/fruit-v4/docker-compose.yml rm -f fruit-v4-uat
docker compose --profile migration \
  -f deploy/fruit-v4/docker-compose.yml \
  -f deploy/fruit-v4/docker-compose.migration.yml \
  stop fruit-v4-setup
docker compose --profile migration \
  -f deploy/fruit-v4/docker-compose.yml \
  -f deploy/fruit-v4/docker-compose.migration.yml \
  rm -f fruit-v4-setup
```

If setup has written additive schema/grant changes but no append-only UAT fact
exists, an independently approved database rollback may restore the fresh
pre-migration backup after V4 is isolated. Once any append-only UAT fact exists,
do not restore over the database. Suspend/isolate/stop V4, preserve the facts
and evidence, and use a separately approved forward corrective migration.

Image digest, network, DSNs, secrets, allowlists, backup, rehearsal, and
approval values are live inputs and remain outside Git.
