# CI Git Transport And Proxy Contract

> Origin: 2026-08-07 incident — vtest CI checkout wedged indefinitely because a
> runner-level `insteadOf` rewrite silently switched fetches to direct ssh.
> Full incident write-up: `vecta/lessons/full/ci-github-actions.md`
> (insteadof-hijacks-checkout).

## Transport design

All GitHub git traffic from CN-hosted runners uses one of two paths:

| Path | Used by | Safeguards that apply |
|---|---|---|
| HTTPS via squid proxy (`geraldsynnas.ddns.net:8888`) | CI checkout/fetch on vtest and mypc runners | token auth, proxy probe + dead-proxy fallback, `http.lowSpeedLimit/lowSpeedTime` |
| ssh direct (`github.com:22`) | operator pushes; prod `/data/ocee` fetches | none of the above; ssh keepalives do not detect a throttled-but-alive stream |

GFW intermittently throttles bulk data on long-lived port-22 connections while
letting the handshake and small packets through. A transfer on the ssh path can
therefore stall at 0 B/s forever without erroring. The HTTPS+proxy path is the
only one with working stall protection, so **CI fetches must never leave it**.

## Per-host contract

### vtest runner (`ubuntu` user)

- Global gitconfig is shared between CI and interactive use and is part of the
  CI contract. Fetch stays HTTPS; push-over-ssh is expressed only as:
  `url.git@github.com:ZenoWangzy/vecta.pushInsteadOf=https://github.com/ZenoWangzy/vecta`
- Never add a plain `insteadOf` for a GitHub https URL — it rewrites checkout
  to ssh and bypasses proxy, token, and every `http.*` safeguard at once.
- `http.lowSpeedLimit=1024` / `http.lowSpeedTime=30` stay set globally and are
  also injected by every `Configure git proxy` workflow step.

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
