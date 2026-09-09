#!/usr/bin/env python3
"""Contract for the role-owned Fleet audit volume migration."""

from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "roles/vecta-app/tasks/fleet_gateway_mypc.yml"
INVENTORY = ROOT / "inventories/mypc/group_vars/mypc.yml"
OVERLAY = ROOT / "deploy/gateways/compose.audit-volume.yml"
RUNBOOK = ROOT / "docs/runbooks/audit-chain-volume-and-verification.md"
WORKFLOW = ROOT / ".github/workflows/pr-contract-checks.yml"


class FleetGatewayAuditVolumeContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.role = ROLE.read_text()
        self.inventory = INVENTORY.read_text()
        self.runbook = RUNBOOK.read_text()

    def test_role_is_the_only_audit_volume_owner(self) -> None:
        self.assertFalse(OVERLAY.exists())
        self.assertIn("community.docker.docker_volume:", self.role)
        self.assertIn('name: "{{ fleet_gateway_audit_volume }}"', self.role)
        self.assertIn("state: present", self.role)
        self.assertIn("fleet_gateway_audit_volume: fleet_gateway_audit_data", self.inventory)
        self.assertIn("fleet_gateway_audit_dir: /app/data/audit", self.inventory)
        self.assertNotIn("compose.audit-volume.yml", self.role)

    def test_runtime_identity_is_one_validated_input(self) -> None:
        self.assertIn(
            'mypc_fleet_gateway_runtime_user: "{{ mypc_fleet_gateway_live.Config.User }}"',
            self.role,
        )
        self.assertIn("mypc_fleet_gateway_live.Config.User == fleet_gateway_user", self.role)
        self.assertIn('fleet_gateway_user: "1000:1000"', self.inventory)
        self.assertNotIn('"1000:1000"', self.role)
        self.assertRegex(
            self.role,
            re.compile(r"--user\n\s+- \"0\"\n\s+- --entrypoint\n\s+- /bin/sh"),
        )
        self.assertIn(
            "chown -R {{ mypc_fleet_gateway_runtime_user | quote }}",
            self.role,
        )
        self.assertIn('user: "{{ mypc_fleet_gateway_runtime_user }}"', self.role)
        self.assertIn(
            "- \"{{ mypc_fleet_gateway_runtime_user }}\"\n"
            "      - \"{{ fleet_gateway_container_name }}\"",
            self.role,
        )

    def test_mount_environment_and_fail_closed_postcondition(self) -> None:
        self.assertIn("'AUDIT_DIR': fleet_gateway_audit_dir", self.role)
        self.assertGreaterEqual(
            self.role.count(
                '"{{ fleet_gateway_audit_volume }}:{{ fleet_gateway_audit_dir }}:rw"'
            ),
            3,
        )
        self.assertIn(
            "failed_when: mypc_fleet_gateway_audit_empty.rc not in [0, 1]",
            self.role,
        )
        self.assertIn('-print -quit)" || exit 2;', self.role)
        postcondition = self.role[
            self.role.index("- name: Require recreated Fleet gateway audit volume")
        :]
        self.assertIn("jq -e", postcondition)
        self.assertIn("Config.User == $runtime_user", postcondition)
        self.assertIn('("AUDIT_DIR=" + $audit_dir)', postcondition)
        self.assertIn(
            '.Type == "volume" and .Name == $audit_volume and '
            '.Destination == $audit_dir and .RW',
            postcondition,
        )
        self.assertIn(
            'test "$AUDIT_DIR" = {{ fleet_gateway_audit_dir | quote }}',
            postcondition,
        )
        self.assertIn('test -w "$AUDIT_DIR"', postcondition)

    def test_seed_happens_after_quiescing_and_before_recreate(self) -> None:
        stop = self.role.index("- name: Stop Fleet gateway before preparing")
        seed = self.role.index("- name: Seed the empty Fleet audit volume")
        recreate = self.role.index(
            "- name: Recreate mypc Fleet gateway from the identical Nexus image"
        )
        self.assertLess(stop, seed)
        self.assertLess(seed, recreate)
        self.assertIn("docker cp", self.role[seed:recreate])
        self.assertIn("stage_dir", self.role[seed:recreate])
        self.assertIn("AUDIT_DIR", self.role[recreate:])

    def test_runbook_marks_historical_and_retention_boundaries(self) -> None:
        self.assertIn("历史生产记录", self.runbook)
        self.assertIn("不是当前 live 证据", self.runbook)
        self.assertIn("#1070", self.runbook)
        self.assertIn("#1076", self.runbook)
        self.assertIn("#1054", self.runbook)
        self.assertIn("#1036", self.runbook)
        self.assertIn("roles/vecta-app/tasks/fleet_gateway_mypc.yml", self.runbook)
        self.assertNotIn("compose.audit-volume.yml", self.runbook)

    def test_pr_workflow_runs_this_script(self) -> None:
        workflow = WORKFLOW.read_text()
        self.assertRegex(workflow, r"for contract in scripts/test_\*\.py")


if __name__ == "__main__":
    unittest.main()
