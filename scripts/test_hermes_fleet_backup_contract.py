#!/usr/bin/env python3
"""Static safety contract for the Hermes fleet backup and restore drill."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKUP = (ROOT / "scripts/hermes-fleet-state-backup.sh").read_text()
RESTORE = (ROOT / "scripts/hermes-fleet-restore-drill.sh").read_text()


def main() -> None:
    for script in (BACKUP, RESTORE):
        assert "set -euo pipefail" in script
        assert "--execute" in script
        assert "eval " not in script
        assert "docker inspect " not in script or "--format" in script

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
        "sha256sum --check --quiet SHA256SUMS",
        ".incomplete",
    ):
        assert literal in BACKUP, literal

    assert "Config.Env" not in BACKUP
    assert "rm -rf" not in BACKUP
    assert "sha256sum --check --quiet SHA256SUMS" in RESTORE
    assert "diff -qr --no-dereference" in RESTORE
    assert "/data/ocee/backups/.hermes-restore-drill." in RESTORE
    assert "refusing unsafe restore-drill cleanup" in RESTORE

    print("Hermes fleet backup contract: ok")


if __name__ == "__main__":
    main()
