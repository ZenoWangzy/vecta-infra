#!/usr/bin/env python3
"""Append explicit container audit records and Docker daemon events."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path("/root/.vecta-agent/container-operations.jsonl")
MAX_BYTES = 10 * 1024 * 1024
BACKUPS = 4
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,254}$")
TEXT = re.compile(r"^[^\r\n\x00]{1,500}$")
EVENT_ACTIONS = {
    "create", "destroy", "die", "kill", "oom", "pause", "restart",
    "start", "stop", "unpause", "update",
}


def append_event(event: dict[str, object]) -> None:
    LOG_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(LOG_PATH.parent, 0o700)
    line = (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode()
    with LOG_PATH.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if LOG_PATH.exists() and LOG_PATH.stat().st_size + len(line) > MAX_BYTES:
            oldest = LOG_PATH.with_suffix(f".jsonl.{BACKUPS}")
            oldest.unlink(missing_ok=True)
            for number in range(BACKUPS - 1, 0, -1):
                source = LOG_PATH.with_suffix(f".jsonl.{number}")
                if source.exists():
                    source.replace(LOG_PATH.with_suffix(f".jsonl.{number + 1}"))
            LOG_PATH.replace(LOG_PATH.with_suffix(".jsonl.1"))
        fd = os.open(LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def record(actor: str, reason: str, operation: str, container: str) -> None:
    if not TEXT.fullmatch(actor) or not TEXT.fullmatch(reason):
        raise ValueError("actor and reason must be non-empty single-line text of at most 500 characters")
    if not NAME.fullmatch(operation) or not NAME.fullmatch(container):
        raise ValueError("operation and container contain invalid characters")
    append_event({
        "at": timestamp(),
        "actor": actor,
        "reason": reason,
        "operation": operation,
        "container": container,
        "source": "explicit",
    })


def record_docker_event(raw: str) -> None:
    docker_event = json.loads(raw)
    action = docker_event.get("Action") or docker_event.get("status")
    attributes = docker_event.get("Actor", {}).get("Attributes", {})
    container = attributes.get("name") or docker_event.get("id")
    if action not in EVENT_ACTIONS or not container:
        return
    append_event({
        "at": timestamp(),
        "docker_time": docker_event.get("timeNano") or docker_event.get("time"),
        "actor": "unknown",
        "observer": "docker-events-audit.service",
        "reason": f"reason unknown; observed Docker event: {action}",
        "operation": action,
        "container": container,
        "container_id": docker_event.get("id"),
        "source": "docker-event",
    })


def record_events() -> int:
    process = subprocess.Popen(
        ["docker", "events", "--filter", "type=container", "--format", "{{json .}}"],
        text=True,
        stdout=subprocess.PIPE,
    )
    assert process.stdout is not None
    try:
        for raw in process.stdout:
            try:
                record_docker_event(raw)
            except (json.JSONDecodeError, OSError, ValueError) as exc:
                print(f"vecta-container-audit: dropped malformed event: {exc}", file=sys.stderr)
    finally:
        process.stdout.close()
    return process.wait()


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("events")
    record_parser = commands.add_parser("record")
    for flag in ("actor", "reason", "operation", "container"):
        record_parser.add_argument(f"--{flag}", required=True)
    args = parser.parse_args()
    try:
        if args.command == "events":
            return record_events()
        record(args.actor, args.reason, args.operation, args.container)
    except (OSError, ValueError) as exc:
        print(f"vecta-container-audit: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
