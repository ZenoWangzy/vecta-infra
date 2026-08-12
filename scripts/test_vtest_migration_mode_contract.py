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
QUIESCE = (ROOT / "roles/deploy-vtest/tasks/quiesce.yml").read_text()
INSPECT_QUIESCE = (ROOT / "roles/deploy-vtest/tasks/inspect_quiesce.yml").read_text()
BACKUP = (ROOT / "roles/deploy-vtest/tasks/backup.yml").read_text()
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

    def test_quiesce_results_are_redacted_and_running_services_are_recovered_on_failure(self) -> None:
        inspect = task_block(INSPECT_QUIESCE, "Inspect write-capable app containers before migration")
        stop = task_block(QUIESCE, "Stop write-capable app containers before migration")
        restore_at = ROLE_MAIN.index("    - name: Restore write-capable app containers after failed deploy")
        audit_at = ROLE_MAIN.index("    - import_tasks: audit_failure.yml")
        restore = ROLE_MAIN[restore_at:audit_at]

        for block in (inspect, stop, restore):
            self.assertIn("no_log: true", block)
        self.assertIn("vtest_quiesce_containers.results | default([])", restore)
        self.assertIn("item.container.State.Running | default(false)", restore)
        self.assertIn("ignore_errors: true", restore)
        self.assertLess(restore_at, audit_at)

    def test_backup_selects_a_login_superuser_and_fails_closed_before_pg_dump(self) -> None:
        backup = task_block(BACKUP, "Backup vtest DB before any migrate")
        selection_at = backup.index("FROM pg_catalog.pg_roles")
        empty_guard_at = backup.index('[ -n "$backup_user" ]')
        dump_at = backup.index("pg_dump")

        self.assertIn("WHERE rolsuper AND rolcanlogin", backup)
        self.assertIn("ORDER BY (rolname = current_user) DESC, rolname", backup)
        self.assertLess(selection_at, empty_guard_at)
        self.assertLess(empty_guard_at, dump_at)
        self.assertIn('-U "$backup_user"', backup)
        self.assertIn('-d "$backup_database"', backup)

    def test_explicit_mode_is_fail_closed_and_production_remains_disabled(self) -> None:
        approval = task_block(PREFLIGHT, "Preflight — require approval for explicit migration files")
        explicit = task_block(MIGRATE, "Apply allowlisted explicit migrations")

        self.assertIn("not (vtest_explicit_migration_mode | bool)", approval)
        self.assertIn("vtest_allow_migrate | string == '1'", approval)
        self.assertIn("ansible.builtin.script:", explicit)
        self.assertIn("cmd: apply-explicit-migration.sh", explicit)
        self.assertIn('VTEST_MIGRATION_FILES: "{{ vtest_migration_files }}"', explicit)
        self.assertNotIn("loop:", explicit)
        self.assertNotIn("loop_control:", explicit)
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
                '#!/bin/sh\nprintf called >> "$DOCKER_MARKER"\ncat >/dev/null\n',
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)
            valid_file = MIGRATION_ROOT / f"contract-valid-{os.getpid()}.sql"
            valid_file.write_text("select 1;\n", encoding="utf-8")

            base_env = {
                **os.environ,
                "DOCKER_MARKER": str(marker),
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "VTEST_POSTGRES_DB": "openclaw_poc",
                "VTEST_POSTGRES_USER": "openclaw_poc",
            }
            invalid_paths = [
                "",
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
                            env={
                                **base_env,
                                "VTEST_MIGRATION_FILES": f"{valid_file},{migration_path}",
                            },
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        self.assertNotEqual(result.returncode, 0)
                        self.assertFalse(marker.exists(), result.stderr)
                        self.assertFalse(injection_marker.exists(), result.stderr)
            finally:
                valid_file.unlink(missing_ok=True)
                symlink_file.unlink(missing_ok=True)

    def test_explicit_migration_helper_materializes_all_files_then_runs_them_in_order(self) -> None:
        MIGRATION_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="vtest-migration-contract-") as temp_dir:
            temp_path = Path(temp_dir)
            fake_bin = temp_path / "bin"
            fake_bin.mkdir()
            calls = temp_path / "docker-calls"
            docker_args = temp_path / "docker-args"
            docker_stdin = temp_path / "docker-stdin"
            fake_docker = fake_bin / "docker"
            fake_docker.write_text(
                '#!/bin/sh\n'
                'printf "called\\n" >> "$DOCKER_CALLS"\n'
                'printf "%s\\n" "$@" >> "$DOCKER_ARGS"\n'
                'printf "%s\\n" --args-end-- >> "$DOCKER_ARGS"\n'
                'cat >> "$DOCKER_STDIN"\n'
                'printf "%s\\n" --stdin-end-- >> "$DOCKER_STDIN"\n'
                'if [ "$(wc -l < "$DOCKER_CALLS" | tr -d " ")" = 1 ]; then\n'
                '  rm -f -- "$SECOND_SOURCE"\n'
                'fi\n',
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)
            first_file = MIGRATION_ROOT / f"contract-first-{os.getpid()}.sql"
            second_file = MIGRATION_ROOT / f"contract-second-{os.getpid()}.sql"
            first_file.write_text("select 'first';\n", encoding="utf-8")
            second_file.write_text("select 'second';\n", encoding="utf-8")

            try:
                result = subprocess.run(
                    ["/bin/bash", str(EXPLICIT_MIGRATION_HELPER)],
                    env={
                        **os.environ,
                        "DOCKER_ARGS": str(docker_args),
                        "DOCKER_CALLS": str(calls),
                        "DOCKER_STDIN": str(docker_stdin),
                        "PATH": f"{fake_bin}:{os.environ['PATH']}",
                        "SECOND_SOURCE": str(second_file),
                        "VTEST_MIGRATION_FILES": f"{first_file},{second_file}",
                        "VTEST_POSTGRES_DB": "openclaw_poc",
                        "VTEST_POSTGRES_USER": "openclaw_poc",
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(calls.read_text().splitlines(), ["called", "called"])
                self.assertFalse(second_file.exists(), "first Docker call did not remove the second source")
                args = docker_args.read_text().splitlines()
                self.assertEqual(args.count("-X"), 2)
                self.assertEqual(args.count("--single-transaction"), 2)
                self.assertEqual(args.count("ON_ERROR_STOP=1"), 2)
                self.assertEqual(args.count("-f"), 2)
                self.assertEqual(args.count("-"), 2)
                stdin = docker_stdin.read_text()
                self.assertEqual(stdin.count("select 'first';"), 1)
                self.assertEqual(stdin.count("select 'second';"), 1)
                self.assertLess(stdin.index("select 'first';"), stdin.index("select 'second';"))
            finally:
                first_file.unlink(missing_ok=True)
                second_file.unlink(missing_ok=True)

    def test_explicit_migration_helper_copy_failure_stops_before_docker(self) -> None:
        MIGRATION_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="vtest-migration-contract-") as temp_dir:
            temp_path = Path(temp_dir)
            fake_bin = temp_path / "bin"
            fake_bin.mkdir()
            marker = temp_path / "docker-called"
            fake_docker = fake_bin / "docker"
            fake_docker.write_text(
                '#!/bin/sh\nprintf called >> "$DOCKER_MARKER"\ncat >/dev/null\n',
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)
            fake_cp = fake_bin / "cp"
            fake_cp.write_text(
                '#!/bin/sh\n'
                'if [ "$2" = "$FAIL_COPY_SOURCE" ]; then exit 72; fi\n'
                'exec /bin/cp "$@"\n',
                encoding="utf-8",
            )
            fake_cp.chmod(0o755)
            materialized_dir = temp_path / "materialized"
            fake_mktemp = fake_bin / "mktemp"
            fake_mktemp.write_text(
                '#!/bin/sh\nmkdir "$MATERIALIZED_DIR"\nprintf "%s\\n" "$MATERIALIZED_DIR"\n',
                encoding="utf-8",
            )
            fake_mktemp.chmod(0o755)
            first_file = MIGRATION_ROOT / f"contract-first-{os.getpid()}.sql"
            second_file = MIGRATION_ROOT / f"contract-second-{os.getpid()}.sql"
            first_file.write_text("select 'first';\n", encoding="utf-8")
            second_file.write_text("select 'second';\n", encoding="utf-8")

            try:
                result = subprocess.run(
                    ["/bin/bash", str(EXPLICIT_MIGRATION_HELPER)],
                    env={
                        **os.environ,
                        "DOCKER_MARKER": str(marker),
                        "FAIL_COPY_SOURCE": str(second_file.resolve()),
                        "MATERIALIZED_DIR": str(materialized_dir),
                        "PATH": f"{fake_bin}:{os.environ['PATH']}",
                        "VTEST_MIGRATION_FILES": f"{first_file},{second_file}",
                        "VTEST_POSTGRES_DB": "openclaw_poc",
                        "VTEST_POSTGRES_USER": "openclaw_poc",
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(marker.exists(), result.stderr)
                self.assertFalse(materialized_dir.exists(), "materialized files were not cleaned after failure")
            finally:
                first_file.unlink(missing_ok=True)
                second_file.unlink(missing_ok=True)

    def test_explicit_migration_helper_stops_after_first_psql_failure(self) -> None:
        MIGRATION_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="vtest-migration-contract-") as temp_dir:
            temp_path = Path(temp_dir)
            fake_bin = temp_path / "bin"
            fake_bin.mkdir()
            calls = temp_path / "docker-calls"
            docker_stdin = temp_path / "docker-stdin"
            fake_docker = fake_bin / "docker"
            fake_docker.write_text(
                '#!/bin/sh\n'
                'printf "called\\n" >> "$DOCKER_CALLS"\n'
                'cat >> "$DOCKER_STDIN"\n'
                'exit 23\n',
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)
            first_file = MIGRATION_ROOT / f"contract-first-{os.getpid()}.sql"
            second_file = MIGRATION_ROOT / f"contract-second-{os.getpid()}.sql"
            first_file.write_text("select 'first';\n", encoding="utf-8")
            second_file.write_text("select 'second';\n", encoding="utf-8")

            try:
                result = subprocess.run(
                    ["/bin/bash", str(EXPLICIT_MIGRATION_HELPER)],
                    env={
                        **os.environ,
                        "DOCKER_CALLS": str(calls),
                        "DOCKER_STDIN": str(docker_stdin),
                        "PATH": f"{fake_bin}:{os.environ['PATH']}",
                        "VTEST_MIGRATION_FILES": f"{first_file},{second_file}",
                        "VTEST_POSTGRES_DB": "openclaw_poc",
                        "VTEST_POSTGRES_USER": "openclaw_poc",
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 23)
                self.assertEqual(calls.read_text().splitlines(), ["called"])
                stdin = docker_stdin.read_text()
                self.assertIn("select 'first';", stdin)
                self.assertNotIn("select 'second';", stdin)
            finally:
                first_file.unlink(missing_ok=True)
                second_file.unlink(missing_ok=True)

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
        download = workflow_step("Download migration SQL files")
        self.assertIn("if: inputs.migration_artifact_run_id != ''", download)
        self.assertIn("name: migration-sqls", download)
        self.assertNotIn("run-id:", download)
        self.assertNotIn("github-token:", download)


if __name__ == "__main__":
    unittest.main()
