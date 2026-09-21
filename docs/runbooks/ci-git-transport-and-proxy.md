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

GitHub git traffic in the remaining CN-hosted production paths uses one of
these paths:

| Path | Used by | Safeguards that apply |
|---|---|---|
| HTTPS via runner-local squild TLS bridge (`127.0.0.1:3129`) | `vecta`'s `ci.yml` self-hosted jobs and `vecta-infra`'s production image build when the bridge probe succeeds | job-scoped Git config, proxy probe, no proxy credentials; cache-miss clones inherit the proxy |
| HTTPS direct fallback | `vecta-infra` production image build when the local bridge is unavailable | existing transport behavior; no new dependency |
| ssh direct (`github.com:22`) | operator pushes; prod `/data/ocee` fetches | none of the above; ssh keepalives do not detect a throttled-but-alive stream |

GFW intermittently throttles bulk data on long-lived connections while letting
the handshake and small packets through. The local bridge is preferred for
bulk CI transfers; the production image workflow keeps its old direct path as
fallback when the bridge is absent.

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
- `build-mypc-images.yml` starts with an optional `Configure git proxy` step.
  It probes `http://127.0.0.1:3129`; when the bridge answers, the step writes
  a temporary job-scoped `GIT_CONFIG_GLOBAL` through `$GITHUB_ENV`, configures
  Git's HTTP/HTTPS proxy, and exports the same proxy for curl-based GitHub API
  calls. When it does not answer, `GIT_CONFIG_GLOBAL: /dev/null` and the
  existing direct transport remain unchanged. The shared `$HOME/.gitconfig`
  is never touched, and no proxy credential is stored.

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
