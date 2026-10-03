#!/usr/bin/env python3
"""Offline guard for two-end timing instrumentation (issue 2279)."""
from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
snippet = (root / 'roles/open-webui/templates/upstream-timing.conf.j2').read_text()
entry = (root / 'roles/open-webui/templates/nginx.conf.j2').read_text()
for field in ('upstream_addr', 'upstream_status', 'upstream_connect_time',
              'upstream_header_time', 'upstream_response_time',
              'connection', 'connection_requests', 'msec', 'request_time'):
    assert '$' + field in snippet, field
assert "{% include 'upstream-timing.conf.j2' %}" in entry
assert 'escape=json' in snippet
assert 'vecta.matrix-ai.com.cn 1;' in snippet
assert 'if=$vecta_timing_enabled' in snippet
assert set(re.findall(r'\$(\w+)', snippet)) == {
    'host', 'vecta_timing_enabled', 'msec', 'request_method', 'uri', 'status',
    'request_time', 'upstream_addr', 'upstream_status', 'upstream_connect_time',
    'upstream_header_time', 'upstream_response_time', 'connection', 'connection_requests',
}
assert not re.search(r'\b(proxy_|keepalive|sub_filter|location\b)', snippet)
print('PASS: timing fields, host scope, no credentials/query or routing changes')
