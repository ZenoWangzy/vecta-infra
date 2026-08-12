#!/usr/bin/env python3
"""Fail-closed claim preflight for the vtest shared-pool service JWTs.

The application verifies Ed25519 signatures. Infra has no signing key and does
not mint or persist tokens; this gate only checks the claims that must include
the fixed fixture. It intentionally never prints a token or decoded payload.
"""

import base64
import json
import os
import sys
import time
from typing import NoReturn


FIXTURE_TENANT = "vtest-shared-pool"


def fail(reason: str) -> NoReturn:
    print(f"SHARED_POOL_PLATFORM_TOKEN_SCOPE_INVALID:{reason}", file=sys.stderr)
    raise SystemExit(1)


def decode_json(segment: str) -> dict:
    if not segment or len(segment) > 65536:
        fail("malformed_jwt_segment")
    try:
        padding = "=" * (-len(segment) % 4)
        raw = base64.b64decode(segment + padding, altchars=b"-_", validate=True)
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        fail("malformed_jwt_json")
    if not isinstance(value, dict):
        fail("jwt_claims_not_object")
    return value


def check_token(name: str, audience: str, scope: str) -> None:
    token = os.environ.get(name, "").strip()
    if not token:
        fail(f"{name.lower()}_missing")
    parts = token.split(".")
    if len(parts) != 3:
        fail(f"{name.lower()}_shape")
    header = decode_json(parts[0])
    payload = decode_json(parts[1])
    if header.get("alg") != "EdDSA":
        fail(f"{name.lower()}_algorithm")
    if payload.get("iss") != "vecta" or payload.get("aud") != audience:
        fail(f"{name.lower()}_issuer_or_audience")
    if not isinstance(payload.get("sub"), str) or not payload["sub"].strip():
        fail(f"{name.lower()}_subject")
    scopes = payload.get("scope")
    if not isinstance(scopes, list) or scope not in scopes:
        fail(f"{name.lower()}_scope")
    tenant_ids = []
    if isinstance(payload.get("tid"), str):
        tenant_ids.append(payload["tid"])
    if isinstance(payload.get("tenant_ids"), list):
        tenant_ids.extend(value for value in payload["tenant_ids"] if isinstance(value, str))
    if FIXTURE_TENANT not in {tenant_id.strip() for tenant_id in tenant_ids}:
        fail(f"{name.lower()}_tenant_scope")
    issued_at = payload.get("iat")
    expires_at = payload.get("exp")
    now = int(time.time())
    if not isinstance(issued_at, (int, float)) or not isinstance(expires_at, (int, float)):
        fail(f"{name.lower()}_time_claims")
    if expires_at <= now or expires_at <= issued_at or issued_at > now + 60:
        fail(f"{name.lower()}_expired_or_future")


def main() -> None:
    public_key = os.environ.get("VTEST_PLATFORM_SERVICE_JWT_PUBLIC_KEY", "").strip()
    if "BEGIN PUBLIC KEY" not in public_key:
        fail("platform_public_key_missing")
    check_token("VTEST_CHANNEL_PLATFORM_SERVICE_TOKEN", "channel-gateway", "channel:internal")
    check_token("VTEST_FLEET_PLATFORM_SERVICE_TOKEN", "fleet-gateway", "fleet:internal")
    print("SHARED_POOL_PLATFORM_TOKEN_SCOPE_OK")


if __name__ == "__main__":
    main()
