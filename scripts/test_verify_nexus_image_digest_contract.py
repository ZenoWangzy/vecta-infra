#!/usr/bin/env python3
"""Executable contract for digest verification through a Nexus Docker group."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "scripts" / "verify-nexus-image-digest.sh"
GROUP_TAG = "127.0.0.1:8083/nousresearch/hermes-agent:v2026.8.19-3811ed13"
EXPECTED_DIGEST = (
    "sha256:3811ed13da874fba2ac99b6d492db9a203d34cb6dccf90d886948c00d0ccec09"
)


def run_verifier(resolved_digest: str | None) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as temporary:
        fake_bin = Path(temporary) / "bin"
        fake_bin.mkdir()
        docker = fake_bin / "docker"
        payload = (
            '{"Descriptor":{"digest":"' + resolved_digest + '"}}'
            if resolved_digest is not None
            else '{}'
        )
        docker.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = manifest ] && [ \"$2\" = inspect ] && [ \"$3\" = --verbose ]; then\n"
            f"  printf '%s\\n' '{payload}'\n"
            "  exit 0\n"
            "fi\n"
            "exit 64\n"
        )
        docker.chmod(0o755)
        environment = os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}"}
        return subprocess.run(
            ["bash", str(VERIFY), GROUP_TAG, EXPECTED_DIGEST],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )


def main() -> None:
    matching = run_verifier(EXPECTED_DIGEST)
    assert matching.returncode == 0, matching.stderr

    shadowed = run_verifier("sha256:" + "f" * 64)
    assert shadowed.returncode != 0
    assert "Nexus digest mismatch" in shadowed.stderr

    malformed = run_verifier(None)
    assert malformed.returncode != 0
    assert "could not read a manifest digest" in malformed.stderr


if __name__ == "__main__":
    main()
