#!/usr/bin/env python3
"""Executable safety contract for the Hermes fleet backup and restore drill."""

import base64
import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
BACKUP = (ROOT / "scripts/hermes-fleet-state-backup.sh").read_text()
RESTORE = (ROOT / "scripts/hermes-fleet-restore-drill.sh").read_text()
BACKUP_PATH = ROOT / "scripts/hermes-fleet-state-backup.sh"
RESTORE_PATH = ROOT / "scripts/hermes-fleet-restore-drill.sh"


def write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def fixture_script(source: Path, root: Path) -> Path:
    path = root / source.name
    path.write_text(
        source.read_text().replace(
            "/data/ocee/backups", str((root / "backups").resolve())
        )
    )
    path.chmod(0o755)
    return path


def fleet_row(employee_id: str) -> str:
    encoded = base64.b64encode(employee_id.encode()).decode()
    return f"{encoded}|stopped|active|hermes|\n"


def backup_fixture_bin(root: Path, rows: str) -> Path:
    fake_bin = root / "bin"
    fake_bin.mkdir()
    rows_path = root / "fleet-rows.tsv"
    rows_path.write_text(rows)
    write_executable(
        fake_bin / "docker",
        """#!/bin/sh
set -eu
if [ "${1:-}" = exec ] && [ "${3:-}" = psql ]; then
  cat "$FAKE_ROWS"
  exit 0
fi
exit 1
""",
    )
    write_executable(
        fake_bin / "getfacl",
        """#!/bin/sh
printf '%s\\n' user:shiyao:rwx mask::rwx default:user:shiyao:rwx default:mask::rwx
""",
    )
    write_executable(fake_bin / "setfacl", "#!/bin/sh\nexit 0\n")
    write_executable(
        fake_bin / "realpath",
        """#!/bin/sh
set -eu
case "${1:-}" in
  -e|-m) shift ;;
esac
python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$1"
""",
    )
    return fake_bin


def run_backup_rejection(label: str, rows: str) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        backups = root / "backups"
        instances = root / "instances"
        backups.mkdir()
        instances.mkdir()
        script = fixture_script(BACKUP_PATH, root)
        fake_bin = backup_fixture_bin(root, rows)
        session_id = "hermes-fleet-20260909T000000Z"
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": f"{fake_bin}:{environment['PATH']}",
                "FAKE_ROWS": str(root / "fleet-rows.tsv"),
            }
        )
        result = subprocess.run(
            [
                "bash",
                str(script),
                "--execute",
                "--backup-root",
                str(backups),
                "--instance-root",
                str(instances),
                "--session-id",
                session_id,
            ],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode != 0, f"{label}: {result.stdout} {result.stderr}"
        assert not (backups / session_id).exists(), label
        assert not (backups / f".{session_id}.incomplete").exists(), label


def make_restore_fixture(
    root: Path,
    *,
    state_paths: str,
    with_regular_file: bool,
    with_symlink: bool = False,
) -> tuple[Path, Path, Path | None]:
    backup = root / "backups" / "hermes-fleet-20260909T000001Z"
    item = backup / "items" / "item-good"
    state = item / "state"
    state.mkdir(parents=True)
    (backup / "MANIFEST").write_text("session_id=fixture\n")
    (backup / "COMPLETE").write_text("complete\n")
    (backup / "fleet-rows.base64.tsv").write_text("Z29vZA==|stopped|active|hermes|\n")
    (item / "row.meta").write_text("employee_id_base64=Z29vZA==\n")
    (item / "state-paths.tsv").write_text(state_paths)
    outside = None
    if with_regular_file:
        (state / "payload.txt").write_text("payload\n")
    else:
        (state / "empty-a").mkdir()
        (state / "empty-b").mkdir()
    if with_symlink:
        outside = root / "outside.txt"
        outside.write_text("outside\n")
        os.symlink(outside, state / "external-link")
    return backup, state, outside


def run_restore_fixture(
    label: str,
    *,
    state_paths: str,
    with_regular_file: bool,
    with_symlink: bool = False,
    expect_success: bool = False,
) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "backups").mkdir()
        script = fixture_script(RESTORE_PATH, root)
        fake_bin = backup_fixture_bin(root, "")
        backup, state, outside = make_restore_fixture(
            root,
            state_paths=state_paths,
            with_regular_file=with_regular_file,
            with_symlink=with_symlink,
        )
        result = subprocess.run(
            ["bash", str(script), "--backup-dir", str(backup), "--execute"],
            cwd=ROOT,
            env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
            text=True,
            capture_output=True,
            check=False,
        )
        if expect_success:
            assert result.returncode == 0, f"{label}: {result.stdout} {result.stderr}"
            evidence = (backup / "RESTORE_DRILL").read_text()
            assert "status=success" in evidence
            assert "file_count=1" in evidence
            assert (state / "external-link").is_symlink() if with_symlink else True
            assert outside is not None and outside.read_text() == "outside\n" if with_symlink else True
        else:
            assert result.returncode != 0, f"{label}: {result.stdout} {result.stderr}"
            assert not (backup / "RESTORE_DRILL").exists(), label


def assert_executable_fixtures() -> None:
    for label, rows in (
        ("empty fleet rows", ""),
        ("empty employee id", fleet_row("")),
        ("dot employee id", fleet_row(".")),
        ("dotdot employee id", fleet_row("..")),
        ("newline employee id", fleet_row("bad\nid")),
        ("control employee id", fleet_row("bad\x01id")),
        ("outside whitelist employee id", fleet_row("bad/id")),
        ("duplicate employee id", fleet_row("same") + fleet_row("same")),
    ):
        run_backup_rejection(label, rows)

    run_restore_fixture(
        "empty state paths",
        state_paths="",
        with_regular_file=True,
    )
    run_restore_fixture(
        "two empty state directories",
        state_paths="config|/app/config\n",
        with_regular_file=False,
    )
    run_restore_fixture(
        "symlink is preserved without following",
        state_paths="config|/app/config\n",
        with_regular_file=True,
        with_symlink=True,
        expect_success=True,
    )


def main() -> None:
    for script in (BACKUP, RESTORE):
        assert "set -euo pipefail" in script
        assert "--execute" in script
        assert "eval " not in script
        assert "docker inspect " not in script or "--format" in script
        for forbidden in ("sha" + "256sum", "SHA" + "256SUMS", ".sha256"):
            assert forbidden not in script, forbidden

    for literal in (
        "user:shiyao:rwx",
        "default:user:shiyao:rwx",
        "setfacl -m u:shiyao:rwx,m::rwx,d:u:shiyao:rwx,d:m::rwx",
        "docker pause",
        "docker unpause",
        "unexpectedly paused container",
        "docker cp",
        "/data/openclaw",
        "/home/node/.openclaw",
        "/app/config",
        "/app/skills",
        "/app/plugins",
        "/opt/data",
        "fleet-rows.base64.tsv",
        'find "$staging_dir" -type f -print0',
        'test -r "$archive_file"',
        "for required_file in MANIFEST COMPLETE",
        'test -s "$staging_dir/$required_file"',
        ".incomplete",
    ):
        assert literal in BACKUP, literal

    assert "Config.Env" not in BACKUP
    assert 'rm -rf -- "$staging_dir"' in BACKUP
    for literal in (
        "for required_file in MANIFEST COMPLETE fleet-rows.base64.tsv",
        'test -s "$resolved_backup/$required_file"',
        'find "$resolved_backup/items"',
        'find "$resolved_backup" -type f -print0',
        'test -r "$archive_file"',
        "file_count=",
        'test -s "$resolved_backup/RESTORE_DRILL"',
        "status=success",
    ):
        assert literal in RESTORE, literal
    assert "diff -qr --no-dereference" in RESTORE
    assert "/data/ocee/backups/.hermes-restore-drill." in RESTORE
    assert "refusing unsafe restore-drill cleanup" in RESTORE

    assert_executable_fixtures()

    print("Hermes fleet backup contract: ok")


if __name__ == "__main__":
    main()
