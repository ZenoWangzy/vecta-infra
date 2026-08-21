# Fruit V4 Isolated Production Compose Contract

This contract is the `vecta-infra` owner for the isolated Fruit V4 UAT
instance. It does not modify, restart, route, or remove any V3 service. The
Compose project has no published host port and joins only the existing
canonical production Docker network supplied by the operator.

## Owned resources

| Resource | Contract value |
| --- | --- |
| Compose file | `deploy/fruit-v4/docker-compose.yml` |
| Compose project | `fruit-v4-isolated-production` |
| setup service/container | `fruit-v4-setup` / `fruit-v4-isolated-setup` |
| UAT service/container | `fruit-v4-uat` / `fruit-v4-isolated-uat` |
| UAT Docker DNS alias | `fruit-v4-isolated-uat` on the canonical network |
| image input | `${FRUIT_V4_IMAGE_REGISTRY}/fruit-industry-pack@sha256:${FRUIT_V4_IMAGE_DIGEST}` |

Both services consume the same digest-only image anchor. The file contains no
`build`, floating tag, host `ports`, `env_file`, volume, or second network.
The setup command is fixed to `node packages/fruit-industry-pack/dist/db/setup.js`;
the UAT service command is fixed to the exact image's Fruit CLI.

## Required live inputs

The operator supplies these values from the approved secret/configuration
source. They are not committed here and must not be printed in evidence:

- `FRUIT_V4_IMAGE_REGISTRY`: registry host (and optional port) without a path,
  tag, or digest; the contract fixes the repository to `fruit-industry-pack`.
- `FRUIT_V4_IMAGE_DIGEST`: exactly 64 lowercase hexadecimal characters; the
  Compose file supplies the `sha256:` algorithm prefix.
- `FRUIT_V4_SOURCE_SHA`: the full 40-character source SHA represented by the
  image build.
- `FRUIT_V4_CANONICAL_NETWORK`: the pre-existing production Docker network.
- `FRUIT_V4_WRITER_DATABASE_URL`: setup-only migration/writer DSN.
- `FRUIT_V4_RUNTIME_DB_ROLE` and `FRUIT_V4_RUNTIME_DB_PASSWORD`: setup-only
  runtime-role provisioning inputs.
- `FRUIT_V4_RUNTIME_DATABASE_URL`: least-privilege runtime DSN for UAT.
- `FRUIT_V4_SERVICE_SECRET`: UAT service credential.
- `FRUIT_V4_ALLOWED_TENANT_IDS` and `FRUIT_V4_ALLOWED_EMPLOYEE_IDS`: approved
  UAT allowlists; no values are embedded in this contract.

Compose uses `:?` for every required input. A missing DSN, secret, allowlist,
source, digest, or network fails during `docker compose config`. A tag-only
value cannot become an image reference because the contract constructs
`@sha256:<digest>`; the operator must also reject a non-hex digest before pull.

The setup container receives only the writer DSN, runtime-role name, and
runtime-role password needed by `dist/db/setup.js`. The UAT container receives
only the runtime DSN, service secret, and approved allowlists. No shared
`env_file` is allowed, so V3 containers never receive these inputs.

## Read-only preflight and start

Run from the checkout containing this file. Do not run these commands against
the V3 Compose project.

```bash
set -euo pipefail
contract=deploy/fruit-v4/docker-compose.yml

: "${FRUIT_V4_IMAGE_REGISTRY:?required}"
: "${FRUIT_V4_IMAGE_DIGEST:?required}"
: "${FRUIT_V4_SOURCE_SHA:?required}"
: "${FRUIT_V4_CANONICAL_NETWORK:?required}"
: "${FRUIT_V4_WRITER_DATABASE_URL:?required}"
: "${FRUIT_V4_RUNTIME_DB_ROLE:?required}"
: "${FRUIT_V4_RUNTIME_DB_PASSWORD:?required}"
: "${FRUIT_V4_RUNTIME_DATABASE_URL:?required}"
: "${FRUIT_V4_SERVICE_SECRET:?required}"
: "${FRUIT_V4_ALLOWED_TENANT_IDS:?required}"
: "${FRUIT_V4_ALLOWED_EMPLOYEE_IDS:?required}"

case "$FRUIT_V4_IMAGE_REGISTRY" in
  */*|*@*|'') echo 'image registry must not contain a path or digest' >&2; exit 1 ;;
esac
printf '%s' "$FRUIT_V4_IMAGE_DIGEST" | grep -Eq '^[0-9a-f]{64}$'
printf '%s' "$FRUIT_V4_SOURCE_SHA" | grep -Eq '^[0-9a-f]{40}$'
docker network inspect "$FRUIT_V4_CANONICAL_NETWORK" >/dev/null

# --quiet validates interpolation without echoing DSNs or secrets.
docker compose -f "$contract" config --quiet
docker compose -f "$contract" config --images
docker image inspect \
  "${FRUIT_V4_IMAGE_REGISTRY}/fruit-industry-pack@sha256:${FRUIT_V4_IMAGE_DIGEST}" \
  --format '{{.Id}}'
```

The final inspect must show the exact `repo@sha256:<digest>` input. Pull and
start only after the preflight has been independently accepted:

```bash
docker compose -f deploy/fruit-v4/docker-compose.yml pull
docker compose -f deploy/fruit-v4/docker-compose.yml up -d --no-build
```

The dependency condition starts UAT only after setup exits successfully. The
UAT healthcheck is the readiness gate: `GET /healthz` must report HTTP 200 and
Docker must report `healthy`. There is no host-port curl path; callers on the
canonical network use `http://fruit-v4-isolated-uat:8002/mcp`.

## Evidence without secret disclosure

Capture the following after setup completion and UAT readiness. These commands
show references, image IDs, labels, network membership, command, and health;
the environment command redacts values before output.

```bash
docker compose -f deploy/fruit-v4/docker-compose.yml ps

for container in fruit-v4-isolated-setup fruit-v4-isolated-uat; do
  docker inspect "$container" \
    --format 'name={{.Name}} image_ref={{.Config.Image}} image_id={{.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
  docker inspect "$container" \
    --format '{{json .Config.Labels}}' | jq .
  docker inspect "$container" \
    --format '{{range .Config.Env}}{{println .}}{{end}}' |
    sed -E 's/=.*$/=<redacted>/'
done

docker image inspect \
  "$(docker inspect -f '{{.Image}}' fruit-v4-isolated-uat)" \
  --format 'repo_digests={{join .RepoDigests ","}}'
docker inspect fruit-v4-isolated-uat \
  --format '{{json .NetworkSettings.Networks}}' | jq .
```

The labels must identify `vecta-infra`, this contract, the full source SHA,
the repository, and the digest. The setup environment must not contain the
UAT service secret or allowlists; the UAT environment must not contain the
writer DSN or setup-only runtime-role password.

## Stop, remove, and rollback order

Rollback is isolated to this project. Never use `down --volumes`, `docker
network prune`, or a broad container/image cleanup, and never stop or restart
V3 services as part of this sequence.

1. Save the inspect/digest/health evidence for the current UAT container.
2. Stop and remove `fruit-v4-uat` / `fruit-v4-isolated-uat` first.
3. Stop and remove `fruit-v4-setup` / `fruit-v4-isolated-setup` second.
4. Set the previously approved immutable Fruit image digest and matching source
   SHA in the operator-only environment.
5. Repeat the preflight, exact-image pull, setup completion check, and UAT
   readiness check in that order.

The concrete removal commands are:

```bash
docker compose -f deploy/fruit-v4/docker-compose.yml stop fruit-v4-uat
docker compose -f deploy/fruit-v4/docker-compose.yml rm -f fruit-v4-uat
docker compose -f deploy/fruit-v4/docker-compose.yml stop fruit-v4-setup
docker compose -f deploy/fruit-v4/docker-compose.yml rm -f fruit-v4-setup
```

The canonical external network is never removed. A rollback digest, network
name, DSN, secret, tenant, employee, or business fixture is a live input and
must remain outside Git.
