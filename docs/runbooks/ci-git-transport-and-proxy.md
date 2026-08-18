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
| HTTPS via squid proxy (`geraldsynnas.ddns.net:8888`) | checkout/fetch on the mypc production-build runner | token auth, proxy probe + dead-proxy fallback, `http.lowSpeedLimit/lowSpeedTime` |
| ssh direct (`github.com:22`) | operator pushes; prod `/data/ocee` fetches | none of the above; ssh keepalives do not detect a throttled-but-alive stream |

GFW intermittently throttles bulk data on long-lived port-22 connections while
letting the handshake and small packets through. A transfer on the ssh path can
therefore stall at 0 B/s forever without erroring. The HTTPS+proxy path is the
only one with working stall protection, so **CI fetches must never leave it**.

## Per-host contract

### mypc prod-build runner (`github-runner` user)

- Has **no** `~/.gitconfig` — keep it that way. Proxy and lowSpeed settings are
  injected per job by `build-mypc-images.yml`; nothing persists host-side that
  could rewrite URLs.

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
