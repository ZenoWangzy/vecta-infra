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
