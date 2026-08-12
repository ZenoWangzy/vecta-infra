#!/usr/bin/env python3
"""Static contract for the vtest-only shared-pool fixture gate."""

from pathlib import Path
import base64
import json
import os
import re
import subprocess
import sys
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = (ROOT / "inventories/vtest/group_vars/vtest.yml").read_text()
MYPC = (ROOT / "inventories/mypc/group_vars/mypc.yml").read_text()
MYCP = (ROOT / "inventories/mycp/group_vars/mycp.yml").read_text()
FIXTURE = (ROOT / "roles/deploy-vtest/tasks/shared_pool_fixture.yml").read_text()
MAIN = (ROOT / "roles/deploy-vtest/tasks/main.yml").read_text()
PREFLIGHT = (ROOT / "roles/deploy-vtest/tasks/preflight.yml").read_text()
A2A = (ROOT / "roles/vecta-app/tasks/a2a_router.yml").read_text()
FLEET = (ROOT / "roles/vecta-app/tasks/fleet_gateway.yml").read_text()
WORKFLOW = (ROOT / ".github/workflows/_deploy-vtest-job.yml").read_text()
TOKEN = (ROOT / "scripts/verify-vtest-platform-token.py").read_text()


class VtestSharedPoolFixtureContractTest(unittest.TestCase):
    def test_fixture_is_fixed_and_vtest_only(self) -> None:
        self.assertIn('shared_pool_vtest_fixture_enabled: true', INVENTORY)
        self.assertIn('shared_pool_vtest_fixture_tenant_id: "vtest-shared-pool"', INVENTORY)
        self.assertNotIn("shared_pool_vtest_fixture", MYPC)
        self.assertNotIn("shared_pool_vtest_fixture", MYCP)
        self.assertRegex(MAIN, r"migrate\.yml[\s\S]+shared_pool_fixture\.yml[\s\S]+deploy\.yml")
        self.assertIn("'vtest' in group_names", MAIN)
        self.assertRegex(MAIN, r"quiesce\.yml[\s\S]+shared_pool_vtest_fixture_enabled[\s\S]+backup\.yml")

    def test_fixture_is_transactional_scoped_and_audited(self) -> None:
        for marker in (
            "BEGIN;",
            "COMMIT;",
            "-v ON_ERROR_STOP=1",
            "pg_advisory_xact_lock",
            "vtest-shared-pool",
            "vtest_shared_pool_fixture",
            "tier = 'free'",
            "public.audit_log",
            "no_log: true",
        ):
            self.assertIn(marker, FIXTURE)
        self.assertNotRegex(FIXTURE, r"\b(?:DELETE|TRUNCATE|DROP)\b")
        self.assertIn("refusing to repurpose an unmarked tenant", FIXTURE)

    def test_token_preflight_is_claim_scoped_and_secret_safe(self) -> None:
        self.assertIn("verify-vtest-platform-token.py", PREFLIGHT)
        self.assertIn("no_log: true", PREFLIGHT)
        for name in (
            "VTEST_PLATFORM_SERVICE_JWT_PUBLIC_KEY",
            "VTEST_CHANNEL_PLATFORM_SERVICE_TOKEN",
            "VTEST_FLEET_PLATFORM_SERVICE_TOKEN",
        ):
            self.assertIn(name, WORKFLOW)
            self.assertIn(name, TOKEN)
        for claim in ("channel-gateway", "fleet-gateway", "channel:internal", "fleet:internal", "vtest-shared-pool"):
            self.assertIn(claim, TOKEN)
        self.assertNotIn("print(token", TOKEN)
        self.assertNotIn("print(payload", TOKEN)

    def test_platform_secrets_are_hidden_in_container_tasks(self) -> None:
        self.assertTrue(A2A.rstrip().endswith("no_log: true"))
        self.assertTrue(FLEET.rstrip().endswith("no_log: true"))

    def test_token_preflight_accepts_scoped_tokens_and_rejects_bad_scope(self) -> None:
        def segment(value: object) -> str:
            return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode().rstrip("=")

        def token(audience: str, scope: str, tenants: list[str]) -> str:
            now = int(time.time())
            return ".".join((
                segment({"alg": "EdDSA", "typ": "JWT"}),
                segment({
                    "iss": "vecta",
                    "aud": audience,
                    "sub": "contract-test",
                    "scope": [scope],
                    "tid": tenants[0],
                    "tenant_ids": tenants[1:],
                    "iat": now,
                    "exp": now + 300,
                }),
                "contract-signature",
            ))

        env = os.environ.copy()
        env.update({
            "VTEST_PLATFORM_SERVICE_JWT_PUBLIC_KEY": "-----BEGIN PUBLIC KEY-----\ncontract\n-----END PUBLIC KEY-----",
            "VTEST_CHANNEL_PLATFORM_SERVICE_TOKEN": token("channel-gateway", "channel:internal", ["vtest-shared-pool"]),
            "VTEST_FLEET_PLATFORM_SERVICE_TOKEN": token("fleet-gateway", "fleet:internal", ["vtest-shared-pool"]),
        })
        script = ROOT / "scripts/verify-vtest-platform-token.py"
        accepted = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, check=False)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(accepted.stdout.strip(), "SHARED_POOL_PLATFORM_TOKEN_SCOPE_OK")

        env["VTEST_FLEET_PLATFORM_SERVICE_TOKEN"] = token("fleet-gateway", "other", ["vtest-shared-pool"])
        rejected = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn(env["VTEST_FLEET_PLATFORM_SERVICE_TOKEN"], rejected.stdout + rejected.stderr)


if __name__ == "__main__":
    unittest.main()
