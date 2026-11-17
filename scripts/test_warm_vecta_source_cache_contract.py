#!/usr/bin/env python3
"""Executable contracts for the Vecta source cache warmer.

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


def assert_full_history_cache_fetch() -> None:
    with tempfile.TemporaryDirectory(prefix=".warm-cache-contract-", dir=ROOT) as temporary:
        root = Path(temporary)
        source = root / "source"
        source.mkdir()

        def git(*arguments: str) -> str:
            return subprocess.run(
                ["git", "-c", "core.hooksPath=/dev/null", "-C", str(source), *arguments],
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()

        git("init", "-b", "main")
        git("config", "user.name", "Cache contract")
        git("config", "user.email", "cache-contract@example.invalid")
        (source / "source.txt").write_text("base\n")
        git("add", "source.txt")
        git("commit", "-m", "base")
        (source / "source.txt").write_text("selected\n")
        git("commit", "-am", "selected")
        selected_sha = git("rev-parse", "HEAD")

        cache = root / "cache.git"
        subprocess.run(
            ["git", "clone", "--bare", "--depth=1", f"file://{source}", str(cache)],
            check=True,
            text=True,
            capture_output=True,
        )
        initial_shallow = subprocess.run(
            ["git", f"--git-dir={cache}", "rev-parse", "--is-shallow-repository"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        assert initial_shallow == "true", initial_shallow

        subprocess.run(
            [
                "git", f"--git-dir={cache}", "fetch", "--unshallow", "--force", "--quiet",
                f"file://{source}", "refs/heads/main:refs/heads/main",
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        full_shallow = subprocess.run(
            ["git", f"--git-dir={cache}", "rev-parse", "--is-shallow-repository"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        commit_count = subprocess.run(
            ["git", f"--git-dir={cache}", "rev-list", "--count", "refs/heads/main"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        assert full_shallow == "false", full_shallow
        assert commit_count == "2", commit_count

        target = root / "target"
        subprocess.run(["git", "init", str(target)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(target), "fetch", "--depth=1", str(cache), selected_sha],
            check=True,
            text=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(target), "checkout", "--detach", selected_sha],
            check=True,
            text=True,
            capture_output=True,
        )
        assert (target / "source.txt").read_text() == "selected\n"

    print("warm-vecta-source-cache history: full main and build-style depth-1 fetch ok")


def main() -> None:
    script = SCRIPT.read_text()
    assert 'rev-parse --is-shallow-repository' in script
    assert 'history_args+=(--unshallow)' in script
    assert 'fetch "${history_args[@]}" --force --quiet "file://$OCEE/.git"' in script
    assert 'fetch --depth=1' not in script
    assert 'cache still reports shallow history' in script
    service = (ROOT / "scripts" / "warm-vecta-source-cache.service").read_text()
    assert "TimeoutStartSec=30min" in service
    assert_full_history_cache_fetch()

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
