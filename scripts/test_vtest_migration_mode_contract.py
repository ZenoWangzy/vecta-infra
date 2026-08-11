#!/usr/bin/env python3
"""Contracts for mutually exclusive and fail-closed vtest migration modes."""

from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
ROLE_MAIN = (ROOT / "roles/deploy-vtest/tasks/main.yml").read_text()
PREFLIGHT = (ROOT / "roles/deploy-vtest/tasks/preflight.yml").read_text()
MIGRATE = (ROOT / "roles/deploy-vtest/tasks/migrate.yml").read_text()
DEPLOY = (ROOT / "roles/deploy-vtest/tasks/deploy.yml").read_text()
MYPC_INVENTORY = (ROOT / "inventories/mypc/group_vars/mypc.yml").read_text()
WORKFLOW = (ROOT / ".github/workflows/_deploy-vtest-job.yml").read_text()
EXPLICIT_MIGRATION_HELPER = ROOT / "roles/deploy-vtest/files/apply-explicit-migration.sh"
MIGRATION_ROOT = Path("/tmp/migration-sqls")


def task_block(text: str, name: str) -> str:
    match = re.search(rf"^- name: {re.escape(name)}$", text, re.MULTILINE)
    if match is None:
        raise AssertionError(f"missing task: {name}")
    next_task = re.search(r"^- name: ", text[match.end() :], re.MULTILINE)
    end = len(text) if next_task is None else match.end() + next_task.start()
    return text[match.start() : end]


def workflow_step(name: str) -> str:
    start = WORKFLOW.index(f"      - name: {name}")
    end = WORKFLOW.find("\n      - ", start + 1)
    return WORKFLOW[start:] if end == -1 else WORKFLOW[start:end]


class VtestMigrationModeContractTest(unittest.TestCase):
    def test_explicit_files_select_raw_sql_only(self) -> None:
        mode = task_block(PREFLIGHT, "Preflight — select one migration mode")
        repos = task_block(PREFLIGHT, "Preflight — compose required Nexus image repo list")
        standard = task_block(MIGRATE, "Run standard vecta migrator image")
        explicit = task_block(MIGRATE, "Apply allowlisted explicit migrations")

        self.assertIn("vtest_explicit_migration_mode", mode)
        self.assertIn("vtest_migration_files | default('') | length > 0", mode)
        self.assertIn("not (vtest_explicit_migration_mode | bool)", repos)
        self.assertIn("when: not vtest_explicit_migration_mode | bool", standard)
        self.assertIn("when: vtest_explicit_migration_mode | bool", explicit)
        self.assertNotIn("openclaw-vecta-migrator", explicit)
        self.assertNotIn("Apply allowlisted migrations", DEPLOY)

    def test_no_files_keep_standard_migrator_and_ledger_guard(self) -> None:
        standard = task_block(MIGRATE, "Run standard vecta migrator image")
        ledger = task_block(PREFLIGHT, "Preflight — read migration ledger count")
        ledger_failure = task_block(PREFLIGHT, "Preflight — fail if migration ledger is unreadable")

        self.assertIn("image: \"{{ vecta_migrator_image }}\"", standard)
        self.assertIn("when: not vtest_explicit_migration_mode | bool", standard)
        for block in (ledger, ledger_failure):
            self.assertIn("not vtest_explicit_migration_mode | bool", block)

    def test_migration_writes_follow_quiesce_and_backup_before_deploy(self) -> None:
        self.assertLess(ROLE_MAIN.index("- import_tasks: quiesce.yml"), ROLE_MAIN.index("- import_tasks: backup.yml"))
        self.assertLess(ROLE_MAIN.index("- import_tasks: backup.yml"), ROLE_MAIN.index("- import_tasks: migrate.yml"))
        self.assertLess(ROLE_MAIN.index("- import_tasks: migrate.yml"), ROLE_MAIN.index("- import_tasks: deploy.yml"))

    def test_explicit_mode_is_fail_closed_and_production_remains_disabled(self) -> None:
        approval = task_block(PREFLIGHT, "Preflight — require approval for explicit migration files")
        explicit = task_block(MIGRATE, "Apply allowlisted explicit migrations")

        self.assertIn("not (vtest_explicit_migration_mode | bool)", approval)
        self.assertIn("vtest_allow_migrate | string == '1'", approval)
        self.assertIn("ansible.builtin.script:", explicit)
        self.assertIn("cmd: apply-explicit-migration.sh", explicit)
        self.assertIn("VTEST_MIGRATION_FILE: \"{{ vtest_migration_file | trim }}\"", explicit)
        self.assertIn("loop: \"{{ vtest_migration_files.split(',') }}\"", explicit)
        self.assertNotIn('<<< "{{ vtest_migration_files }}"', explicit)
        self.assertIn('vtest_allow_migrate: "0"', MYPC_INVENTORY)

    def test_explicit_migration_helper_rejects_unsafe_paths_before_docker(self) -> None:
        MIGRATION_ROOT.mkdir(exist_ok=True)
        self.assertFalse(MIGRATION_ROOT.is_symlink())

        with tempfile.TemporaryDirectory(prefix="vtest-migration-contract-") as temp_dir:
            temp_path = Path(temp_dir)
            fake_bin = temp_path / "bin"
            fake_bin.mkdir()
            marker = temp_path / "docker-called"
            injection_marker = temp_path / "injection-ran"
            fake_docker = fake_bin / "docker"
            fake_docker.write_text(
                '#!/bin/sh\nprintf called > "$DOCKER_MARKER"\ncat >/dev/null\n',
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)

            base_env = {
                **os.environ,
                "DOCKER_MARKER": str(marker),
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "VTEST_POSTGRES_DB": "openclaw_poc",
                "VTEST_POSTGRES_USER": "openclaw_poc",
            }
            invalid_paths = [
                str(MIGRATION_ROOT / "../outside.sql"),
                str(MIGRATION_ROOT / "bad'quote.sql"),
                str(MIGRATION_ROOT / 'bad"quote.sql'),
                str(MIGRATION_ROOT / "bad;touch.sql"),
                str(MIGRATION_ROOT / f"bad$(touch {injection_marker}).sql"),
            ]
            symlink_target = temp_path / "symlink-target.sql"
            symlink_target.write_text("select 1;\n", encoding="utf-8")
            symlink_file = MIGRATION_ROOT / f"contract-symlink-{os.getpid()}.sql"
            symlink_file.unlink(missing_ok=True)
            symlink_file.symlink_to(symlink_target)
            invalid_paths.append(str(symlink_file))

            try:
                for migration_path in invalid_paths:
                    with self.subTest(migration_path=migration_path):
                        marker.unlink(missing_ok=True)
                        result = subprocess.run(
                            ["/bin/bash", str(EXPLICIT_MIGRATION_HELPER)],
                            env={**base_env, "VTEST_MIGRATION_FILE": migration_path},
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        self.assertNotEqual(result.returncode, 0)
                        self.assertFalse(marker.exists(), result.stderr)
                        self.assertFalse(injection_marker.exists(), result.stderr)
            finally:
                symlink_file.unlink(missing_ok=True)

    def test_explicit_migration_helper_accepts_one_direct_regular_sql_file(self) -> None:
        MIGRATION_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="vtest-migration-contract-") as temp_dir:
            temp_path = Path(temp_dir)
            fake_bin = temp_path / "bin"
            fake_bin.mkdir()
            marker = temp_path / "docker-called"
            fake_docker = fake_bin / "docker"
            fake_docker.write_text(
                '#!/bin/sh\nprintf called > "$DOCKER_MARKER"\ncat >/dev/null\n',
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)
            migration_file = MIGRATION_ROOT / f"contract-{os.getpid()}.sql"
            migration_file.write_text("select 1;\n", encoding="utf-8")

            try:
                result = subprocess.run(
                    ["/bin/bash", str(EXPLICIT_MIGRATION_HELPER)],
                    env={
                        **os.environ,
                        "DOCKER_MARKER": str(marker),
                        "PATH": f"{fake_bin}:{os.environ['PATH']}",
                        "VTEST_MIGRATION_FILE": str(migration_file),
                        "VTEST_POSTGRES_DB": "openclaw_poc",
                        "VTEST_POSTGRES_USER": "openclaw_poc",
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(marker.exists())
            finally:
                migration_file.unlink(missing_ok=True)

    def test_each_run_discards_stale_migration_files_before_optional_download(self) -> None:
        reset = workflow_step("Reset staged migration SQL files")
        reset_at = WORKFLOW.index("      - name: Reset staged migration SQL files")
        download_at = WORKFLOW.index("      - name: Download migration SQL files")
        deploy_at = WORKFLOW.index("      - name: Deploy via Ansible")

        self.assertLess(reset_at, download_at)
        self.assertLess(download_at, deploy_at)
        self.assertIn("migration_dir=/tmp/migration-sqls", reset)
        self.assertIn('[ "$migration_dir" = "/tmp/migration-sqls" ]', reset)
        self.assertIn('rm -rf -- "$migration_dir"', reset)
        self.assertIn('mkdir -p -- "$migration_dir"', reset)
        self.assertIn("if: inputs.migration_artifact_run_id != ''", workflow_step("Download migration SQL files"))


if __name__ == "__main__":
    unittest.main()
