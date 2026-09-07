# CI Git Transport And Proxy Contract

> Status: active only for the mypc production-build runner and production
> checkout. The former integration-runner path is retired.

The image build is manually dispatched from `vecta-infra` main with the full
current VectA main HEAD SHA. The dispatching repository writer is the current
authorization boundary. The `production` environment is an audit label with no
reviewer or protection gate, and the referenced secrets remain repository-level.
The result is independent exact-SHA image-build evidence, not a deployment or
production-health result.

## Buildx host provisioning

The `buildx` Ansible tag downloads Buildx on the controller, verifies the
official checksum, and copies only the plugin to
`/usr/local/lib/docker/cli-plugins/docker-buildx` on mypc. The managed host does
not connect to GitHub, and this tag does not call Docker or touch containers,
images, Nexus, or registry configuration.

Check the isolated tag without applying it:

```bash
uvx --from ansible-core ansible-playbook -i inventories/mypc/hosts.ini \
  playbooks/infra.yml --limit mypc --tags buildx --check \
  -e mypc_deploy_enabled=true
```

After review, apply only that tag:

```bash
uvx --from ansible-core ansible-playbook -i inventories/mypc/hosts.ini \
  playbooks/infra.yml --limit mypc --tags buildx \
  -e mypc_deploy_enabled=true
```

## Transport design

GitHub git traffic in the remaining CN-hosted production paths uses one of two
paths:

| Path | Used by | Safeguards that apply |
|---|---|---|
| HTTPS via squid proxy (`geraldsynnas.ddns.net:8888`) | `vecta`'s `ci.yml` PR-gate and postsubmit checkout on the `mypc-ci` runner. **Not** the production image build — `build-mypc-images.yml` is isolated from this path entirely, see the per-host contract below | token auth, proxy probe + dead-proxy fallback, `http.lowSpeedLimit/lowSpeedTime` |
| ssh direct (`github.com:22`) | operator pushes; prod `/data/ocee` fetches | none of the above; ssh keepalives do not detect a throttled-but-alive stream |

GFW intermittently throttles bulk data on long-lived port-22 connections while
letting the handshake and small packets through. A transfer on the ssh path can
therefore stall at 0 B/s forever without erroring. The HTTPS+proxy path is the
only one with working stall protection, so **CI fetches must never leave it**.

## Per-host contract

### mypc prod-build runner (`github-runner` user)

- `~/.gitconfig` is **not** build-mypc-images.yml's to configure, and reading
  it is not a safe way to learn this runner's proxy state: the OS user
  `github-runner` is shared by three runner services registered on this host
  (`mypc-vecta-infra-prod-build`, its `-2` twin, and `mypc-ci`), so whichever
  of them last wrote to `$HOME/.gitconfig` decides what the file says, not
  which workflow you're reading. It was `vecta`'s `ci.yml` — its
  `pr-touched-packages`/`postsubmit` jobs run on `mypc-ci` and used to
  `git config --global` a proxy probe result and `http.lowSpeedLimit`/`Time`
  straight into that shared file (ticket 136). As of ticket 136, that step
  redirects its own `git config --global` writes to a job-scoped file via
  `GIT_CONFIG_GLOBAL` (propagated to later steps in the same job through
  `$GITHUB_ENV`), so `$HOME/.gitconfig` no longer changes at all from that
  path — verified: writing a poisoned `http.proxy` into a stand-in `$HOME`,
  running the updated step, and diffing that file before/after showed it
  byte-identical.
- `build-mypc-images.yml` itself has never configured a proxy since `18b5a46`
  hardened it, and does not read `$HOME/.gitconfig` either way: its job sets
  `GIT_CONFIG_GLOBAL: /dev/null` and `GIT_CONFIG_SYSTEM: /dev/null` (added in
  `af9b347`, before `18b5a46`, and locked by
  `scripts/test_build_mypc_images_contract.py`'s negative assertions), so
  every `git` invocation in that job goes straight to GitHub regardless of
  what any other job on this host has written to the shared home. That is a
  deliberate choice, not an oversight: mypc's direct GitHub reachability
  tested 3/3 versus 1/3 through the squid proxy (ticket 131), so this
  workflow is better off never touching it.

### mypc prod checkout (`/data/ocee`, root)

- `origin` is intentionally ssh (`git@github.com:ZenoWangzy/vecta.git`) so no
  PAT is stored on the production host. `deploy-prd-local.sh` runs
  `git fetch origin main` on this path, so it carries the same GFW stall risk.
  Verified healthy on 2026-08-07 (full fetch in ~9s).
- If prod fetches start stalling, do NOT store a PAT on the host. Route ssh
  through the squid proxy instead (CONNECT to ssh.github.com:443):

  ```
  # /root/.ssh/config on mypc
  Host github.com
      HostName ssh.github.com
      Port 443
      User git
      ProxyCommand nc -X connect -x geraldsynnas.ddns.net:8888 %h %p
  ```

## Diagnosis quick path

```bash
# 1. What transport is the stuck fetch actually using?
ps -eo pid,etime,time,args | grep -E "git.*(fetch|remote-https|upload-pack)"
#    `git-remote-https ...` -> http path;  `ssh git@github.com git-upload-pack` -> ssh path

# 2. Where does its socket go?
ss -tnp | grep -E "8888|:22"

# 3. Any URL rewrite in play?
git config --global -l | grep -i insteadof

# 4. Bandwidth-level proof the proxy path works (small probe 200s prove nothing):
GIT_CONFIG_GLOBAL=/dev/null git -c http.proxy="$P" -c https.proxy="$P" \
  clone --depth=1 https://github.com/git/git.git /tmp/proxy-bulk-test
```

`timeout-minutes` on every self-hosted job is the last-resort backstop when all
transport-level safeguards are bypassed; a single-runner queue cannot survive a
permanently hung job without it.
