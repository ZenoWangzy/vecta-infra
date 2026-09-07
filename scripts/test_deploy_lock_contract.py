#!/usr/bin/env python3
"""Executable contract for scripts/deploy-lock.sh — ticket 140.

Runs entirely against a throwaway temp directory (DEPLOY_LOCK_DIR override);
never touches mypc or any real lock path. Exercises the four things ticket
140 requires be demonstrated, not just asserted:

1. A second holder is denied while the first is genuinely alive, and sees
   who holds it, what it's touching, and when it was acquired.
2. A crashed holder (killed, not released) is judged stale by the kernel's
   own flock state — not by a timestamp/TTL heuristic — and a live holder
   well inside its own ttl is never misjudged as stale.
3. A forgotten lock (ttl elapses, nobody calls release) frees itself.
4. Explicit release frees the lock immediately and removes its files.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "deploy-lock.sh"


def acquire(lock_dir: Path, holder: str, session: str, containers: str, ttl: int) -> subprocess.Popen[str]:
    env = os.environ | {"DEPLOY_LOCK_DIR": str(lock_dir)}
    proc = subprocess.Popen(
        [
            "bash", str(SCRIPT), "acquire",
            "--holder", holder,
            "--session", session,
            "--containers", containers,
            "--ttl-seconds", str(ttl),
        ],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Wait for the ACQUIRED/DENIED line so the caller never races the fork.
    line = proc.stdout.readline()  # type: ignore[union-attr]
    return proc, line


def run(lock_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ | {"DEPLOY_LOCK_DIR": str(lock_dir)}
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def reap(proc: subprocess.Popen[str], timeout: float = 3.0) -> None:
    # `proc` is this holder's direct OS parent (Popen never called wait()
    # yet), so a plain `kill -0` on its pid would see an unreaped zombie and
    # wrongly report it as still alive. Only the actual parent's wait()
    # both confirms exit and reaps it.
    proc.wait(timeout=timeout)


def test_denied_shows_current_holder() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        lock_dir = Path(tmp)
        holder, line = acquire(lock_dir, "agent-A", "session-AAA", "openclaw-fleet-gateway,openclaw-channel-gateway", ttl=10)
        assert line.startswith("ACQUIRED"), line
        try:
            denied = run(lock_dir, "acquire", "--holder", "agent-B", "--session", "session-BBB", "--containers", "openclaw-fleet-gateway")
            assert denied.returncode != 0
            assert "DENIED" in denied.stderr
            assert "holder=agent-A" in denied.stderr
            assert "session=session-AAA" in denied.stderr
            assert "containers=openclaw-fleet-gateway,openclaw-channel-gateway" in denied.stderr
            assert "acquired_at=" in denied.stderr
        finally:
            holder.kill()
            reap(holder)


def test_alive_holder_is_never_misjudged_as_stale() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        lock_dir = Path(tmp)
        holder, line = acquire(lock_dir, "agent-D", "session-DDD", "fruit-v4-isolated-uat", ttl=20)
        assert line.startswith("ACQUIRED"), line
        try:
            # holder.pid is still running (well inside its 20s ttl): a second
            # acquire must be denied, and `status` must report HELD.
            assert holder.poll() is None
            denied = run(lock_dir, "acquire", "--holder", "agent-E", "--session", "session-EEE", "--containers", "fruit-v4-isolated-uat")
            assert denied.returncode != 0
            status = run(lock_dir, "status")
            assert status.returncode != 0
            assert status.stdout.startswith("HELD"), status.stdout
        finally:
            holder.kill()
            reap(holder)


def test_crashed_holder_is_reclaimable_immediately_not_by_ttl() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        lock_dir = Path(tmp)
        # Long ttl on purpose: proves reclaim happens because the process
        # died, not because a timestamp/ttl expired. A real crash and a slow
        # 18-minute build look identical on a clock; they must not look
        # identical to this check.
        holder, line = acquire(lock_dir, "agent-A", "session-AAA", "openclaw-fleet-gateway", ttl=600)
        assert line.startswith("ACQUIRED"), line
        assert holder.poll() is None  # genuinely alive, nowhere near its ttl

        holder.kill()  # SIGKILL: no clean shutdown, no SIGHUP handler, no chance to release
        reap(holder)

        reclaim, rline = acquire(lock_dir, "agent-B", "session-BBB", "openclaw-fleet-gateway", ttl=5)
        try:
            assert rline.startswith("ACQUIRED"), rline
            assert "holder=agent-B" in rline
        finally:
            reclaim.kill()
            reap(reclaim)


def test_forgotten_lock_self_expires_via_ttl() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        lock_dir = Path(tmp)
        holder, line = acquire(lock_dir, "agent-C", "session-CCC", "fruit-v4-isolated-uat", ttl=1)
        assert line.startswith("ACQUIRED"), line
        # Nobody calls release. Wait past the ttl and let the process exit on
        # its own; the lock must free itself with no manual intervention.
        holder.wait(timeout=5)
        status = run(lock_dir, "status")
        assert status.returncode == 0
        assert status.stdout.startswith("FREE"), status.stdout


def test_explicit_release_frees_lock_and_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        lock_dir = Path(tmp)
        holder, line = acquire(lock_dir, "agent-A", "session-AAA", "openclaw-fleet-gateway", ttl=120)
        assert line.startswith("ACQUIRED"), line
        released = run(lock_dir, "release")
        assert released.returncode == 0
        assert "RELEASED" in released.stdout
        reap(holder)  # release kills the recorded pid
        status = run(lock_dir, "status")
        assert status.returncode == 0
        assert status.stdout.startswith("FREE (lock file does not exist yet")
        assert not (lock_dir / "production-deploy.lock").exists()
        assert not (lock_dir / "production-deploy.meta").exists()


def main() -> None:
    test_denied_shows_current_holder()
    test_alive_holder_is_never_misjudged_as_stale()
    test_crashed_holder_is_reclaimable_immediately_not_by_ttl()
    test_forgotten_lock_self_expires_via_ttl()
    test_explicit_release_frees_lock_and_files()


if __name__ == "__main__":
    main()
