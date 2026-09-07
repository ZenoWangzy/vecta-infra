# `openclaw-wecom-contact-sync` recovery

Ticket 145 (vecta), third service in this pattern:
`openclaw-fruit-feishu-gateway` (ticket 132) -> `openclaw-rag-service`
(ticket 145) -> this one. Full inventory and the reason this jumped the
queue -- a postgres superuser password rotation blocked a third time,
because this was the last of 10 real `DATABASE_URL` holders without a
resolvable compose definition -- are in the vecta repo:
`.scratch/fruit-v41-daily-reconciliation/issues/145-thirteen-containers-have-nothing-that-would-bring-them-back.md`.
This doc only covers the mechanics; read
`docs/runbooks/rag-service-recovery.md` first if this is your first time
using this pattern.

## What this is for

Before this ticket, `openclaw-wecom-contact-sync` had no compose project, no
systemd unit, no crontab entry, and -- unlike `openclaw-rag-service` or
`openclaw-fruit-feishu-gateway` -- no self-bootstrapping recreate script of
any kind either. `docker compose config --format json` could not resolve
this service from any file on the host.

**Correction to how this gap was first reported**: it is not true that the
only remaining trace of this container name is a stale Ansible `group_var`
in an old release checkout. `roles/vecta-app/tasks/wechat_contact_sync_mypc.yml`
exists on current `main` and is imported by `roles/vecta-app/tasks/main.yml`.
But `grep -rn "role: vecta-app" playbooks/*.yml` is empty -- like
`roles/open-webui` (ticket 145 wave 2), this role has no playbook wiring it
up at all, so the task is unreachable regardless. And even if it were wired
up, the task is self-referential by design (it `docker inspect`s the
*currently running* container to source `command`/`entrypoint`/`env` for a
Nexus image swap) -- same flaw already flagged for `rag_service_mypc.yml`.
It is left untouched here, not deleted: it isn't factually wrong (unlike the
`open-webui` role's tasks, which declared network memberships that
disagreed with the live containers), just narrower-purpose -- a
migration/image-swap tool, not a disaster-recovery one. Same follow-up
ticket as `rag_service_mypc.yml`'s equivalent gap.

## Files

| Resource | Location |
| --- | --- |
| Compose file (tracked) | `deploy/wecom-contact-sync/docker-compose.yml` |
| Env key template (tracked, no values) | `deploy/wecom-contact-sync/wecom-contact-sync.env.example` |
| Real env file (host only, never committed) | `mypc:/data/ocee/deploy/wecom-contact-sync/wecom-contact-sync.env` (root:root, `600`) |
| Compose project name | `wecom-contact-sync` |
| Container name | `openclaw-wecom-contact-sync` |
| Network | `openclaw-enterprise_openclaw-net` (external -- reachability for postgres/fleet-gateway/channel-gateway, no compose lifecycle dependency) |
| Mounts | none -- this container is fully stateless (confirmed via `docker inspect --format '{{json .Mounts}}'`, not assumed from its Ansible declaration; see "Two things checked" below) |

## A compose interpolation bug caught by the mandatory pre-execution render (do not skip this step)

The container's command is a shell loop:

```sh
while true; do
  node packages/channel-gateway/dist/wecom-contact-sync.js
  sleep "${WECOM_CONTACT_SYNC_INTERVAL_SECONDS:-86400}"
done
```

Writing `${WECOM_CONTACT_SYNC_INTERVAL_SECONDS:-86400}` directly into a
Compose YAML file is wrong: Docker Compose performs its own `${VAR:-default}`
interpolation on the file's contents *before* anything reaches the
container, using Compose's own environment (the invoking shell / a `.env`
file in the project directory) -- **not** the `env_file:` entries, which are
injected into the container's environment only. Since
`WECOM_CONTACT_SYNC_INTERVAL_SECONDS` isn't set in Compose's own
interpolation context, Compose silently substitutes the literal default
`86400` at file-parse time and bakes it into the command forever, regardless
of what value the real env file (or a future edit to it) actually sets. This
was caught by `docker compose config` rendering `sleep "86400"` instead of
the intended runtime-expanded form, during the mandatory field-for-field
render-and-diff step this whole pattern requires before touching anything
live -- not found by inspection, found by actually running the render.

Fix: escape as `$$` in the compose file (`sleep "$${WECOM_CONTACT_SYNC_INTERVAL_SECONDS:-86400}"`).
Compose turns `$$` into a literal single `$` in what the container actually
receives, so the container's own `sh` expands it at runtime against its real
environment, honoring `env_file:` as intended. Verified against the live
container's actual `Config.Cmd` after bringing the verify instance up
(showed the correctly single-`$` runtime command), not just against
Compose's own re-rendered YAML (whose own `config` output still displays the
escaped `$$` form, which reads as suspicious until you check what Docker
itself received).

## Two things checked before touching anything (do not skip these for the next orphan container)

1. **Is it actually stateless?** `docker inspect --format '{{json .Mounts}}'`
   returns an empty array. No named volumes, no anonymous volumes (compare
   against `onlyoffice`, `lessons/RULES.md` D26 in the vecta repo, which
   looked simple from its old declaration but actually carries 5
   undocumented anonymous volumes). This is the simplest of the three
   services fixed under this pattern so far.
2. **Is a parallel verification instance safe against this specific
   container's connection model?** Not by default. The container runs a
   shell loop that calls `wecom-contact-sync.js` immediately at boot
   (before any sleep), which unconditionally calls
   `fetchWeComDirectoryUsers()` -- a real call to WeCom's contact-directory
   API -- *before* the script's own `--dry-run` flag is even checked.
   `--dry-run` only gates the database `INSERT`/`UPDATE`/`COMMIT` calls
   inside `syncWeComDirectoryUsers`; it does not stop the real external API
   call. The actual off switch the app already exposes:
   `fetchWeComDirectoryUsers()` checks `if (!corpId || !contactsSecret)`
   *first* and returns `null` (logging `"未配置，跳过同步"`) without ever
   reaching the WeCom API or opening a database connection. Blanking
   `WECOM_CORP_ID` and `WECOM_CONTACTS_SECRET` for the verify instance only
   exercises that exact, already-written code path -- never done on the
   real container's declaration, whose whole job is to actually reach
   WeCom and write real contacts.

## An unrelated, pre-existing bug this recreation surfaced (not caused)

Both real production cutovers (see below) logged `[WeComContactSync] 同步失败:
Error: [WeComContacts] Token error: invalid corpid` on their first sync
attempt after startup. Checked, not assumed, that this predates this
ticket's changes:

- The exported env file has exactly 31 real `KEY=VALUE` entries plus one
  trailing blank line from the export command's own `println` -- confirmed
  with the same embedded-newline check `docs/runbooks/fruit-feishu-gateway-recovery.md`
  used (`docker inspect --format '{{json .Config.Env}}'` piped through a
  check for `"\n" in entry`), which found zero. If the capture had truncated
  a multi-line value, this would show up here; it didn't.
- `openclaw-channel-gateway`'s `WECOM_CORP_ID` and `openclaw-wecom-contact-sync`'s
  `WECOM_CORP_ID` are different values (compared with `diff` on the isolated
  key=value lines, values never printed) -- WeCom's corp ID is one value per
  enterprise, shared across every app in that enterprise, so two different
  values for the same key across two services in the same stack is a real
  drift, not an intentional difference.

Both round-trips reproduced the identical failure deterministically -- this
is the *same* pre-existing credential problem being faithfully reproduced by
an accurate recreation, not a new one introduced by it. Recommend a
follow-up: reconcile `openclaw-wecom-contact-sync`'s `WECOM_CORP_ID` against
`openclaw-channel-gateway`'s (or whichever is authoritative) in the real env
file at `/data/ocee/deploy/wecom-contact-sync/wecom-contact-sync.env`. Not
attempted here -- this ticket's job was making the container recoverable,
not diagnosing which of two live values is correct.

## Status: live since 2026-09-07 (this ticket, real cutover, both round-trips)

`docker stop` + `docker rm` the old manually-run container, then
`docker compose -p wecom-contact-sync -f docker-compose.yml up -d`.
`docker inspect` now shows `com.docker.compose.project=wecom-contact-sync`.
Unlike `openclaw-rag-service` (one round-trip only, justified by continuous
live MCP traffic), this container has no published port and effectively no
live traffic between its 24-hour sync attempts, so both round-trips were
done: a normal cutover, then a full `docker rm -f` and recreate purely from
the compose declaration with zero dependency on a running container to read
from. Both produced a new container ID and the correct restart policy,
network, and command (including the runtime-expanded sleep interval).

## Recreating the container from nothing

1. **Take the production deploy lock first** (full contract in
   `docs/runbooks/production-deploy-lock.md`):

   ```bash
   ssh mypc 'bash -s -- acquire --holder "<you>" --session "<url>" \
     --containers "openclaw-wecom-contact-sync"' < scripts/deploy-lock.sh
   ```

2. Confirm the two tracked files above are present on the host at
   `/data/ocee/deploy/wecom-contact-sync/` alongside the real (non-`.example`)
   env file. Copy `docker-compose.yml` there by hand if it isn't already.

3. Bring it up:

   ```bash
   cd /data/ocee/deploy/wecom-contact-sync
   docker compose -p wecom-contact-sync -f docker-compose.yml up -d
   ```

4. Verify before trusting it:
   - `docker inspect openclaw-wecom-contact-sync` shows the expected image,
     network, restart policy, and a `Config.Cmd` with a single-`$`
     `${WECOM_CONTACT_SYNC_INTERVAL_SECONDS:-86400}` (not a literal `86400`
     baked in -- if it's baked in, the `$$` escaping in the compose file was
     lost, re-check it).
   - `docker logs openclaw-wecom-contact-sync` shows either a successful
     sync report (JSON with `inserted`/`updated`/`softDisabled`/`skipped`/
     `errors`) or, if the `WECOM_CORP_ID` drift above hasn't been fixed yet,
     the same `invalid corpid` error every 24h -- not a crash loop, not
     silence.

5. Release the lock.

## Verification already done (2026-09-07, this ticket)

Proven with a throwaway parallel instance before touching the real
container: same image tag, same env file content except
`WECOM_CORP_ID`/`WECOM_CONTACTS_SECRET` blanked (see above), different
container name (`openclaw-wecom-contact-sync-verify-145`), no published
port either way so no port to change. `docker compose config` render
matched the live container field-for-field (image, entrypoint, command
structure, working_dir, user, restart, network, env key *set*) via an
actual `diff`, not a visual check. Brought up clean: logged the expected
`"WECOM_CORP_ID/WECOM_CONTACTS_SECRET 未配置，跳过同步"` line, no crash, no
restart, entered its sleep. Torn down cleanly (`docker compose down`)
immediately after, confirmed no orphaned containers or networks left
behind.
