# Entry upstream timing (issue 2279)

Use `roles/open-webui/templates/upstream-timing.conf.j2` as an HTTP-context
nginx snippet. It contains no Jinja variables. On the VPS entry, install it as
`/etc/nginx/conf.d/vecta-upstream-timing.conf`; the existing `http` include
loads it. The Open WebUI proxy template includes the same snippet.

Before applying, back up current files and preserve unrelated live hotfixes.
For a running proxy with an alternate include, append this snippet to that
active HTTP-context config rather than replacing it with the repository
routing template. Run `nginx -t` on each endpoint, then `nginx -s reload`.
Do not recreate containers or run the whole Open WebUI role to arm logs.

`/var/log/nginx/vecta-upstream-timing.log` records epoch time, method, URI
without query, status, total time, upstream address/status/connect/header/
response times, and incoming connection ID/request count. Only the production
VectA host is logged. Existing logs remain enabled. No cookies or auth headers
are recorded. Connection IDs are local to each hop: correlate by URI/time and
TCP tuples, not by assuming IDs match. Server/location access_log overrides
can suppress inherited logs; confirm a real chunk appears at both ends.

Before changing keepalive or retry policy, compare both effective configs and
capture TCP metadata on the WireGuard path. Use fresh authenticated isolated
9223 contexts under the agent-browser lease, disable cache, test 390/1440 widths,
and include idle gaps longer than the proxy's client keepalive timeout.
No reproduced failure means instrumentation is armed, not root cause fixed.

Offline guard: `python3 scripts/test_upstream_timing_contract.py`.

## mypc retention (issue 2326)

Install only `playbooks/mypc-upstream-timing-retention.yml` against the real
`mypc` inventory with `-e mypc_deploy_enabled=true`. It copies the rotator to `/usr/local/sbin/` and installs
`/etc/cron.d/vecta-upstream-timing`; host cron must be running. It never renders
nginx routes or manages container lifecycle.

Every minute, the host script checks the running `openclaw-webui-proxy` log.
At 32 MiB it renames the current log and retains five numbered archives, then
uses `nginx -s reopen`, without reload/restart or `copytruncate` loss.
The default budget is about 192 MiB (current plus five archives), with one
check interval of growth above each threshold. At the observed 0.5 MB/minute,
this covers about six hours. A stopped proxy is skipped; cron resumes on its
next start. Existing archives survive container restarts, but recreating this
container loses its unmounted log directory; copy evidence before any recreate.

For one controlled rotation on mypc:

```bash
sudo /usr/local/sbin/vecta-upstream-timing-rotate --force
```

Check the new file receives real immutable chunk requests, inspect the 390px
chat screenshot, and record config backups/md5 on issue 2173. Logs and browser
evidence stay private. This narrower ~200 MiB budget follows the supervisor's
resume instruction, rather than the automated issue brief's ~896 MiB proposal.

Offline guard: `python3 scripts/test_upstream_timing_retention.py`.
