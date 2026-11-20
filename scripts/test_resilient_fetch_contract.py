#!/usr/bin/env python3
"""Behaviour and static contract for scripts/resilient-fetch.sh (every GitHub git fetch in mypc release)."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts/resilient-fetch.sh"
IDENTITY = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
TOKEN = "fake-token-606b"


def git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)


def run_script(
    url: str, sha: str, dest: Path, warm_cache: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "FETCH_TOKEN": TOKEN, "RESILIENT_FETCH_BACKOFF": "0", "GIT_TERMINAL_PROMPT": "0"}
    arguments = ["bash", str(SCRIPT_PATH), url, sha, str(dest)]
    if warm_cache is not None:
        arguments.append(str(warm_cache))
    return subprocess.run(
        arguments, env=env, text=True, capture_output=True, timeout=120,
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

        cached_dest = root / "cached-checkout"
        cached = run_script(f"file://{root / 'missing.git'}", sha, cached_dest, origin)
        assert cached.returncode == 0, f"warm-cache exact-SHA reproduction exit code: {cached.returncode}: {cached.stderr}"
        cached_head = git(["rev-parse", "HEAD"], cwd=cached_dest).stdout.strip()
        assert cached_head == sha, (cached_head, sha)
        print(f"warm-cache exact-SHA reproduction: HEAD={cached_head} (exit code: {cached.returncode})")

        missing_sha_cache = root / "missing-sha-cache.git"
        assert git(["init", "-q", "--bare", str(missing_sha_cache)]).returncode == 0
        assert git(["push", "-q", str(missing_sha_cache), f"{sha}:refs/heads/main"], cwd=seed).returncode == 0
        assert git([*IDENTITY, "commit", "-q", "--allow-empty", "-m", "next"], cwd=seed).returncode == 0
        next_sha = git(["rev-parse", "HEAD"], cwd=seed).stdout.strip()
        assert git(["push", "-q", str(origin), "HEAD:refs/heads/main"], cwd=seed).returncode == 0
        fallback_dest = root / "cache-miss-fallback"
        fallback = run_script(f"file://{origin}", next_sha, fallback_dest, missing_sha_cache)
        assert fallback.returncode == 0, fallback.stderr
        fallback_head = git(["rev-parse", "HEAD"], cwd=fallback_dest).stdout.strip()
        assert fallback_head == next_sha, (fallback_head, next_sha)
        print(f"warm-cache missing-SHA fallback: HEAD={fallback_head} (exit code: {fallback.returncode})")

        # Commit object present but a blob missing: cat-file passes, the cache fetch fails, and the
        # script must still fall back to GitHub instead of dying under `set -e`.
        (seed / "payload.txt").write_text("payload\n")
        assert git(["add", "payload.txt"], cwd=seed).returncode == 0
        assert git([*IDENTITY, "commit", "-q", "-m", "payload"], cwd=seed).returncode == 0
        blob_sha = git(["rev-parse", "HEAD:payload.txt"], cwd=seed).stdout.strip()
        payload_sha = git(["rev-parse", "HEAD"], cwd=seed).stdout.strip()
        assert git(["push", "-q", str(origin), "HEAD:refs/heads/main"], cwd=seed).returncode == 0
        broken_cache = root / "broken-cache.git"
        assert git(["init", "-q", "--bare", str(broken_cache)]).returncode == 0
        assert git(["push", "-q", str(broken_cache), f"{payload_sha}:refs/heads/main"], cwd=seed).returncode == 0
        (broken_cache / "objects" / blob_sha[:2] / blob_sha[2:]).unlink()
        assert git(["cat-file", "-e", f"{payload_sha}^{{commit}}"], cwd=broken_cache).returncode == 0
        broken_dest = root / "broken-cache-fallback"
        broken = run_script(f"file://{origin}", payload_sha, broken_dest, broken_cache)
        assert broken.returncode == 0, f"broken warm cache must fall back to GitHub: {broken.returncode}: {broken.stderr}"
        assert git(["rev-parse", "HEAD"], cwd=broken_dest).stdout.strip() == payload_sha
        assert "Using warm source cache" not in broken.stdout, broken.stdout
        print(f"warm-cache missing-blob fallback: HEAD={payload_sha} (exit code: {broken.returncode})")

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


GITHUB_URL = "https://github.com/ZenoWangzy/example.git"
LOCAL_PROXY, VPS_PROXY = "http://127.0.0.1:3129", "http://10.67.0.1:8888"
SHIM = """#!/bin/sh
printf '%s\\n' "$*" >> "$GIT_CALLS"
proxy=
for arg in "$@"; do
  case "$arg" in http.proxy=*) proxy=${arg#http.proxy=} ;; esac
  if [ "$arg" = ls-remote ]; then
    case " $STUB_REACHABLE " in *" $proxy "*) exit 0 ;; esac
    exit 1
  fi
done
exec 'REAL_GIT' "$@"
"""


def routing() -> None:
    """Route choice: local proxy, then VPS proxy, then direct. No test touches the network."""
    real_git = shutil.which("git")
    assert real_git
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        seed, origin = root / "seed", root / "origin.git"
        assert git(["init", "-q", "-b", "main", str(seed)]).returncode == 0
        assert git([*IDENTITY, "commit", "-q", "--allow-empty", "-m", "seed"], cwd=seed).returncode == 0
        sha = git(["rev-parse", "HEAD"], cwd=seed).stdout.strip()
        assert git(["init", "-q", "--bare", str(origin)]).returncode == 0
        assert git(["push", "-q", str(origin), "HEAD:refs/heads/main"], cwd=seed).returncode == 0
        # ls-remote is the probe: it succeeds only through the proxies listed in STUB_REACHABLE.
        # Every other git call runs for real, with the GitHub URL redirected to the local bare repo.
        shim_dir = root / "bin"
        shim_dir.mkdir()
        shim = shim_dir / "git"
        shim.write_text(SHIM.replace("REAL_GIT", real_git))
        shim.chmod(0o755)

        def run(reachable: str, name: str, *extra: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
            calls = root / f"{name}.calls"
            calls.write_text("")
            env = {
                **os.environ,
                "PATH": f"{shim_dir}:{os.environ['PATH']}",
                "FETCH_TOKEN": TOKEN, "RESILIENT_FETCH_BACKOFF": "0", "GIT_TERMINAL_PROMPT": "0",
                "GIT_CALLS": str(calls), "STUB_REACHABLE": reachable,
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": f"url.file://{origin}.insteadOf", "GIT_CONFIG_VALUE_0": GITHUB_URL,
            }
            args = extra or (GITHUB_URL, sha, str(root / name))
            result = subprocess.run(["bash", str(SCRIPT_PATH), *args], env=env, text=True, capture_output=True, timeout=120)
            return result, calls.read_text().splitlines()

        for reachable, expected, label in (
            (f"{LOCAL_PROXY} {VPS_PROXY}", LOCAL_PROXY, "local proxy first"),
            (VPS_PROXY, VPS_PROXY, "VPS proxy when local is down"),
        ):
            result, calls = run(reachable, label.replace(" ", "-"))
            assert result.returncode == 0, (label, result.stderr)
            fetches = [c for c in calls if " fetch " in f" {c} "]
            assert fetches and all(f"http.proxy={expected}" in c for c in fetches), (label, calls)
            host_port = expected.split("//")[1]
            assert host_port in result.stderr, (label, result.stderr)
            assert TOKEN not in result.stdout + result.stderr
            print(f"route {label}: fetch via {host_port} (exit code: {result.returncode})")

        result, calls = run("", "direct")
        assert result.returncode == 0, result.stderr
        fetches = [c for c in calls if " fetch " in f" {c} "]
        assert fetches and not any("http.proxy" in c for c in fetches), calls
        assert "direct" in result.stderr, result.stderr
        print(f"route direct when no proxy answers (exit code: {result.returncode})")

        # fetch-vecta-source.sh asks the same script for the route instead of keeping its own copy.
        result, _ = run(VPS_PROXY, "select-route", "--select-route", GITHUB_URL)
        assert result.returncode == 0 and result.stdout.strip() == VPS_PROXY, (result.stdout, result.stderr)
        result, _ = run("", "select-route-direct", "--select-route", GITHUB_URL)
        assert result.returncode == 0 and result.stdout.strip() == "", (result.stdout, result.stderr)
        print("route --select-route prints the chosen proxy, empty for direct")


def global_rewrite() -> None:
    """A global url.*.insteadOf to a stale local mirror (mypc includeIf cache bridge) must not hide origin."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        seed, origin, stale = root / "seed", root / "origin.git", root / "stale-mirror.git"
        assert git(["init", "-q", "-b", "main", str(seed)]).returncode == 0
        assert git([*IDENTITY, "commit", "-q", "--allow-empty", "-m", "seed"], cwd=seed).returncode == 0
        sha = git(["rev-parse", "HEAD"], cwd=seed).stdout.strip()
        assert git(["init", "-q", "--bare", str(origin)]).returncode == 0
        assert git(["push", "-q", str(origin), "HEAD:refs/heads/main"], cwd=seed).returncode == 0
        assert git(["init", "-q", "--bare", str(stale)]).returncode == 0
        global_config = root / "gitconfig"
        global_config.write_text(f'[url "file://{stale}"]\n\tinsteadOf = file://{origin}\n')
        dest = root / "checkout"
        env = {
            **os.environ, "GIT_CONFIG_GLOBAL": str(global_config),
            "FETCH_TOKEN": TOKEN, "RESILIENT_FETCH_BACKOFF": "0", "GIT_TERMINAL_PROMPT": "0",
        }
        # Sanity: the rewrite is live for a plain git command, so the script must be what bypasses it.
        assert git(["ls-remote", f"file://{origin}"]).returncode == 0
        plain = subprocess.run(["git", "ls-remote", f"file://{origin}"], env=env, text=True, capture_output=True)
        assert plain.stdout == "", "the global rewrite must hide origin from a plain git command"
        result = subprocess.run(
            ["bash", str(SCRIPT_PATH), f"file://{origin}", sha, str(dest)],
            env=env, text=True, capture_output=True, timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert git(["rev-parse", "HEAD"], cwd=dest).stdout.strip() == sha
        print(f"global insteadOf to a stale mirror bypassed: HEAD={sha} (exit code: {result.returncode})")


def static() -> None:
    source = SCRIPT_PATH.read_text()
    assert "for attempt in 1 2 3; do" in source
    assert "timeout 4m git \"${egress_args[@]}\" -C \"$dir\" fetch" in source
    assert "GIT_HTTP_LOW_SPEED_LIMIT=1024" in source and "GIT_HTTP_LOW_SPEED_TIME=60" in source
    assert 'trap \'rm -f "$askpass"\' EXIT' in source
    assert 'git -C "$warm_cache" cat-file -e "$sha^{commit}"' in source
    assert 'git -C "$dir" fetch -q --depth=1 "$warm_cache" "$sha"' in source
    assert "GIT_ASKPASS_REQUIRE" not in source, "git ignores GIT_ASKPASS_REQUIRE"
    assert not re.search(r"\bset\s+-x\b|\bxtrace\b", source)
    assert not re.search(r"\becho\b[^\n]*\$FETCH_TOKEN", source), "token must not be echoed"
    assert "GIT_TERMINAL_PROMPT=0" in source


def main() -> None:
    static()
    behaviour()
    routing()
    global_rewrite()
    print("resilient fetch contract: PASS")


if __name__ == "__main__":
    main()
