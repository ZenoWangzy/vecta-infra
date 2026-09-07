# `openclaw-rag-service` recovery

Ticket 145 (vecta). Full inventory, blast-radius ranking, and the reason this
one jumped the queue -- a postgres superuser password rotation (tickets
151/149) was blocked twice because this container had no rebuild path and
holds one of the 8 real `DATABASE_URL` connections -- are in the vecta repo:
`.scratch/fruit-v41-daily-reconciliation/issues/145-thirteen-containers-have-nothing-that-would-bring-them-back.md`.
This doc only covers the mechanics of using the definition this ticket adds.
Method mirrors `docs/runbooks/fruit-feishu-gateway-recovery.md` (ticket 132);
read that first if this is your first time using this pattern.

## What this is for

Before this ticket, `openclaw-rag-service` had no Compose project, no
systemd unit, no crontab entry -- `RestartPolicy=unless-stopped` only
survives a Docker daemon restart for a container the daemon already knows
about. A `docker rm` or a host reprovision loses it for good. Unlike
`openclaw-fruit-feishu-gateway` or `vecta-fruit-industry-pack-canary`, there
was no self-bootstrapping recreate script at all here -- nothing, not even a
fragile one, would bring this container back.

`deploy/rag-service/docker-compose.yml` is the fix.

## Files

| Resource | Location |
| --- | --- |
| Compose file (tracked) | `deploy/rag-service/docker-compose.yml` |
| Env key template (tracked, no values) | `deploy/rag-service/rag-service.env.example` |
| Real env file (host only, never committed) | `mypc:/data/ocee/deploy/rag-service/rag-service.env` (root:root, `600`) |
| Compose project name | `rag-service` |
| Container name | `openclaw-rag-service` |
| Network | `openclaw-enterprise_openclaw-net` (external -- real network dependency for postgres/fleet-gateway/channel-gateway reachability, no compose lifecycle dependency on the 31-file `-f` chain) |
| Named volume (external, reused not recreated) | `openclaw-enterprise_rag_model_cache` -> `/home/node/.cache/huggingface` |
| Bind mount (host directory, reused not recreated) | `/data/ocee/packages/rag-service/knowledge` -> `/app/knowledge` |

The real env file was produced once, on 2026-09-07, by dumping the then-live
container:

```bash
ssh mypc 'docker inspect openclaw-rag-service \
  --format "{{range .Config.Env}}{{println .}}{{end}}" \
  > /data/ocee/deploy/rag-service/rag-service.env
chmod 600 /data/ocee/deploy/rag-service/rag-service.env'
```

That file is now the source of truth going forward. There is no automation
keeping it in sync with future env changes to this container -- re-run the
dump above (or hand-edit the specific key) after any such change, same
caveat as the feishu-gateway runbook.

## Two things checked before touching anything (do not skip these for the next orphan container)

1. **Is it actually stateless?** `docker inspect --format '{{json .Mounts}}'`
   -- this container has exactly 2 documented mounts (one named volume, one
   bind mount), neither anonymous. Compare against `onlyoffice`
   (`lessons/RULES.md` D26 in the vecta repo): that one looked similarly
   simple from its old Ansible declaration but actually carries 5
   undocumented anonymous volumes with embedded postgres/redis/rabbitmq
   state. Always run the mount check yourself; never trust a name or a
   pre-existing declaration to tell you a container is stateless.
2. **Is a parallel verification instance safe against this specific
   container's connection model?** (Per ticket 132's own explicit caveat:
   this half does not generalize from one service to the next.)
   `openclaw-rag-service` runs a background job worker
   (`packages/rag-service/src/ingestion/job-worker.ts`) that polls the
   shared `knowledge_ingest_jobs` table every 5 seconds starting
   immediately at boot. It uses `FOR UPDATE SKIP LOCKED`, so a second
   worker instance cannot double-process a job -- but it could still claim
   a real pending job and then get torn down mid-processing when the
   verify instance is removed, leaving that job stuck in `running` status
   until the next real restart's stale-job recovery (which only runs once,
   at worker startup, not per tick). The app already exposes
   `RAG_INGEST_WORKER_ENABLED=false` for exactly this kind of situation --
   set it on the verify instance only, never on the real container (its
   whole job is to run that worker). `/healthz` doesn't touch the database
   at all, so this still gets a real, meaningful health check with the risk
   removed.
   Also found live, not predicted: the app *does* run one eager read query
   against the real database at boot regardless of the worker flag --
   `fastify-server.ts`'s embedding-dimension guard (`SELECT ... FROM
   knowledge_chunks LIMIT 1`) -- confirmed read-only by reading the
   source before treating it as acceptable, not assumed safe because it
   looked harmless in the log line.

## Status: live since 2026-09-07 (this ticket, real cutover)

Unlike `fruit-feishu-gateway` (whose real cutover happened opportunistically
during a later token-rotation ticket), this container's real cutover was
done directly as part of this ticket, because unblocking the credential
rotation was the whole point: `docker stop` + `docker rm` the old manually-run
container, `docker compose -p rag-service -f docker-compose.yml up -d`.
`docker inspect` now shows `com.docker.compose.project=rag-service`. Only one
real round-trip was done (not two) -- the "no self-reference" property was
already established by the isolated parallel-instance step below (the real
env was captured to a static file with no runtime dependency on the live
container), so a second forced interruption of the busiest container in this
fleet (highest 24h log volume of the 13 orphans found by ticket 145) would
have added risk without adding proof.

## Recreating the container from nothing

1. **Take the production deploy lock first** (full contract in
   `docs/runbooks/production-deploy-lock.md`):

   ```bash
   ssh mypc 'bash -s -- acquire --holder "<you>" --session "<url>" \
     --containers "openclaw-rag-service"' < scripts/deploy-lock.sh
   ```

2. Confirm the two tracked files above are present on the host at
   `/data/ocee/deploy/rag-service/` alongside the real (non-`.example`) env
   file. Copy `docker-compose.yml` there by hand if it isn't already --
   same as `fruit-feishu-gateway`, this project has no CI job shipping it
   automatically.

3. Bring it up:

   ```bash
   cd /data/ocee/deploy/rag-service
   docker compose -p rag-service -f docker-compose.yml up -d
   ```

4. Verify before trusting it:
   - `docker inspect openclaw-rag-service` shows the expected image,
     network, port mapping, restart policy, and **both mounts** (the named
     volume and the knowledge bind mount -- a missing mount here means
     silently empty caches or an empty knowledge base, not a crash).
   - `curl -sf http://127.0.0.1:8000/healthz` succeeds.
   - The startup log line `"Embedding dimension check passed"` appears
     (its absence, or an `Embedding dimension mismatch` error, means the
     bind-mounted knowledge/embedding config disagrees with what's already
     stored in postgres -- do not proceed past that without understanding
     why).
   - A real retrieval/MCP round-trip actually works -- "the config file
     exists" is not the bar (ticket 145's own criterion, echoing 132's).

5. Release the lock.

## Verification already done (2026-09-07, this ticket)

Proven with a throwaway parallel instance before touching the real
container: same image tag, same env file content (except
`RAG_INGEST_WORKER_ENABLED=false`, see above), different container name
(`openclaw-rag-service-verify-145`), different loopback port
(`127.0.0.1:18000`), knowledge mount remounted read-only as an extra
precaution. `docker compose config` render matched the live container
field-for-field (image, entrypoint, command, working_dir, restart, ports
structure, network, both volumes, env key *set*) via an actual `diff`, not a
visual check. Brought up clean: `/healthz` returned `{"ok":true,"service":
"rag-service"}`, embedding model warmed up, dimension check passed against
the real (read-only) database, no crash, no restart. Torn down cleanly
(`docker compose down`) after confirming no unexpected behavior, verified no
orphaned containers or networks left behind.
