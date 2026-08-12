#!/usr/bin/env python3
"""Contract and executable smoke tests for the vtest shared-pool fixture gate."""

from pathlib import Path
import json
import os
import subprocess
import unittest
import base64


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = (ROOT / "inventories/vtest/group_vars/vtest.yml").read_text()
MYPC = (ROOT / "inventories/mypc/group_vars/mypc.yml").read_text()
MYCP = (ROOT / "inventories/mycp/group_vars/mycp.yml").read_text()
FIXTURE = (ROOT / "roles/deploy-vtest/tasks/shared_pool_fixture.yml").read_text()
MAIN = (ROOT / "roles/deploy-vtest/tasks/main.yml").read_text()
PREFLIGHT = (ROOT / "roles/deploy-vtest/tasks/preflight.yml").read_text()
BACKUP = (ROOT / "roles/deploy-vtest/tasks/backup_shared_pool_bundle.yml").read_text()
PRE_SHARED = (ROOT / "roles/deploy-vtest/tasks/preflight_shared_pool_bundle.yml").read_text()
INSPECT = (ROOT / "roles/deploy-vtest/tasks/inspect_quiesce.yml").read_text()
ROLLBACK = (ROOT / "roles/deploy-vtest/tasks/rollback_shared_pool_bundle.yml").read_text()
CLEANUP = (ROOT / "roles/deploy-vtest/tasks/cleanup_shared_pool_bundle.yml").read_text()
RESTART_PROBE = (ROOT / "roles/deploy-vtest/tasks/shared_pool_restart_probe.yml").read_text()
A2A = (ROOT / "roles/vecta-app/tasks/a2a_router.yml").read_text()
FLEET = (ROOT / "roles/vecta-app/tasks/fleet_gateway.yml").read_text()
MYPC_FLEET = (ROOT / "roles/vecta-app/tasks/fleet_gateway_mypc.yml").read_text()
CHANNEL = (ROOT / "roles/vecta-app/tasks/channel_gateway.yml").read_text()
FRUIT = (ROOT / "roles/fruit_vtest/tasks/main.yml").read_text()
WECHAT = (ROOT / "roles/vecta-app/tasks/wechat_contact_sync.yml").read_text()
WORKFLOW = (ROOT / ".github/workflows/_deploy-vtest-job.yml").read_text()
MINT = (ROOT / "scripts/mint-vtest-platform-bundle.mjs").read_text()
VERIFY = (ROOT / "scripts/verify-vtest-platform-bundle.mjs").read_text()
MINT_PATH = ROOT / "scripts/mint-vtest-platform-bundle.mjs"
VERIFY_PATH = ROOT / "scripts/verify-vtest-platform-bundle.mjs"


def mint_bundle(input_text: str = "tenant-b\ntenant-a\n") -> dict[str, str]:
    result = subprocess.run(
        ["node", str(MINT_PATH)],
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    fields: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in fields:
            raise AssertionError("mint output shape changed")
        fields[key] = value
    return fields


def verify_bundle(fields: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "VTEST_PLATFORM_SERVICE_JWT_PUBLIC_KEY": fields["PUBLIC_KEY_ESCAPED"],
            "VTEST_CHANNEL_PLATFORM_SERVICE_TOKEN": fields["CHANNEL_PLATFORM_SERVICE_TOKEN"],
            "VTEST_FLEET_PLATFORM_SERVICE_TOKEN": fields["FLEET_PLATFORM_SERVICE_TOKEN"],
            "VTEST_FLEET_FRUIT_PLATFORM_TOKEN": fields["FLEET_FRUIT_PLATFORM_TOKEN"],
            "VTEST_EXPECTED_TENANT_IDS_JSON": fields["TENANT_IDS_JSON"],
        }
    )
    return subprocess.run(["node", str(VERIFY_PATH)], env=env, capture_output=True, text=True, check=False)


def token_payload(token: str) -> dict[str, object]:
    segment = token.split('.')[1]
    padded = segment + '=' * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(padded).decode())


def signed_variant(case: str) -> dict[str, str]:
    """Create a signed JWT with one deliberately malformed claim for verifier tests."""
    node_source = r'''
import { generateKeyPairSync, sign } from 'node:crypto';
const b64 = (value) => Buffer.from(value).toString('base64url');
const tenants = ['tenant-a', 'tenant-b', 'vtest-shared-pool'];
const now = Math.floor(Date.now() / 1000);
const { privateKey, publicKey } = generateKeyPairSync('ed25519');
const header = { alg: process.env.CASE === 'alg' ? 'none' : 'EdDSA', typ: 'JWT' };
const pem = publicKey.export({ type: 'spki', format: 'pem' }).toString().trim().replace(/\n/g, '\\n');
function makeToken(aud, scopes) {
  const payload = { iss: 'vecta', aud, sub: 'contract-test', scope: scopes, tid: tenants[0], tenant_ids: tenants.slice(1), iat: now, exp: now + 300 };
  if (process.env.CASE === 'bool') payload.iat = true;
  if (process.env.CASE === 'aud') payload.aud = 'wrong-audience';
  if (process.env.CASE === 'scope') payload.scope = ['wrong-scope'];
  if (process.env.CASE === 'tid') payload.tid = false;
  if (process.env.CASE === 'tenant_ids') payload.tenant_ids = 'tenant-b';
  if (process.env.CASE === 'exp') payload.exp = 'later';
  if (process.env.CASE === 'iat') payload.iat = 1.5;
  const payloadJson = process.env.CASE === 'nan' ? JSON.stringify(payload).replace(String(now), 'NaN') : JSON.stringify(payload);
  const input = `${b64(JSON.stringify(header))}.${b64(payloadJson)}`;
  return `${input}.${sign(null, Buffer.from(input), privateKey).toString('base64url')}`;
}
const fruitScopes = process.env.CASE === 'fruit-generic' ? ['fleet:internal'] : ['fleet:internal', 'fleet:scenario-pack-publish'];
for (const [key, token] of [['CHANNEL_PLATFORM_SERVICE_TOKEN', makeToken('channel-gateway', ['channel:internal'])], ['FLEET_PLATFORM_SERVICE_TOKEN', makeToken('fleet-gateway', ['fleet:internal'])], ['FLEET_FRUIT_PLATFORM_TOKEN', makeToken('fleet-gateway', fruitScopes)]]) console.log(`${key}=${token}`);
console.log(`PUBLIC_KEY_ESCAPED=${pem}`);
console.log(`TENANT_IDS_JSON=${JSON.stringify(tenants)}`);
'''
    env = os.environ.copy()
    env["CASE"] = case
    result = subprocess.run(["node", "--input-type=module", "-e", node_source], env=env, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return {key: value for line in result.stdout.splitlines() for key, _, value in [line.partition("=")]}


class VtestSharedPoolFixtureContractTest(unittest.TestCase):
    def test_fixture_is_fixed_and_vtest_only(self) -> None:
        self.assertIn("shared_pool_vtest_fixture_enabled: true", INVENTORY)
        self.assertIn('shared_pool_vtest_fixture_tenant_id: "vtest-shared-pool"', INVENTORY)
        self.assertNotIn("shared_pool_vtest_fixture", MYPC)
        self.assertNotIn("shared_pool_vtest_fixture", MYCP)
        self.assertNotIn("vtest-shared-pool", MYPC)
        self.assertNotIn("vtest-shared-pool", MYCP)
        self.assertIn("'vtest' in group_names", MAIN)

    def test_fixture_runs_inside_scoped_transaction_and_audits(self) -> None:
        for marker in (
            "BEGIN;",
            "COMMIT;",
            "-v ON_ERROR_STOP=1",
            "pg_advisory_xact_lock",
            "set_config('app.current_tenant', '__auth__', true)",
            "vtest-shared-pool",
            "vtest_shared_pool_fixture",
            "tier = 'free'",
            "audit_log.tenant_id",
            "relforcerowsecurity",
            "public.audit_log",
            "no_log: true",
        ):
            self.assertIn(marker, FIXTURE)
        self.assertNotRegex(FIXTURE, r"\b(?:DELETE|TRUNCATE|DROP)\b")
        self.assertIn("refusing to repurpose an unmarked tenant", FIXTURE)

    def test_empty_employee_allowlist_still_mints_fixture_only(self) -> None:
        fields = mint_bundle("")
        self.assertEqual(json.loads(fields["TENANT_IDS_JSON"]), ["vtest-shared-pool"])

    def test_tenant_allowlist_uses_explicit_rls_context_and_filters_psql_status(self) -> None:
        for marker in (
            "tenant_query_output=",
            "even when this DB role has BYPASSRLS",
            "BEGIN; SET LOCAL app.current_tenant = '__auth__';",
            "current_setting('app.current_tenant', true)",
            "SELECT DISTINCT tenant_id FROM public.employees",
            "COMMIT;",
            "tenant_query_line",
            "''|BEGIN|COMMIT|SET|DO",
        ):
            self.assertIn(marker, WORKFLOW)
        self.assertNotIn("SELECT DISTINCT tenant_id FROM public.employees WHERE tenant_id IS NOT NULL ORDER BY tenant_id\")\"", WORKFLOW)

    def test_atomic_cutover_orders_backup_deploy_health_cleanup_and_rollback(self) -> None:
        self.assertRegex(MAIN, r"inspect_quiesce\.yml[\s\S]+preflight_shared_pool_bundle\.yml[\s\S]+quiesce\.yml[\s\S]+backup_shared_pool_bundle\.yml[\s\S]+backup\.yml")
        self.assertRegex(MAIN, r"quiesce\.yml[\s\S]+backup_shared_pool_bundle\.yml[\s\S]+backup\.yml")
        self.assertRegex(MAIN, r"backup_shared_pool_bundle\.yml[\s\S]+migrate\.yml[\s\S]+shared_pool_fixture\.yml[\s\S]+deploy\.yml[\s\S]+smoke\.yml[\s\S]+cleanup_shared_pool_bundle\.yml")
        self.assertIn("shared_pool_cutover_complete: true", MAIN)
        self.assertRegex(MAIN, r"rescue:[\s\S]+rollback_shared_pool_bundle\.yml[\s\S]+Restore write-capable")
        for marker in ("docker inspect", "chmod 0600", "- rename", "shared-pool-rollback"):
            self.assertIn(marker, BACKUP + PRE_SHARED)
        self.assertIn("Remove consumers that did not exist before the cutover", ROLLBACK)
        self.assertIn("state: started", ROLLBACK)
        self.assertIn("state: absent", CLEANUP)
        self.assertIn("docker_container_info", CLEANUP)
        self.assertIn("parked rollback container", CLEANUP)

    def test_rollback_requires_complete_inspect_and_snapshot_evidence_before_mutation(self) -> None:
        for marker in (
            "ROLLBACK_VALIDATION_STARTED",
            "vtest_quiesce_containers.results | length",
            "item.failed | default(false)",
            "item.exists is defined",
            "item.container.Id is defined",
            "item.container.Config.Env is defined",
            "shared_pool_rollback_snapshot_stats",
            "ROLLBACK_VALIDATION_PASSED",
            "unknown is not treated as nonexistent",
        ):
            self.assertIn(marker, ROLLBACK)
        validation_end = ROLLBACK.index("ROLLBACK_VALIDATION_PASSED")
        mutation_start = ROLLBACK.index("Remove consumers that did not exist before the cutover")
        self.assertLess(validation_end, mutation_start)
        self.assertIn("not (shared_pool_cutover_committed | default(false) | bool)", MAIN)

    def test_cleanup_commit_point_forbids_mixed_restore_after_partial_deletion(self) -> None:
        for marker in (
            "Require complete quiesce evidence before any parked cleanup",
            "Quiesce evidence is missing or failed",
            "shared_pool_new_bundle_containers",
            "shared_pool_parked_before_cleanup",
            "CUTOVER_COMMITTED",
            "shared_pool_cutover_committed: true",
            "shared-pool-partial-cleanup-",
            "new_bundle=keep_running",
            "rollback=forbidden",
            "irreversible commit point",
        ):
            self.assertIn(marker, CLEANUP + MAIN)
        self.assertIn("shared_pool_partial_cleanup_markers", PRE_SHARED)
        self.assertIn("previous partial shared-pool cleanup", PRE_SHARED)
        self.assertRegex(MAIN, r"shared_pool_cutover_committed \| default\(false\) \| bool[\s\S]+shared-pool-partial-cleanup-")
        self.assertNotRegex(MAIN, r"shared_pool_cutover_committed \| default\(false\) \| bool[\s\S]+rollback_shared_pool_bundle\.yml")

    def test_all_shared_pool_consumers_are_quiesced_and_hidden(self) -> None:
        for service in ("a2a-router", "fleet-gateway", "channel-gateway", "fruit-industry-pack"):
            self.assertIn(f"  - {service}", INVENTORY)
        for task in (A2A, FLEET, CHANNEL, FRUIT):
            self.assertTrue(task.rstrip().endswith("no_log: true"))

    def test_fleet_e2e_env_is_vtest_only_and_uses_the_fleet_bundle_token(self) -> None:
        self.assertIn("shared_pool_vtest_e2e_enabled: true", INVENTORY)
        self.assertIn(
            "FLEET_PLATFORM_SERVICE_TOKEN: \"{{ (shared_pool_vtest_e2e_enabled | default(false) | bool) | ternary(lookup('env', 'FLEET_PLATFORM_SERVICE_TOKEN'), omit) }}\"",
            FLEET,
        )
        self.assertIn(
            "OPENAI_COMPAT_SYNTHETIC_MODE: \"{{ (shared_pool_vtest_e2e_enabled | default(false) | bool) | ternary('off', omit) }}\"",
            FLEET,
        )
        self.assertTrue(FLEET.rstrip().endswith("no_log: true"))
        for production in (MYPC, MYCP, MYPC_FLEET):
            self.assertNotIn("FLEET_PLATFORM_SERVICE_TOKEN", production)
            self.assertNotIn("OPENAI_COMPAT_SYNTHETIC_MODE", production)

    def test_wechat_contact_sync_hides_existing_token_fields(self) -> None:
        self.assertEqual(WECHAT.count("no_log: true"), 3)
        for marker in ("FLEET_SERVICE_SECRET", "SERVICE_SECRET", "WECOM_CORP_ID", "WECOM_CONTACTS_SECRET"):
            self.assertIn(marker, WECHAT)

    def test_restart_probe_is_guarded_vtest_only_and_runs_before_cleanup(self) -> None:
        for marker in (
            "VTEST_SHARED_POOL_RESTART_PROBE",
            "openclaw-fleet-gateway",
            "/app/data/instances/vtest-shared-pool-e2e/restart-manifest.json",
            "manifest_mode",
            "manifest_owner",
            "manifest_schema",
            "manifest_employee",
            "docker_container_exec",
            "wait_for:",
            "shared-pool-restart-probe",
            "/app/packages/fleet-gateway/dist/vtest/shared-pool-e2e.js",
            "--tenant-id",
            "--employee-a",
            "--employee-b",
            "--employee-guard",
            "--restart-post",
            "state: absent",
        ):
            self.assertIn(marker, RESTART_PROBE + WORKFLOW)
        self.assertRegex(MAIN, r"cleanup_shared_pool_bundle\.yml[\s\S]+shared_pool_restart_probe\.yml")
        self.assertRegex(MAIN, r"shared_pool_cutover_complete[\s\S]+shared_pool_restart_probe\.yml")
        self.assertIn("'vtest' in group_names", RESTART_PROBE)
        self.assertIn("shared_pool_vtest_fixture_enabled", RESTART_PROBE)
        self.assertIn("vecta-app/tasks/fleet_gateway.yml", RESTART_PROBE)
        self.assertNotIn("FLEET_PLATFORM_SERVICE_TOKEN:", RESTART_PROBE)
        self.assertNotIn("docker restart", RESTART_PROBE)
        self.assertNotIn("docker restart", WORKFLOW)
        self.assertNotIn("shared_pool_restart_probe_marker_path", RESTART_PROBE + WORKFLOW)
        self.assertNotIn("shared_pool_restart_probe_post_command", RESTART_PROBE + WORKFLOW)

    def test_restart_probe_is_default_off_and_has_no_command_inputs(self) -> None:
        self.assertIn("shared_pool_restart_probe:\n        type: boolean\n        default: false", WORKFLOW)
        self.assertNotIn("shared_pool_restart_probe_marker_path:", WORKFLOW)
        self.assertNotIn("shared_pool_restart_probe_post_command:", WORKFLOW)
        self.assertNotIn("lookup('env', 'VTEST_SHARED_POOL_RESTART_PROBE_MARKER_PATH')", RESTART_PROBE)
        self.assertNotIn("lookup('env', 'VTEST_SHARED_POOL_RESTART_PROBE_POST_COMMAND')", RESTART_PROBE)
        self.assertIn("argv:\n      - node", RESTART_PROBE)

    def test_shared_pool_preflight_snapshots_before_quiesce_and_parks_after(self) -> None:
        for marker in (
            "shared_pool_partial_cleanup_markers",
            "vtest_write_quiesce_services",
            "docker_container_info",
            "shared-pool-rollback",
            "vtest_write_quiesce_services",
            "item.container.State.Running",
            "snapshot already exists",
            "snapshot_shape",
            "snapshot_id",
            "snapshot_env",
            "mv -- \"$temp\" \"$target\"",
            "0600",
        ):
            self.assertIn(marker, PRE_SHARED + INSPECT)
        self.assertNotIn("docker rename", PRE_SHARED + INSPECT)
        self.assertIn("- rename", BACKUP)
        self.assertRegex(MAIN, r"inspect_quiesce\.yml[\s\S]+preflight_shared_pool_bundle\.yml[\s\S]+quiesce\.yml")
        self.assertRegex(MAIN, r"quiesce\.yml[\s\S]+backup_shared_pool_bundle\.yml[\s\S]+backup\.yml")
        self.assertIn("Require complete quiesce evidence before any parked cleanup", CLEANUP)
        self.assertIn("unknown is not treated as nonexistent", CLEANUP)
        self.assertIn("item.container.State.Running", (ROOT / "roles/deploy-vtest/tasks/quiesce.yml").read_text())
        self.assertIn("register: vtest_quiesce_stop_results", (ROOT / "roles/deploy-vtest/tasks/quiesce.yml").read_text())
        self.assertIn("vtest_quiesce_containers.results", (ROOT / "roles/deploy-vtest/tasks/quiesce.yml").read_text())
        self.assertIn("Inspect write-capable app containers before migration", INSPECT)
        self.assertIn("register: vtest_quiesce_containers", INSPECT)
        self.assertNotIn("shared_pool_vtest_fixture_enabled", INSPECT)
        self.assertNotIn("shared_pool_vtest_fixture_enabled", (ROOT / "roles/deploy-vtest/tasks/quiesce.yml").read_text())
        self.assertIn("when: item.exists | default(false)", BACKUP)

    def test_database_backup_failure_can_restore_current_or_parked_containers(self) -> None:
        self.assertRegex(MAIN, r"quiesce\.yml[\s\S]+backup_shared_pool_bundle\.yml[\s\S]+backup\.yml")
        self.assertIn("- rename", BACKUP)
        self.assertIn("if docker inspect \"openclaw-{{ item.item }}.shared-pool-rollback\"", ROLLBACK)
        self.assertIn("Restart the complete old platform consumer set", ROLLBACK)

    def test_restart_is_independent_post_commit_acceptance(self) -> None:
        self.assertRegex(MAIN, r"shared_pool_cleanup_complete: true[\s\S]+shared_pool_restart_probe_started")
        self.assertIn("shared-pool-restart-failure-", MAIN)
        self.assertIn("new_bundle=keep_running", MAIN)
        self.assertIn("rollback=forbidden", MAIN)

    def test_manifest_mount_is_dedicated_and_does_not_chown_deploy_root(self) -> None:
        for marker in (
            "deploy/vtest-shared-pool-e2e",
            "owner: \"1000\"",
            "group: \"1000\"",
            "mode: \"0700\"",
            "shared_pool_manifest_dir_stat",
            "shared_pool_manifest_dir_writable",
            'become_user: "#1000"',
            "UID 1000 cannot write",
            "vtest-shared-pool-e2e:/app/data/instances/vtest-shared-pool-e2e",
        ):
            self.assertIn(marker, PRE_SHARED + FLEET)
        self.assertNotIn('path: "{{ remote_dir }}/deploy"\n    owner: "1000"', PRE_SHARED)
        self.assertNotIn('"{{ remote_dir }}/deploy:/app/data/instances"\n    owner:', FLEET)

    def test_token_preflight_uses_real_ed25519_verifier(self) -> None:
        self.assertIn("verify-vtest-platform-bundle.mjs", PREFLIGHT)
        self.assertIn("executable: node", PREFLIGHT)
        self.assertIn("no_log: true", PREFLIGHT)
        for claim in ("channel-gateway", "fleet-gateway", "channel:internal", "fleet:internal", "vtest-shared-pool"):
            self.assertIn(claim, VERIFY + MINT)
        for marker in ("import { createPublicKey, verify", "header.alg", "payload.aud", "payload.scope", "payload.tid", "payload.tenant_ids", "payload.iat", "payload.exp"):
            self.assertIn(marker, VERIFY)
        self.assertNotIn("verify-vtest-platform-token.py", PREFLIGHT + WORKFLOW)

    def test_workflow_mints_ephemeral_bundle_and_masks_without_persistence(self) -> None:
        for marker in (
            "mint-vtest-platform-bundle.mjs",
            "::add-mask::",
            "VTEST_EXPECTED_TENANT_IDS_JSON",
            "trap cleanup_ephemeral_platform_bundle EXIT",
            "unset PLATFORM_SERVICE_JWT_PUBLIC_KEY",
        ):
            self.assertIn(marker, WORKFLOW)
        self.assertNotIn("GITHUB_OUTPUT", WORKFLOW)
        self.assertNotIn("GITHUB_ENV", WORKFLOW)
        self.assertNotIn("upload-artifact", WORKFLOW)
        for legacy_secret in (
            "VTEST_PLATFORM_SERVICE_JWT_PUBLIC_KEY:",
            "VTEST_CHANNEL_PLATFORM_SERVICE_TOKEN:",
            "VTEST_FLEET_PLATFORM_SERVICE_TOKEN:",
            "VTEST_FLEET_FRUIT_PLATFORM_TOKEN:",
        ):
            self.assertNotIn(legacy_secret, WORKFLOW)

    def test_mint_emits_three_real_signed_tokens_and_fixture_scope(self) -> None:
        fields = mint_bundle()
        self.assertEqual(
            set(fields),
            {
                "PUBLIC_KEY_ESCAPED",
                "CHANNEL_PLATFORM_SERVICE_TOKEN",
                "FLEET_PLATFORM_SERVICE_TOKEN",
                "FLEET_FRUIT_PLATFORM_TOKEN",
                "TENANT_IDS_JSON",
            },
        )
        self.assertIn("vtest-shared-pool", json.loads(fields["TENANT_IDS_JSON"]))
        for name in ("CHANNEL_PLATFORM_SERVICE_TOKEN", "FLEET_PLATFORM_SERVICE_TOKEN", "FLEET_FRUIT_PLATFORM_TOKEN"):
            self.assertEqual(len(fields[name].split(".")), 3)
        self.assertEqual(token_payload(fields["FLEET_FRUIT_PLATFORM_TOKEN"])["scope"], ["fleet:internal", "fleet:scenario-pack-publish"])
        self.assertNotIn("console.log(privateKey", MINT)
        self.assertNotIn("PRIVATE_KEY=", MINT)

    def test_real_signed_bundle_passes_claim_and_signature_preflight(self) -> None:
        result = verify_bundle(mint_bundle())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "SHARED_POOL_PLATFORM_TOKEN_SIGNATURES_OK")

    def test_tampered_signature_is_rejected_without_token_logging(self) -> None:
        fields = mint_bundle()
        original = fields["FLEET_PLATFORM_SERVICE_TOKEN"]
        fields["FLEET_PLATFORM_SERVICE_TOKEN"] = f"{original[:-1]}{'A' if original[-1] != 'A' else 'B'}"
        result = verify_bundle(fields)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(original, result.stdout + result.stderr)

    def test_verifier_rejects_malformed_claim_shapes(self) -> None:
        for malformed in ("not-a-jwt", "a.b.c", "eyJhbGciOiJub25lIn0.e30.invalid"):
            fields = mint_bundle()
            fields["FLEET_PLATFORM_SERVICE_TOKEN"] = malformed
            result = verify_bundle(fields)
            self.assertNotEqual(result.returncode, 0)

    def test_verifier_rejects_each_required_bad_claim_variant(self) -> None:
        for case in ("bool", "nan", "alg", "aud", "scope", "tid", "tenant_ids", "exp", "iat", "fruit-generic"):
            result = verify_bundle(signed_variant(case))
            self.assertNotEqual(result.returncode, 0, case)

    def test_verifier_accepts_fruit_token_only_with_both_required_scopes(self) -> None:
        result = verify_bundle(signed_variant("normal"))
        self.assertEqual(result.returncode, 0, result.stderr)
        generic_only = verify_bundle(signed_variant("fruit-generic"))
        self.assertNotEqual(generic_only.returncode, 0)


if __name__ == "__main__":
    unittest.main()
