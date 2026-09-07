#!/usr/bin/env python3
"""Executable contract for the safe.directory guard in
scripts/warm-vecta-source-cache.sh -- ticket 136.

`git config --global --add` is not idempotent: running it every ten minutes
(this script's own systemd timer, ticket 143) grows github-runner's shared
~/.gitconfig without bound -- observed going from 8 to 10 duplicate entries
during one session. This extracts the exact guard block from the real script
(so drift between the script and this test fails loudly) and runs it twice
against a throwaway git config file -- no sudo, no /data/ocee, no mypc --
proving the second run adds no new line.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "warm-vecta-source-cache.sh"

GUARD_START = "# Cross-user local fetch trips git's dubious-ownership guard"
GUARD_END = 'git config --global --add safe.directory "$OCEE/.git" 2>/dev/null || true\nfi\n'


def extract_guard() -> str:
    text = SCRIPT.read_text()
    start = text.index(GUARD_START)
    end = text.index(GUARD_END, start) + len(GUARD_END)
    return text[start:end]


def main() -> None:
    guard = extract_guard()
    assert "--get-all safe.directory" in guard, (
        "guard must check before adding, not blindly --add -- ticket 136"
    )

    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "gitconfig"
        config.touch()
        env = {"GIT_CONFIG_GLOBAL": str(config), "PATH": "/usr/bin:/bin", "OCEE": "/fake/ocee"}
        # sudo -u github-runner is stripped: this test runs as the current
        # user against a throwaway GIT_CONFIG_GLOBAL, not the real account.
        run_script = guard.replace("sudo -u github-runner ", "")

        for i in (1, 2):
            subprocess.run(["bash", "-c", run_script], env=env, check=True)
            entries = subprocess.run(
                ["git", "config", "--file", str(config), "--get-all", "safe.directory"],
                capture_output=True, text=True, check=True,
            ).stdout.splitlines()
            assert entries.count("/fake/ocee/.git") == 1, (
                f"run {i}: expected exactly one safe.directory entry, got {entries}"
            )

    print("warm-vecta-source-cache safe.directory guard: idempotent across 2 runs, ok")


if __name__ == "__main__":
    main()
