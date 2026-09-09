#!/usr/bin/env python3
"""Contract for the role-owned Fleet audit volume migration."""

from __future__ import annotations

from pathlib import Path
import re
import unittest

import yaml


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
SET_FACT_MODULES = ("ansible.builtin.set_fact", "set_fact")


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader: UniqueKeyLoader, node, deep: bool = False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


_MISSING = object()


class BooleanExpressionParser:
    _TOKEN = re.compile(
        r"\s*(?:(==|!=)|([A-Za-z_][A-Za-z0-9_.]*)|([0-9]+)|([()|]))"
    )

    def __init__(self, expression: str, values: dict[str, object]) -> None:
        source = expression.strip()
        if source.startswith("{{"):
            if not source.endswith("}}"):
                raise ValueError(f"unclosed Jinja expression: {expression}")
            source = source[2:-2].strip()
        self.tokens = self._tokenize(source)
        self.values = values
        self.position = 0

    @classmethod
    def _tokenize(cls, source: str) -> list[str]:
        tokens = []
        position = 0
        while position < len(source):
            match = cls._TOKEN.match(source, position)
            if match is None:
                raise ValueError(f"unsupported expression syntax near {source[position:]!r}")
            token = next(group for group in match.groups() if group is not None)
            tokens.append(token)
            position = match.end()
        if not tokens:
            raise ValueError("empty expression")
        return tokens

    def _peek(self) -> str | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def _consume(self, expected: str | None = None) -> str:
        token = self._peek()
        if token is None:
            raise ValueError("unexpected end of expression")
        if expected is not None and token != expected:
            raise ValueError(f"expected {expected!r}, got {token!r}")
        self.position += 1
        return token

    def _match(self, token: str) -> bool:
        if self._peek() == token:
            self.position += 1
            return True
        return False

    def parse(self) -> bool:
        value = self._parse_or()
        if self._peek() is not None:
            raise ValueError(f"unexpected token {self._peek()!r}")
        return bool(value)

    def _parse_or(self):
        value = self._parse_and()
        while self._match("or"):
            right = self._parse_and()
            value = bool(value) or bool(right)
        return value

    def _parse_and(self):
        value = self._parse_not()
        while self._match("and"):
            right = self._parse_not()
            value = bool(value) and bool(right)
        return value

    def _parse_not(self):
        if self._match("not"):
            return not self._parse_not()
        return self._parse_comparison()

    def _parse_comparison(self):
        left = self._parse_value()
        operator = self._peek()
        if operator not in ("==", "!="):
            return left
        self.position += 1
        right = self._parse_value()
        return left == right if operator == "==" else left != right

    def _parse_value(self):
        value = self._parse_primary()
        if self._match("|"):
            self._consume("default")
            self._consume("(")
            fallback = int(self._consume())
            self._consume(")")
            value = fallback if value is _MISSING else value
        if value is _MISSING:
            raise ValueError("unknown variable without a default filter")
        return value

    def _parse_primary(self):
        if self._match("("):
            value = self._parse_or()
            self._consume(")")
            return value
        token = self._consume()
        if token.isdigit():
            return int(token)
        if token in ("true", "false"):
            return token == "true"
        if token in ("and", "or", "not", "default"):
            raise ValueError(f"unexpected operator {token!r}")
        return self.values.get(token, _MISSING)


class FleetGatewayAuditVolumeContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.role = ROLE.read_text()
        self.inventory = INVENTORY.read_text()
        self.runbook = RUNBOOK.read_text()
        self.role_tasks = yaml.load(self.role, Loader=UniqueKeyLoader)
        self.assertIsInstance(self.role_tasks, list)

    def task_block(self, task_name: str) -> str:
        start = self.role.index(f"- name: {task_name}")
        end = self.role.find("\n- name:", start + 1)
        return self.role[start:] if end == -1 else self.role[start:end]

    def yaml_task(self, task_name: str) -> dict:
        matches = [
            task
            for task in self.role_tasks
            if isinstance(task, dict) and task.get("name") == task_name
        ]
        self.assertEqual(len(matches), 1, f"{task_name} must have one YAML task")
        return matches[0]

    def set_fact_assignments(self, tasks=None) -> dict:
        if tasks is None:
            tasks = self.role_tasks
        assignments = {}
        for task in tasks:
            if not isinstance(task, dict):
                continue
            for module_name in SET_FACT_MODULES:
                facts = task.get(module_name)
                if not isinstance(facts, dict):
                    continue
                for fact_name, expression in facts.items():
                    assignments.setdefault(fact_name, []).append(
                        (task.get("name"), module_name, expression)
                    )
        return assignments

    def fact_expression(self, fact_name: str) -> str:
        entries = self.set_fact_assignments().get(fact_name, [])
        self.assertEqual(len(entries), 1, f"{fact_name} must be defined once")
        expression = entries[0][2]
        self.assertIsInstance(expression, str, fact_name)
        return expression

    def when_expressions(self, task_name: str) -> list[str]:
        when = self.yaml_task(task_name).get("when")
        self.assertIsInstance(when, list, f"{task_name} must use list-form when")
        for expression in when:
            self.assertIsInstance(expression, str, task_name)
        return when

    def evaluate_when(self, task_name: str, values: dict[str, object]) -> bool:
        return all(
            BooleanExpressionParser(expression, values).parse()
            for expression in self.when_expressions(task_name)
        )

    @staticmethod
    def normalize_expression(expression: str) -> str:
        expression = expression.strip()
        if expression.startswith("{{") and expression.endswith("}}"):
            expression = expression[2:-2]
        return " ".join(expression.split())

    def assert_set_fact_assignments_unique(self, assignments: dict) -> None:
        for fact_name, entries in assignments.items():
            self.assertEqual(len(entries), 1, f"{fact_name} is reassigned")

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

    def test_yaml_task_names_are_unique(self) -> None:
        names = []
        for task in self.role_tasks:
            self.assertIsInstance(task, dict)
            name = task.get("name")
            self.assertIsInstance(name, str)
            names.append(name)
        self.assertEqual(len(names), len(set(names)))

    def test_short_set_fact_reassignment_is_rejected(self) -> None:
        tasks = [
            {
                "name": "initial FQCN definition",
                "ansible.builtin.set_fact": {"gate": "{{ true }}"},
            },
            {
                "name": "later short-name rewrite",
                "set_fact": {"gate": "{{ false }}"},
            },
        ]
        assignments = self.set_fact_assignments(tasks)
        with self.assertRaises(AssertionError):
            self.assert_set_fact_assignments_unique(assignments)

    def test_transition_fact_definitions_are_unique_and_exact(self) -> None:
        approved = {
            "mypc_fleet_gateway_audit_contract_compliant": (
                "mypc_fleet_gateway_audit_contract.rc == 0 and "
                "(mypc_fleet_gateway_audit_writeability.rc | default(1)) == 0"
            ),
            "mypc_fleet_gateway_audit_repair_required": (
                "not mypc_fleet_gateway_audit_contract_compliant"
            ),
            "mypc_fleet_gateway_image_changed": (
                "mypc_fleet_gateway_live.Config.Image != fleet_gateway_image"
            ),
            "mypc_fleet_gateway_recreate_required": (
                "mypc_fleet_gateway_audit_repair_required or "
                "mypc_fleet_gateway_image_changed"
            ),
        }
        assignments = self.set_fact_assignments()
        for fact_name, expression in approved.items():
            entries = assignments.get(fact_name, [])
            self.assertEqual(len(entries), 1, fact_name)
            self.assertIsInstance(entries[0][2], str, fact_name)
            self.assertEqual(
                self.normalize_expression(entries[0][2]),
                expression,
                fact_name,
            )
        self.assert_set_fact_assignments_unique(assignments)

    def test_transition_truth_table_is_driven_by_role_expressions(self) -> None:
        expressions = {
            fact_name: self.fact_expression(fact_name)
            for fact_name in (
                "mypc_fleet_gateway_audit_contract_compliant",
                "mypc_fleet_gateway_audit_repair_required",
                "mypc_fleet_gateway_image_changed",
                "mypc_fleet_gateway_recreate_required",
            )
        }
        truth_table = (
            ((True, False), (False, False, False)),
            ((True, True), (True, False, True)),
            ((False, False), (False, True, True)),
            ((False, True), (True, True, True)),
        )
        for (audit_compliant, image_changed), expected in truth_table:
            values = {
                "ansible_check_mode": False,
                "mypc_fleet_gateway_audit_contract.rc": (
                    0 if audit_compliant else 1
                ),
                "mypc_fleet_gateway_audit_writeability.rc": 0,
                "mypc_fleet_gateway_live.Config.Image": (
                    "old-image" if image_changed else "selected-image"
                ),
                "fleet_gateway_image": "selected-image",
            }
            values[
                "mypc_fleet_gateway_audit_contract_compliant"
            ] = BooleanExpressionParser(
                expressions["mypc_fleet_gateway_audit_contract_compliant"],
                values,
            ).parse()
            values["mypc_fleet_gateway_audit_repair_required"] = (
                BooleanExpressionParser(
                    expressions["mypc_fleet_gateway_audit_repair_required"],
                    values,
                ).parse()
            )
            values["mypc_fleet_gateway_image_changed"] = BooleanExpressionParser(
                expressions["mypc_fleet_gateway_image_changed"],
                values,
            ).parse()
            values["mypc_fleet_gateway_recreate_required"] = (
                BooleanExpressionParser(
                    expressions["mypc_fleet_gateway_recreate_required"],
                    values,
                ).parse()
            )
            actual = (
                self.evaluate_when(
                    "Pull the selected Fleet image for an explicit Fleet gateway transition",
                    values,
                ),
                self.evaluate_when(
                    "Stop Fleet gateway before preparing its audit volume",
                    values,
                ),
                self.evaluate_when(
                    "Recreate mypc Fleet gateway from the selected Nexus image",
                    values,
                ),
            )
            self.assertEqual(actual, expected)

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
        pull_when = self.when_expressions(
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
            audit_when = self.when_expressions(task_name)
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
