#!/usr/bin/env python3
"""Behaviour and static contract for scripts/resilient-fetch.sh (every GitHub git fetch in mypc release)."""

import os
from pathlib import Path
import re
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts/resilient-fetch.sh"
IDENTITY = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
TOKEN = "fake-token-606b"


def git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)


def run_script(url: str, sha: str, dest: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "FETCH_TOKEN": TOKEN, "RESILIENT_FETCH_BACKOFF": "0", "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(
        ["bash", str(SCRIPT_PATH), url, sha, str(dest)], env=env, text=True, capture_output=True, timeout=120,
    )


def behaviour() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        seed, origin = root / "seed", root / "origin.git"
        assert git(["init", "-q", "-b", "main", str(seed)]).returncode == 0
        assert git([*IDENTITY, "commit", "-q", "--allow-empty", "-m", "seed"], cwd=seed).returncode == 0
        sha = git(["rev-parse", "HEAD"], cwd=seed).stdout.strip()
        assert git(["init", "-q", "--bare", str(origin)]).returncode == 0
        assert git(["push", "-q", str(origin), "HEAD:refs/heads/main"], cwd=seed).returncode == 0

        dest = root / "checkout"
        ok = run_script(f"file://{origin}", sha, dest)
        assert ok.returncode == 0, ok.stderr
        head = git(["rev-parse", "HEAD"], cwd=dest).stdout.strip()
        assert head == sha, (head, sha)
        assert git(["status", "--porcelain", "--untracked-files=all"], cwd=dest).stdout == ""

        bad_dest = root / "unreachable"
        failed = run_script(f"file://{root / 'missing.git'}", sha, bad_dest)
        assert failed.returncode != 0, "an unreachable remote must fail the step"
        assert "failed after 3 attempts" in failed.stderr, failed.stderr
        assert TOKEN not in failed.stdout + failed.stderr, "token must never reach the log"


def static() -> None:
    source = SCRIPT_PATH.read_text()
    assert "for attempt in 1 2 3; do" in source
    assert "timeout 4m git -C \"$dir\" fetch" in source
    assert "GIT_HTTP_LOW_SPEED_LIMIT=1024" in source and "GIT_HTTP_LOW_SPEED_TIME=60" in source
    assert 'trap \'rm -f "$askpass"\' EXIT' in source
    assert "GIT_ASKPASS_REQUIRE" not in source, "git ignores GIT_ASKPASS_REQUIRE"
    assert not re.search(r"\bset\s+-x\b|\bxtrace\b", source)
    assert not re.search(r"\becho\b[^\n]*\$FETCH_TOKEN", source), "token must not be echoed"
    assert "GIT_TERMINAL_PROMPT=0" in source


def main() -> None:
    static()
    behaviour()
    print("resilient fetch contract: PASS")


if __name__ == "__main__":
    main()
