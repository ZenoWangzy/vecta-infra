#!/usr/bin/env python3
"""Executable safety contract for the Nexus third-party image sync script."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync-mypc-nexus-images.sh"
HERMES_TARGET = "nousresearch/hermes-agent:v2026.8.19-3811ed13"
HERMES_SOURCE = (
    "nousresearch/hermes-agent:v2026.8.19"
    "@sha256:3811ed13da874fba2ac99b6d492db9a203d34cb6dccf90d886948c00d0ccec09"
)


def run_selected_dry_run() -> str:
    with tempfile.TemporaryDirectory() as temporary:
        fake_bin = Path(temporary) / "bin"
        fake_bin.mkdir()
        docker = fake_bin / "docker"
        docker.write_text("#!/bin/sh\nexit 1\n")
        docker.chmod(0o755)
        environment = os.environ | {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "NEXUS_SYNC_ONLY": HERMES_TARGET,
        }
        result = subprocess.run(
            ["bash", str(SCRIPT), "--dry-run"],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
    assert result.returncode == 0, result.stderr
    return result.stdout


def main() -> None:
    syntax = subprocess.run(
        ["bash", "-n", str(SCRIPT)], text=True, capture_output=True, check=False
    )
    assert syntax.returncode == 0, syntax.stderr

    output = run_selected_dry_run()
    assert f"target 127.0.0.1:8082/{HERMES_TARGET}" in output
    assert f"+ docker pull {HERMES_SOURCE} " in output
    assert (
        f"+ docker tag {HERMES_SOURCE} 127.0.0.1:8082/{HERMES_TARGET} "
        in output
    )
    assert f"+ docker push 127.0.0.1:8082/{HERMES_TARGET} " in output
    assert "+ docker pull redis:7-alpine " not in output
    assert "+ docker pull vecta-hermes-withopenclaw:v2026.5.16 " not in output


if __name__ == "__main__":
    main()
