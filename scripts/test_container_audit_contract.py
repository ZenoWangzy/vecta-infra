#!/usr/bin/env python3
"""Contract tests for explicit and passive container audit records."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "roles/container-audit/files/vecta-container-audit.py"
SPEC = importlib.util.spec_from_file_location("container_audit", SCRIPT)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class ContainerAuditContract(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.log = Path(self.temp.name) / "container-operations.jsonl"
        self.old_path, self.old_limit = AUDIT.LOG_PATH, AUDIT.MAX_BYTES
        AUDIT.LOG_PATH, AUDIT.MAX_BYTES = self.log, 400

    def tearDown(self) -> None:
        AUDIT.LOG_PATH, AUDIT.MAX_BYTES = self.old_path, self.old_limit
        self.temp.cleanup()

    def test_explicit_record_has_actor_reason_time_and_container(self) -> None:
        AUDIT.record("ansible:roles/vecta-app/tasks/fleet_gateway_mypc.yml", "Recreate mypc Fleet gateway", "recreate", "openclaw-fleet-gateway")
        row = json.loads(self.log.read_text())
        self.assertEqual(row["actor"], "ansible:roles/vecta-app/tasks/fleet_gateway_mypc.yml")
        self.assertEqual(row["reason"], "Recreate mypc Fleet gateway")
        self.assertTrue(row["at"].endswith("Z"))
        self.assertEqual(row["container"], "openclaw-fleet-gateway")

    def test_docker_event_records_observer_and_event_reason(self) -> None:
        AUDIT.record_docker_event(json.dumps({
            "Action": "die", "timeNano": 123, "id": "abc", "Actor": {"Attributes": {"name": "openclaw-fleet-gateway"}},
        }))
        row = json.loads(self.log.read_text())
        self.assertEqual(row["actor"], "unknown")
        self.assertEqual(row["observer"], "docker-events-audit.service")
        self.assertEqual(row["reason"], "reason unknown; observed Docker event: die")
        self.assertEqual(row["container"], "openclaw-fleet-gateway")

    def test_rotation_keeps_bounded_history(self) -> None:
        for number in range(8):
            AUDIT.record("test", f"rotation {number}", "restart", "service")
        self.assertLessEqual(self.log.stat().st_size, AUDIT.MAX_BYTES)
        self.assertTrue(self.log.with_suffix(".jsonl.1").exists())

    def test_every_managed_container_task_has_prior_audit_intent(self) -> None:
        checked = 0
        for path in (ROOT / "roles").rglob("*.yml"):
            lines = path.read_text().splitlines()
            for index, line in enumerate(lines):
                if line.strip() != "community.docker.docker_container:":
                    continue
                task_start = max(i for i in range(index) if lines[i].startswith("- name:"))
                prior_start = max((i for i in range(task_start) if lines[i].startswith("- name:")), default=-1)
                prior_task = "\n".join(lines[prior_start + 1:task_start])
                self.assertTrue(
                    "tasks_from: record.yml" in prior_task
                    or "/usr/local/bin/vecta-container-audit" in prior_task,
                    f"{path}:{index + 1}",
                )
                self.assertTrue("container_audit_actor:" in prior_task or "--actor" in prior_task, f"{path}:{index + 1}")
                self.assertTrue("container_audit_reason:" in prior_task or "--reason" in prior_task, f"{path}:{index + 1}")
                checked += 1
        self.assertEqual(checked, 17)

    def test_event_recorder_is_installed_as_root_user_unit(self) -> None:
        tasks = (ROOT / "roles/container-audit/tasks/main.yml").read_text()
        self.assertIn("loginctl enable-linger root", tasks)
        self.assertIn("scope: user", tasks)
        self.assertIn("vecta-container-events-audit.service", tasks)
        self.assertIn("ExecStart=/usr/local/bin/vecta-container-audit events", tasks)

    def test_dedicated_playbook_only_installs_audit_on_mypc(self) -> None:
        playbook = (ROOT / "playbooks/container-audit.yml").read_text()
        self.assertIn("hosts: mypc", playbook)
        self.assertIn("role: container-audit", playbook)
        self.assertNotIn("role: vecta-app", playbook)

    def test_fleet_gateway_preparation_stop_is_audited(self) -> None:
        source = (ROOT / "roles/vecta-app/tasks/fleet_gateway_mypc.yml").read_text()
        intent = source.index("container_audit_reason: \"Stop Fleet gateway before preparing its audit volume\"")
        operation = source.index("argv: [docker, stop, \"{{ fleet_gateway_container_name }}\"]")
        self.assertLess(intent, operation)

    def test_container_roles_install_recorder_before_operations(self) -> None:
        for path in (
            "roles/infra-bootstrap/meta/main.yml",
            "roles/infra-services/meta/main.yml",
            "roles/mypc-nexus-adoption/meta/main.yml",
            "roles/vecta-app/meta/main.yml",
        ):
            self.assertIn("role: container-audit", (ROOT / path).read_text(), path)


if __name__ == "__main__":
    unittest.main()
