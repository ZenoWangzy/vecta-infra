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
AUDIT_OWNER_ROOTS = (ROOT / "deploy", ROOT / "roles", ROOT / "inventories")
AUDIT_OWNER_MARKERS = re.compile(
    r"AUDIT_DIR|fleet_gateway_audit_(?:volume|dir)|/app/data/audit",
    re.IGNORECASE,
)


class FleetGatewayAuditVolumeContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.role = ROLE.read_text()
        self.inventory = INVENTORY.read_text()
        self.runbook = RUNBOOK.read_text()

    def task_block(self, task_name: str) -> str:
        start = self.role.index(f"- name: {task_name}")
        end = self.role.find("\n- name:", start + 1)
        return self.role[start:] if end == -1 else self.role[start:end]

    def when_clause(self, task_name: str) -> str:
        match = re.search(
            r"\n  when:\n((?:    .*\n)+)",
            self.task_block(task_name),
        )
        if match is None:
            self.fail(f"{task_name} has no list-form when clause")
        return match.group(1)

    def test_role_is_the_only_audit_volume_owner(self) -> None:
        owner_files = {
            path
            for root in AUDIT_OWNER_ROOTS
            for path in root.rglob("*")
            if path.is_file() and AUDIT_OWNER_MARKERS.search(path.read_text())
        }
        self.assertEqual(owner_files, {ROLE, INVENTORY})
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
        self.assertIn("mypc_fleet_gateway_audit_contract_compliant", self.role)

    def test_compliant_same_image_skips_audit_mutation_and_recreation(self) -> None:
        decisions = self.role[
            self.role.index(
                "- name: Record whether current Fleet gateway is already audit-contract compliant"
            ) :
        ]
        self.assertIn("mypc_fleet_gateway_audit_repair_required", decisions)
        self.assertIn("mypc_fleet_gateway_image_changed", decisions)
        self.assertIn("mypc_fleet_gateway_recreate_required", decisions)
        self.assertIn(
            "mypc_fleet_gateway_live.Config.Image != fleet_gateway_image",
            decisions,
        )
        self.assertIn(
            "mypc_fleet_gateway_audit_repair_required or",
            self.task_block("Decide whether the Fleet gateway needs recreation"),
        )

        for task_name in (
            "Pull the selected Fleet image for an explicit Fleet gateway transition",
            "Capture running per-user runtime count before Fleet adoption",
            "Back up Fleet instance state before Nexus adoption",
            "Run Fleet regression before Nexus adoption",
            "Stop Fleet gateway before preparing its audit volume",
            "Ensure the external named Fleet audit volume exists",
            "Check whether the Fleet audit volume needs first-use seeding",
            "Check Fleet audit volume ownership against the validated runtime identity",
            "Repair Fleet audit volume ownership when it differs",
            "Seed the empty Fleet audit volume from the quiesced gateway",
            "Recreate mypc Fleet gateway from the selected Nexus image",
            "Require the recreated mypc Fleet gateway has no Fruit host bind",
            "Require recreated Fleet gateway audit volume and runtime contract",
            "Require recreated Fleet gateway audit directory is writable by its runtime identity",
            "Require the same per-user runtime count after Fleet adoption",
            "Run Fleet regression after Nexus adoption",
        ):
            self.assertIn("when:", self.task_block(task_name), task_name)

    def test_compliant_explicit_image_change_recreates_without_audit_repair(self) -> None:
        pull = self.task_block(
            "Pull the selected Fleet image for an explicit Fleet gateway transition"
        )
        self.assertIn("mypc_fleet_gateway_image_changed", pull)

        for task_name in (
            "Stop Fleet gateway before preparing its audit volume",
            "Ensure the external named Fleet audit volume exists",
            "Check whether the Fleet audit volume needs first-use seeding",
            "Check Fleet audit volume ownership against the validated runtime identity",
            "Repair Fleet audit volume ownership when it differs",
            "Seed the empty Fleet audit volume from the quiesced gateway",
        ):
            self.assertIn(
                "mypc_fleet_gateway_audit_repair_required",
                self.task_block(task_name),
                task_name,
            )

        recreate = self.task_block(
            "Recreate mypc Fleet gateway from the selected Nexus image"
        )
        self.assertIn("mypc_fleet_gateway_recreate_required", recreate)
        self.assertIn('image: "{{ fleet_gateway_image }}"', recreate)
        self.assertIn(
            "{{ fleet_gateway_audit_volume }}:{{ fleet_gateway_audit_dir }}:rw",
            recreate,
        )
        self.assertIn("'AUDIT_DIR': fleet_gateway_audit_dir", recreate)
        self.assertIn('user: "{{ mypc_fleet_gateway_runtime_user }}"', recreate)
        self.assertNotIn("identical", recreate.lower())
        self.assertNotIn("mypc_fleet_gateway_image_ids", self.role)
        self.assertNotIn("must match the live image", self.role)

    def test_image_and_audit_guards_are_mutually_exclusive(self) -> None:
        pull_when = self.when_clause(
            "Pull the selected Fleet image for an explicit Fleet gateway transition"
        )
        self.assertIn("mypc_fleet_gateway_image_changed", pull_when)
        self.assertNotIn("mypc_fleet_gateway_audit_repair_required", pull_when)
        self.assertNotIn("mypc_fleet_gateway_recreate_required", pull_when)

        audit_tasks = (
            "Stop Fleet gateway before preparing its audit volume",
            "Ensure the external named Fleet audit volume exists",
            "Check whether the Fleet audit volume needs first-use seeding",
            "Check Fleet audit volume ownership against the validated runtime identity",
            "Repair Fleet audit volume ownership when it differs",
            "Seed the empty Fleet audit volume from the quiesced gateway",
        )
        for task_name in audit_tasks:
            audit_when = self.when_clause(task_name)
            self.assertIn(
                "mypc_fleet_gateway_audit_repair_required",
                audit_when,
                task_name,
            )
            self.assertNotIn("mypc_fleet_gateway_image_changed", audit_when, task_name)
            self.assertNotIn(
                "mypc_fleet_gateway_recreate_required",
                audit_when,
                task_name,
            )

    def test_noncompliant_same_image_repairs_without_pull_then_recreates(self) -> None:
        pull = self.task_block(
            "Pull the selected Fleet image for an explicit Fleet gateway transition"
        )
        self.assertIn("mypc_fleet_gateway_image_changed", pull)

        for task_name in (
            "Stop Fleet gateway before preparing its audit volume",
            "Ensure the external named Fleet audit volume exists",
            "Check whether the Fleet audit volume needs first-use seeding",
            "Check Fleet audit volume ownership against the validated runtime identity",
            "Repair Fleet audit volume ownership when it differs",
            "Seed the empty Fleet audit volume from the quiesced gateway",
        ):
            self.assertIn(
                "mypc_fleet_gateway_audit_repair_required",
                self.task_block(task_name),
                task_name,
            )

        self.assertEqual(
            self.role.count("- name: Stop Fleet gateway before preparing its audit volume"),
            1,
        )
        self.assertIn(
            "mypc_fleet_gateway_recreate_required",
            self.task_block("Recreate mypc Fleet gateway from the selected Nexus image"),
        )

    def test_noncompliant_changed_image_quiesces_once_then_repairs_and_recreates(self) -> None:
        ordered_tasks = (
            "Stop Fleet gateway before preparing its audit volume",
            "Ensure the external named Fleet audit volume exists",
            "Check whether the Fleet audit volume needs first-use seeding",
            "Check Fleet audit volume ownership against the validated runtime identity",
            "Repair Fleet audit volume ownership when it differs",
            "Seed the empty Fleet audit volume from the quiesced gateway",
            "Recreate mypc Fleet gateway from the selected Nexus image",
        )
        positions = [self.role.index(f"- name: {name}") for name in ordered_tasks]
        self.assertEqual(
            self.role.count("- name: Stop Fleet gateway before preparing its audit volume"),
            1,
        )
        self.assertEqual(positions, sorted(positions))

        for task_name in ordered_tasks[2:-1]:
            self.assertIn(
                "mypc_fleet_gateway_audit_repair_required",
                self.task_block(task_name),
                task_name,
            )
        self.assertIn(
            "mypc_fleet_gateway_audit_ownership.rc == 1",
            self.task_block("Repair Fleet audit volume ownership when it differs"),
        )
        self.assertIn(
            "failed_when: mypc_fleet_gateway_audit_ownership_repair.rc != 0",
            self.task_block("Repair Fleet audit volume ownership when it differs"),
        )
        self.assertIn(
            "mypc_fleet_gateway_recreate_required",
            self.task_block("Recreate mypc Fleet gateway from the selected Nexus image"),
        )

    def test_unapproved_or_unresolvable_target_fails_closed(self) -> None:
        approval_gate = self.task_block(
            "Refuse a Fleet gateway image that does not descend from the ticket 00 fail-closed guard"
        )
        self.assertIn("mypc_fleet_gateway_guard_fetch.rc == 0", approval_gate)
        self.assertIn("mypc_fleet_gateway_guard_check.rc == 0", approval_gate)

        target_exists = self.task_block(
            "Require the selected Fleet gateway target image exists"
        )
        self.assertIn(
            "failed_when: mypc_fleet_gateway_target_image.rc != 0",
            target_exists,
        )
        pull_start = self.role.index(
            "- name: Pull the selected Fleet image for an explicit Fleet gateway transition"
        )
        self.assertLess(
            self.role.index(
                "- name: Refuse a Fleet gateway image that does not descend from the ticket 00 fail-closed guard"
            ),
            pull_start,
        )

    def test_seed_happens_after_quiescing_and_before_recreate(self) -> None:
        stop = self.role.index("- name: Stop Fleet gateway before preparing")
        empty = self.role.index(
            "- name: Check whether the Fleet audit volume needs first-use seeding"
        )
        ownership = self.role.index(
            "- name: Check Fleet audit volume ownership against the validated runtime identity"
        )
        repair = self.role.index(
            "- name: Repair Fleet audit volume ownership when it differs"
        )
        seed = self.role.index("- name: Seed the empty Fleet audit volume")
        recreate = self.role.index(
            "- name: Recreate mypc Fleet gateway from the selected Nexus image"
        )
        self.assertLess(stop, seed)
        self.assertLess(stop, empty)
        self.assertLess(empty, ownership)
        self.assertLess(ownership, repair)
        self.assertLess(repair, seed)
        self.assertLess(seed, recreate)
        seed_block = self.task_block("Seed the empty Fleet audit volume from the quiesced gateway")
        self.assertIn("docker cp", seed_block)
        self.assertIn("stage_dir", seed_block)
        self.assertIn("mypc_fleet_gateway_audit_empty.rc == 0", seed_block)
        self.assertNotIn("mypc_fleet_gateway_audit_empty.rc == 1", seed_block)
        self.assertIn("AUDIT_DIR", self.role[recreate:])

    def test_non_empty_audit_volume_never_enters_seed_path(self) -> None:
        empty = self.task_block(
            "Check whether the Fleet audit volume needs first-use seeding"
        )
        seed = self.task_block(
            "Seed the empty Fleet audit volume from the quiesced gateway"
        )
        self.assertIn("failed_when: mypc_fleet_gateway_audit_empty.rc not in [0, 1]", empty)
        self.assertIn("mypc_fleet_gateway_audit_empty.rc == 0", seed)
        self.assertNotIn("mypc_fleet_gateway_audit_empty.rc == 1", seed)
        self.assertLess(
            self.role.index("- name: Check whether the Fleet audit volume needs first-use seeding"),
            self.role.index("- name: Repair Fleet audit volume ownership when it differs"),
        )

    def test_runbook_marks_historical_and_retention_boundaries(self) -> None:
        self.assertIn("历史生产记录", self.runbook)
        self.assertIn("不是当前 live 证据", self.runbook)
        self.assertIn("#1070", self.runbook)
        self.assertIn("#1076", self.runbook)
        self.assertIn("#1054", self.runbook)
        self.assertIn("#1036", self.runbook)
        self.assertIn("roles/vecta-app/tasks/fleet_gateway_mypc.yml", self.runbook)
        self.assertNotIn("compose.audit-volume.yml", self.runbook)
        self.assertIn("already satisfies", self.runbook)
        self.assertIn("non-empty", self.runbook)

    def test_pr_workflow_runs_this_script(self) -> None:
        workflow = WORKFLOW.read_text()
        self.assertRegex(workflow, r"for contract in scripts/test_\*\.py")


if __name__ == "__main__":
    unittest.main()
