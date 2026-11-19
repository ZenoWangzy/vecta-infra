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

import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "warm-vecta-source-cache.sh"
HISTORY_HELPER = ROOT / "scripts" / "vecta-main-history.sh"

GUARD_START = "# Cross-user local fetch trips git's dubious-ownership guard"
GUARD_END = 'git config --global --add safe.directory "$OCEE/.git" 2>/dev/null || true\nfi\n'


def extract_guard() -> str:
    text = SCRIPT.read_text()
    start = text.index(GUARD_START)
    end = text.index(GUARD_END, start) + len(GUARD_END)
    return text[start:end]


def assert_installed_warmer_requires_helper() -> None:
    install_command = (
        "sudo install -m 0755 scripts/warm-vecta-source-cache.sh "
        "scripts/vecta-main-history.sh /usr/local/sbin/"
    )
    with tempfile.TemporaryDirectory(prefix=".warm-cache-installed-alone-", dir=ROOT) as temporary:
        installed = Path(temporary) / "usr/local/sbin/warm-vecta-source-cache.sh"
        installed.parent.mkdir(parents=True)
        installed.write_text(SCRIPT.read_text())

        result = subprocess.run(
            ["bash", str(installed)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        expected_helper = str(installed.parent / "vecta-main-history.sh")
        assert result.returncode == 1, result.stdout + result.stderr
        assert expected_helper in result.stderr, result.stderr
        assert install_command in result.stderr, result.stderr
        assert "No such file or directory" not in result.stderr, result.stderr
        print(
            "warm-vecta-source-cache installed-alone reproduction: "
            f"{result.stderr.strip()} (exit code: {result.returncode})"
        )
    print("warm-vecta-source-cache installed-alone guard: helper install diagnostic verified (exit code: 0)")


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


def assert_shallow_backup_ref_does_not_block_main_and_is_idempotent() -> None:
    with tempfile.TemporaryDirectory(prefix=".warm-cache-backups-", dir=ROOT) as temporary:
        root = Path(temporary)
        seed = root / "seed"
        seed.mkdir()

        def git(*arguments: str) -> str:
            return subprocess.run(
                ["git", "-c", "core.hooksPath=/dev/null", "-C", str(seed), *arguments],
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()

        git("init", "-b", "main")
        git("config", "user.name", "Cache contract")
        git("config", "user.email", "cache-contract@example.invalid")
        (seed / "source.txt").write_text("base\n")
        git("add", "source.txt")
        git("commit", "-m", "base")
        (seed / "source.txt").write_text("selected\n")
        git("commit", "-am", "selected")

        git("checkout", "--orphan", "backup")
        git("rm", "-rf", ".")
        (seed / "backup.txt").write_text("obsolete backup\n")
        git("add", "backup.txt")
        git("commit", "-m", "obsolete backup")

        upstream = root / "upstream.git"
        subprocess.run(
            ["git", "init", "--bare", "-b", "main", str(upstream)],
            check=True,
            text=True,
            capture_output=True,
        )
        git("remote", "add", "origin", str(upstream))
        git("push", "origin", "main")
        ocee = root / "ocee"
        subprocess.run(
            ["git", "clone", str(upstream), str(ocee)],
            check=True,
            text=True,
            capture_output=True,
        )

        cache = root / "cache.git"
        subprocess.run(
            ["git", "clone", "--bare", "--branch=main", f"file://{ocee}/.git", str(cache)],
            check=True,
            text=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git", f"--git-dir={cache}", "fetch", "--depth=1", "--quiet",
                f"file://{seed}/.git", "refs/heads/backup:refs/backups/pre-stale-main",
            ],
            check=True,
            text=True,
            capture_output=True,
        )

        def cache_git(*arguments: str) -> str:
            return subprocess.run(
                ["git", f"--git-dir={cache}", *arguments],
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()

        assert cache_git("rev-parse", "--is-shallow-repository") == "true"
        assert cache_git("for-each-ref", "--format=%(refname)", "refs/backups/") == (
            "refs/backups/pre-stale-main"
        )
        complete_before = subprocess.run(
            [
                "bash", "-c", 'source "$1" && vecta_main_history_is_complete "$2" refs/heads/main',
                "bash", str(HISTORY_HELPER), str(cache),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert complete_before.returncode == 0, complete_before.stdout + complete_before.stderr

        engine_seed, engine_origin = root / "engine-seed", root / "engine-origin.git"
        engine_cache = root / "engine-cache.git"
        subprocess.run(["git", "init", "-q", "-b", "main", str(engine_seed)], check=True)
        subprocess.run(["git", "-C", str(engine_seed), "config", "user.name", "Cache contract"], check=True)
        subprocess.run(["git", "-C", str(engine_seed), "config", "user.email", "cache-contract@example.invalid"], check=True)
        (engine_seed / "engine.txt").write_text("engine source\n")
        subprocess.run(["git", "-C", str(engine_seed), "add", "engine.txt"], check=True)
        subprocess.run(["git", "-C", str(engine_seed), "commit", "-q", "-m", "engine source"], check=True)
        engine_sha = subprocess.run(
            ["git", "-C", str(engine_seed), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        subprocess.run(["git", "init", "-q", "--bare", str(engine_origin)], check=True)
        subprocess.run(
            ["git", "-C", str(engine_seed), "push", "-q", str(engine_origin), "HEAD:refs/heads/main"],
            check=True,
        )

        # Run the real warmer and shared helper against disposable paths. The
        # sudo shim drops only its user switch; all Git work stays in this fixture.
        script_text = SCRIPT.read_text()
        script_text = script_text.replace("OCEE=/data/ocee", f"OCEE='{ocee}'")
        script_text = script_text.replace(
            "CACHE=/home/github-runner/.cache/vecta-main.git", f"CACHE='{cache}'"
        )
        script_text = script_text.replace(
            "ENGINE_CACHE=/home/github-runner/.cache/zenoforge-engine.git", f"ENGINE_CACHE='{engine_cache}'"
        )
        script_text = script_text.replace(
            "ENGINE_REMOTE=https://github.com/ZenoWangzy/zenoforge.git", f"ENGINE_REMOTE='file://{engine_origin}'"
        )
        assert "OCEE=/data/ocee" not in script_text
        assert "CACHE=/home/github-runner/.cache/vecta-main.git" not in script_text
        assert "ENGINE_CACHE=/home/github-runner/.cache/zenoforge-engine.git" not in script_text
        scripts_dir = root / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "vecta-main-history.sh").write_text(HISTORY_HELPER.read_text())
        warm_script = scripts_dir / "warm-vecta-source-cache.sh"
        warm_script.write_text(script_text)
        fake_bin = root / "bin"
        fake_bin.mkdir()
        sudo = fake_bin / "sudo"
        sudo.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = -u ]; then shift 2; fi\n"
            "exec \"$@\"\n"
        )
        sudo.chmod(0o755)
        global_config = root / "gitconfig"
        global_config.touch()
        environment = os.environ.copy()
        environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
        environment["GIT_CONFIG_GLOBAL"] = str(global_config)

        before_fix = subprocess.run(
            [
                "bash", "-c",
                'if [ "$(git --git-dir="$1" rev-parse --is-shallow-repository)" != false ]; '
                'then echo "FATAL: cache still reports shallow history after a successful fetch"; exit 1; fi',
                "bash", str(cache),
            ],
            env=environment,
            check=False,
            text=True,
            capture_output=True,
        )
        assert before_fix.returncode == 1, before_fix.stdout + before_fix.stderr
        assert "FATAL: cache still reports shallow history after a successful fetch" in before_fix.stdout
        print(
            "warm-vecta-source-cache before-fix reproduction: "
            "FATAL: cache still reports shallow history after a successful fetch (exit code: 1)"
        )

        for run_number in (1, 2):
            result = subprocess.run(
                ["bash", str(warm_script)],
                cwd=ROOT,
                env=environment,
                check=True,
                text=True,
                capture_output=True,
            )
            assert "cache warm: refs/heads/main = " in result.stdout, result.stdout
            assert "Zenoforge cache warm: refs/heads/main = " in result.stdout, result.stdout
            engine_tip = subprocess.run(
                ["git", f"--git-dir={engine_cache}", "rev-parse", "refs/heads/main"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            assert engine_tip == engine_sha, (engine_tip, engine_sha)
            engine_commit = subprocess.run(
                ["git", f"--git-dir={engine_cache}", "cat-file", "-e", f"{engine_sha}^{{commit}}"],
                check=False, capture_output=True, text=True,
            )
            assert engine_commit.returncode == 0, engine_commit.stderr
            if run_number == 1:
                warm_line = next(
                    line for line in result.stdout.splitlines()
                    if "cache warm: refs/heads/main = " in line
                )
                print(f"warm-vecta-source-cache after-fix reproduction: {warm_line} (exit code: 0)")
            assert cache_git("for-each-ref", "--format=%(refname)", "refs/backups/") == (
                "refs/backups/pre-stale-main"
            )
            assert cache_git("rev-list", "--count", "refs/heads/main") == "2"
            assert cache_git("rev-parse", "--is-shallow-repository") == "true"
            complete_after = subprocess.run(
                [
                    "bash", "-c", 'source "$1" && vecta_main_history_is_complete "$2" refs/heads/main',
                    "bash", str(HISTORY_HELPER), str(cache),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            assert complete_after.returncode == 0, complete_after.stdout + complete_after.stderr

            entries = subprocess.run(
                ["git", "config", "--file", str(global_config), "--get-all", "safe.directory"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.splitlines()
            assert entries.count(f"{ocee}/.git") == 1, (
                f"run {run_number}: expected one safe.directory entry, got {entries}"
            )

    print("warm-vecta-source-cache shallow backup ref: main stays complete and 2 runs are idempotent")


def main() -> None:
    script = SCRIPT.read_text()
    assert_installed_warmer_requires_helper()
    assert 'rev-parse --is-shallow-repository' in script
    assert 'vecta_main_history_is_complete "$2" refs/heads/main' in script
    assert 'history_args+=(--unshallow)' in script
    assert 'fetch "${history_args[@]}" --force --quiet "file://$OCEE/.git"' in script
    assert 'update-ref -d "$backup_ref"' not in script
    assert 'prune' not in script
    assert 'fetch --depth=1' not in script
    assert 'cache main history is incomplete after a successful fetch' in script
    assert 'sudo -u github-runner git --git-dir="$ENGINE_CACHE"' in script
    assert 'fetch "${engine_history_args[@]}" --force --quiet "$ENGINE_REMOTE"' in script
    assert 'trap log_cache_size EXIT' in script
    assert 'du -sh "$CACHE"' in script
    service = (ROOT / "scripts" / "warm-vecta-source-cache.service").read_text()
    assert "TimeoutStartSec=35min" in service
    assert_full_history_cache_fetch()

    guard = extract_guard()
    assert "--get-all safe.directory" in guard, (
        "guard must check before adding, not blindly --add -- ticket 136"
    )

    with tempfile.TemporaryDirectory(prefix=".safe-directory-contract-", dir=ROOT) as tmp:
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
    assert_shallow_backup_ref_does_not_block_main_and_is_idempotent()


if __name__ == "__main__":
    main()
