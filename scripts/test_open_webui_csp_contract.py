#!/usr/bin/env python3
"""Static contract for the production Open WebUI CSP override."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "roles/open-webui/templates/nginx.conf.j2"
EXPECTED_CSP = (
    "default-src 'self';script-src 'self' 'unsafe-inline';"
    "style-src 'self' 'unsafe-inline';img-src 'self' data:;"
    "connect-src 'self' data:;font-src 'self';object-src 'none';"
    "frame-ancestors 'none';base-uri 'self';form-action 'self';"
    "script-src-attr 'none';upgrade-insecure-requests"
)
EXPECTED_OVERRIDE = (
    "proxy_hide_header Content-Security-Policy;\n"
    f'        add_header Content-Security-Policy "{EXPECTED_CSP}" always;'
)


def main() -> None:
    content = TEMPLATE.read_text()
    assert content.count(EXPECTED_OVERRIDE) == 2
    assert "connect-src 'self';font-src" not in content
    for prefix in ("/agent/", "/chat/"):
        block = content.split(f"location ^~ {prefix} {{", 1)[1].split("\n    }", 1)[0]
        assert EXPECTED_OVERRIDE in block, prefix
    workflow = (ROOT / ".github/workflows/build-mypc-images.yml").read_text()
    assert "python3 scripts/test_open_webui_csp_contract.py" in workflow
    print("open webui csp contract: ok")


if __name__ == "__main__":
    main()
