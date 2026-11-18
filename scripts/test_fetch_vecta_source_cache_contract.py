#!/usr/bin/env python3
"""Exercise main-history cache selection with unrelated shallow backup refs."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FETCH_SCRIPT = ROOT / "scripts" / "fetch-vecta-source.sh"
HISTORY_HELPER = ROOT / "scripts" / "vecta-main-history.sh"
GITHUB_SOURCE = "https://github.com/ZenoWangzy/vecta.git"


def run_git(*arguments: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *arguments],
        check=check,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def helper_result(cache: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash", "-c", 'source "$1" && vecta_main_history_is_complete "$2" refs/heads/main',
            "bash", str(HISTORY_HELPER), str(cache),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def make_fixture(root: Path) -> tuple[Path, Path, str]:
    seed = root / "seed"
    seed.mkdir()
    run_git("init", "-b", "main", str(seed))
    run_git("-C", str(seed), "config", "user.name", "Cache contract")
    run_git("-C", str(seed), "config", "user.email", "cache-contract@example.invalid")
    (seed / "source.txt").write_text("base\n")
    nested_file = seed / "nested" / "deeper" / "migration.sql"
    nested_file.parent.mkdir(parents=True)
    nested_file.write_text("select 1;\n")
    run_git("-C", str(seed), "add", "source.txt", "nested/deeper/migration.sql")
    run_git("-C", str(seed), "commit", "-m", "base")
    (seed / "source.txt").write_text("selected\n")
    run_git("-C", str(seed), "commit", "-am", "selected")
    selected_sha = run_git("-C", str(seed), "rev-parse", "HEAD")

    run_git("-C", str(seed), "checkout", "--orphan", "backup")
    (seed / "backup.txt").write_text("old rollback snapshot\n")
    run_git("-C", str(seed), "add", "backup.txt")
    run_git("-C", str(seed), "commit", "-m", "rollback snapshot")

    upstream = root / "upstream.git"
    run_git("init", "--bare", "-b", "main", str(upstream))
    run_git("-C", str(seed), "remote", "add", "origin", str(upstream))
    run_git("-C", str(seed), "push", "origin", "main", "backup")

    cache = root / "cache.git"
    subprocess.run(
        ["git", "clone", "--bare", "--branch=main", f"file://{upstream}", str(cache)],
        check=True,
        text=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git", f"--git-dir={cache}", "fetch", "--depth=1", "--quiet",
            f"file://{upstream}", "refs/heads/backup:refs/backups/pre-stale-main",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    return upstream, cache, selected_sha


def install_command_shims(root: Path, real_git: str) -> tuple[Path, Path]:
    fake_bin = root / "bin"
    fake_bin.mkdir()
    git_shim = fake_bin / "git"
    git_shim.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$GIT_CALLS\"\n"
        "fetching=false\n"
        "for arg in \"$@\"; do\n"
        "  [ \"$arg\" != fetch ] || fetching=true\n"
        f"  [ \"$arg\" != '{GITHUB_SOURCE}' ] || [ \"$fetching\" != true ] || exit 97\n"
        "done\n"
        f"exec '{real_git}' \"$@\"\n"
    )
    git_shim.chmod(0o755)

    curl_shim = fake_bin / "curl"
    curl_shim.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$CURL_CALLS\"\n"
        "printf '%s' \"$COMPARE_RESPONSE\"\n"
    )
    curl_shim.chmod(0o755)
    return fake_bin, git_shim


def run_fetch(
    root: Path,
    cache: Path,
    selected_sha: str,
    fake_bin: Path,
    calls_file: Path,
    curl_calls: Path,
    helper: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    invocation = root / f"run-{cache.name}"
    scripts_dir = invocation / "scripts"
    scripts_dir.mkdir(parents=True)
    script_text = FETCH_SCRIPT.read_text().replace(
        'cache="/home/github-runner/.cache/vecta-main.git"', f'cache="{cache}"'
    )
    assert 'cache="/home/github-runner/.cache/vecta-main.git"' not in script_text
    (scripts_dir / "fetch-vecta-source.sh").write_text(script_text)
    shutil.copyfile(helper, scripts_dir / "vecta-main-history.sh")

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "SOURCE_SHA": selected_sha,
            "SOURCE_BRANCH": "main",
            "VECTA_READ_TOKEN": "contract-token",
            "COMPARE_RESPONSE": (
                '{"status":"identical","base_commit":{"sha":"'
                f'{selected_sha}'
                '"},"merge_base_commit":{"sha":"'
                f'{selected_sha}'
                '"}}'
            ),
            "GIT_CALLS": str(calls_file),
            "CURL_CALLS": str(curl_calls),
            **(extra_env or {}),
        }
    )
    return subprocess.run(
        ["bash", str(scripts_dir / "fetch-vecta-source.sh"), "--with-main-history"],
        cwd=invocation,
        env=env,
        preexec_fn=lambda: os.umask(0o022),
        check=False,
        text=True,
        capture_output=True,
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix=".fetch-vecta-cache-contract-", dir=ROOT) as temporary:
        root = Path(temporary)
        upstream, cache, selected_sha = make_fixture(root)
        cache_git = lambda *args: subprocess.run(
            ["git", f"--git-dir={cache}", *args], check=True, text=True, capture_output=True
        ).stdout.strip()

        assert cache_git("rev-parse", "--is-shallow-repository") == "true"
        assert cache_git("for-each-ref", "--format=%(refname)", "refs/backups/") == (
            "refs/backups/pre-stale-main"
        )
        complete_cache = helper_result(cache)
        assert complete_cache.returncode == 0, complete_cache.stdout + complete_cache.stderr

        shallow_cache = root / "shallow-main.git"
        subprocess.run(
            [
                "git", "clone", "--bare", "--depth=1", "--branch=main",
                f"file://{upstream}", str(shallow_cache),
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        shallow_main = helper_result(shallow_cache)
        assert shallow_main.returncode == 1, shallow_main.stdout + shallow_main.stderr
        print("fetch-vecta-source main-history contract: a shallow main is rejected")

        real_git = shutil.which("git")
        assert real_git
        fake_bin, _ = install_command_shims(root, real_git)
        full_calls = root / "full-git-calls"
        full_curl_calls = root / "full-curl-calls"
        full = run_fetch(
            root, cache, selected_sha, fake_bin, full_calls, full_curl_calls, HISTORY_HELPER
        )
        assert full.returncode == 0, full.stdout + full.stderr
        assert "Using cached VectA main history" in full.stdout, full.stdout
        checkout = root / f"run-{cache.name}" / "vecta"
        nested_directory = checkout / "nested" / "deeper"
        nested_file = nested_directory / "migration.sql"
        directory_mode = nested_directory.stat().st_mode & 0o777
        file_mode = nested_file.stat().st_mode & 0o777
        assert directory_mode == 0o755, (
            f"expected nested directory mode 0755, got {directory_mode:04o}"
        )
        assert file_mode == 0o644, f"expected checked-out file mode 0644, got {file_mode:04o}"
        print("fetch-vecta-source checkout permissions contract: nested directory 0755, file 0644")
        full_github_fetches = [
            line for line in full_calls.read_text().splitlines()
            if "fetch" in line.split() and GITHUB_SOURCE in line
        ]
        assert not full_github_fetches, full_github_fetches
        assert (root / f"run-{cache.name}" / "vecta" / ".git").exists()
        assert run_git(
            "-C", str(root / f"run-{cache.name}" / "vecta"), "rev-parse", "HEAD"
        ) == selected_sha
        print("fetch-vecta-source main-history contract: complete main used cache, no GitHub source fetch")

        shallow_calls = root / "shallow-git-calls"
        shallow_curl_calls = root / "shallow-curl-calls"
        rejected = run_fetch(
            root,
            shallow_cache,
            selected_sha,
            fake_bin,
            shallow_calls,
            shallow_curl_calls,
            HISTORY_HELPER,
            extra_env={"VECTA_CACHE_WAIT_SECONDS": "0"},
        )
        assert rejected.returncode != 0, rejected.stdout + rejected.stderr
        assert "is stale or incomplete" in rejected.stderr, rejected.stderr
        shallow_github_fetches = [
            line for line in shallow_calls.read_text().splitlines()
            if "fetch" in line.split() and GITHUB_SOURCE in line
        ]
        assert shallow_github_fetches, shallow_calls.read_text()
        print("fetch-vecta-source main-history contract: shallow main rejected; GitHub fallback selected")
        test_publish_boundary_shapes(root / "publish-shapes")
        test_main_boundary_waits_for_warmer(root / "warm-wait")


def publish_depth_one(cache: Path, upstream: Path, refspec: str) -> None:
    # Same shape as vecta ci.yml "Publish validated source to the local source cache".
    subprocess.run(
        ["git", f"--git-dir={cache}", "fetch", "--depth=1", "--quiet", f"file://{upstream}", refspec],
        check=True,
        text=True,
        capture_output=True,
    )


def test_publish_boundary_shapes(root: Path) -> None:
    (root / "main").mkdir(parents=True)
    upstream, cache, _ = make_fixture(root / "main")
    publish_depth_one(cache, upstream, "+refs/heads/main:refs/heads/main")
    main_boundary = helper_result(cache)
    assert main_boundary.returncode == 1, main_boundary.stdout + main_boundary.stderr

    (root / "train").mkdir(parents=True)
    upstream, cache, _ = make_fixture(root / "train")
    publish_depth_one(cache, upstream, "+refs/heads/backup:refs/heads/train/b1")
    train_boundary = helper_result(cache)
    assert train_boundary.returncode == 0, train_boundary.stdout + train_boundary.stderr
    print("fetch-vecta-source publish-boundary contract: main boundary rejected, train boundary accepted")


def test_main_boundary_waits_for_warmer(root: Path) -> None:
    root.mkdir(parents=True)
    upstream, cache, selected_sha = make_fixture(root)
    publish_depth_one(cache, upstream, "+refs/heads/main:refs/heads/main")
    assert helper_result(cache).returncode == 1

    real_git = shutil.which("git")
    assert real_git
    fake_bin, _ = install_command_shims(root, real_git)
    calls = root / "wait-git-calls"
    curl_calls = root / "wait-curl-calls"

    def warm() -> None:
        # Mirrors warm-vecta-source-cache.sh: unshallow main from its upstream.
        subprocess.run(
            [
                "git", f"--git-dir={cache}", "fetch", "--unshallow", "--force", "--quiet",
                f"file://{upstream}", "refs/heads/main:refs/heads/main",
            ],
            check=True,
            text=True,
            capture_output=True,
        )

    warmer = threading.Timer(2.0, warm)
    warmer.start()
    result = run_fetch(
        root, cache, selected_sha, fake_bin, calls, curl_calls, HISTORY_HELPER,
        extra_env={"VECTA_CACHE_WAIT_SECONDS": "30", "VECTA_CACHE_WAIT_STEP": "1"},
    )
    warmer.join()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Using cached VectA main history" in result.stdout, result.stdout
    github_fetches = [
        line for line in calls.read_text().splitlines()
        if "fetch" in line.split() and GITHUB_SOURCE in line
    ]
    assert not github_fetches, github_fetches
    print("fetch-vecta-source warmer-wait contract: main boundary waited out, cache used, no GitHub fetch")


if __name__ == "__main__":
    main()
