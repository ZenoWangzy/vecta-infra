# `openclaw-fruit-feishu-gateway` recovery

Ticket 132. Full inventory and the attribution decision (own Compose
project, not a member of the `openclaw-enterprise` `-f` chain) are in the
vecta repo: `.scratch/fruit-v41-daily-reconciliation/issues/132-nothing-would-bring-the-feishu-gateway-back-after-a-reboot.md`.
This doc only covers the mechanics of using the definition this ticket adds.

## What this is for

Before this ticket, `openclaw-fruit-feishu-gateway` had no Compose project,
no systemd unit, no crontab entry. `unless-stopped` only survives a Docker
daemon restart for a container the daemon already knows about; a `docker rm`
or a host reprovision loses it for good, because the only other thing that
touches it -- `scripts/ops/fruit-account-onboard.sh`'s `recreate_container()`
in the `vecta` repo -- bootstraps by reading *this same running container*,
so it cannot recover from the state where the container is already gone.

`deploy/fruit-feishu-gateway/docker-compose.yml` is the fix: a declarative
definition that does not depend on the container being alive to read.

## Files

| Resource | Location |
| --- | --- |
| Compose file (tracked) | `deploy/fruit-feishu-gateway/docker-compose.yml` |
| Env key template (tracked, no values) | `deploy/fruit-feishu-gateway/fruit-feishu-gateway.env.example` |
| Real env file (host only, never committed) | `mypc:/data/ocee/deploy/fruit-feishu-gateway/fruit-feishu-gateway.env` (root:root, `600`) |
| Compose project name | `fruit-feishu-gateway` |
| Container name | `openclaw-fruit-feishu-gateway` |
| Network | `openclaw-enterprise_openclaw-net` (external, shared with the enterprise stack for postgres/redis/fleet-gateway/rag-service reachability -- see the compose file's comments for why this doesn't mean joining that project) |

The real env file was produced once, on 2026-09-07, by dumping the then-live
container:

```bash
ssh mypc 'docker inspect openclaw-fruit-feishu-gateway \
  --format "{{range .Config.Env}}{{println .}}{{end}}" \
  > /data/ocee/deploy/fruit-feishu-gateway/fruit-feishu-gateway.env
chmod 600 /data/ocee/deploy/fruit-feishu-gateway/fruit-feishu-gateway.env'
```

That file is now the source of truth going forward -- it survives the
container being deleted, unlike reading the live container each time.
**Keep it in sync**: any onboarding-script run (`recreate_container()`) that
changes this container's env makes the file stale. There is no automation
for this yet; re-run the dump above after any such change. (Worth a small
follow-up: have `fruit-account-onboard.sh` refresh this file as part of its
own blue-green swap, instead of leaving it to whoever remembers.)

## Recreating the container from nothing

1. **Take the production deploy lock first** (same mechanism as the gateway
   promotion and fruit-v4 runbooks; full contract in
   `docs/runbooks/production-deploy-lock.md`):

   ```bash
   ssh mypc 'bash -s -- acquire --holder "<you>" --session "<url>" \
     --containers "openclaw-fruit-feishu-gateway"' < scripts/deploy-lock.sh
   ```

2. Confirm the two tracked files above are present on the host at
   `/data/ocee/deploy/fruit-feishu-gateway/` alongside the real
   (non-`.example`) env file. Copy `docker-compose.yml` there if it isn't
   already (this project has no CI job that ships it automatically -- copy
   it by hand, the same way `deploy/fruit-v4/docker-compose.yml` is handled
   for its own isolated project).

3. Bring it up:

   ```bash
   cd /data/ocee/deploy/fruit-feishu-gateway
   docker compose -p fruit-feishu-gateway -f docker-compose.yml up -d
   ```

4. Verify before trusting it:
   - `docker inspect openclaw-fruit-feishu-gateway` shows the expected
     image, network, port mapping, and restart policy.
   - `curl -sf http://127.0.0.1:18003/healthz` succeeds.
   - A real or equivalent Feishu smoke path actually round-trips -- "the
     config file exists" is not the bar (ticket 132's own criterion).

5. Release the lock.

## Verification already done (2026-09-07, this ticket)

The above was proven with a throwaway parallel instance, not against the
real container: same image tag, same env file content, different container
name (`fruit-feishu-gateway-verify-132`), different loopback port, same
external network, on a `docker compose -p fruit-feishu-gateway-verify-132`
project -- brought up, health-checked, config-diffed field by field against
`docker inspect openclaw-fruit-feishu-gateway`, then `docker compose down`
and removed. The production container was never stopped, recreated, or
otherwise touched. See the ticket file for the full diff record.

## Known gap: the pinned tag is already behind

`openclaw-channel-gateway` (same `channel-gateway` image, different
container) is already running `f4195d3e9cd5129413638452ef1667b8c8c6c55e`;
this file pins `5d9a930b981cc47fcbfa2ed58a8a1e83afdfefe4`, which is what
`openclaw-fruit-feishu-gateway` is *actually* running today. That's
deliberate ordering, not an oversight: this ticket's job was proving a safe
recreate path first, because the old failure mode (self-bootstrapping off
the live container) meant a botched attempt to "fix" this at the same time
would have been unrecoverable. Advancing the pinned tag to match
`openclaw-channel-gateway` is real production traffic risk (it changes what
the live Feishu channel runs against, with confirmed non-test usage) and
belongs in its own follow-up now that this path exists to fall back to.
That follow-up should also update `production-deploy-lock.md` to require
the lock for that specific promotion, and confirm the newer image is
compatible with this container's current 50-key env before switching (the
parallel-instance mechanism above is the way to check that without touching
production).

Ticket 147 (rotating `A2A_ROUTER_TOKEN`, three holders) is blocked on this
recovery path existing, not on the tag advance -- it can proceed once this
file lands.
