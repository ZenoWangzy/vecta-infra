#!/usr/bin/env python3
"""Executable contract for the gateway healthcheck overlay and the watch cron.

Ticket 106. Two things are pinned here:

1. `deploy/gateways/compose.healthchecks.yml` gives both gateways a healthcheck
   that probes their own /healthz and a rotating json-file log driver, and both
   survive a later release overlay that sets nothing but `image:` -- which is
   how every deploy window extends the `openclaw-enterprise` `-f` chain.
2. `scripts/ops/gateway-watch.sh` turns an unhealthy container or a grown
   RestartCount into exactly one admin alert row, in the column shape
   fleet-gateway's enqueueAdminAlertIfNeeded already writes, and writes nothing
   at all when the state has not changed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
OVERLAY_PATH = ROOT / "deploy/gateways/compose.healthchecks.yml"
WATCH_PATH = ROOT / "scripts/ops/gateway-watch.sh"
GROUP_VARS_PATH = ROOT / "inventories/mypc/group_vars/mypc.yml"
RUNBOOK_PATH = ROOT / "docs/runbooks/gateway-healthchecks-and-watch.md"

# Ports read off the live containers on 2026-09-05: fleet-gateway listens on
# 3000, channel-gateway on 9000. Neither image has curl; both have GNU wget,
# which exits non-zero on the 503 both /healthz routes can return.
GATEWAY_PORTS = {"channel-gateway": 9000, "fleet-gateway": 3000}
LOG_OPTIONS = {"max-size": "50m", "max-file": "5"}


def compose_config(files: list[Path], cwd: Path) -> dict:
    command = ["docker", "compose", "--env-file", "/dev/null"]
    for path in files:
        command += ["-f", str(path)]
    command += ["config", "--format", "json"]
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def assert_overlay_merges() -> None:
    assert shutil.which("docker"), "docker compose is required to read the merged overlay"
    with tempfile.TemporaryDirectory(prefix="gateway-healthcheck-contract-") as directory:
        workdir = Path(directory)
        base = workdir / "base.yml"
        base.write_text(
            "services:\n"
            + "".join(
                f"  {service}:\n    image: placeholder.invalid/{service}:base\n"
                for service in sorted(GATEWAY_PORTS)
            )
        )
        # Every release overlay in the live chain sets `image:` and nothing else.
        # Appending one after the healthcheck overlay is exactly what the next
        # deploy window does, and it must not take the healthcheck away.
        later_release = workdir / "compose.images.yml"
        later_release.write_text(
            "services:\n"
            + "".join(
                f"  {service}:\n    image: 127.0.0.1:8082/{service}:"
                + "0" * 40
                + "\n"
                for service in sorted(GATEWAY_PORTS)
            )
        )

        merged = compose_config([base, OVERLAY_PATH, later_release], workdir)["services"]
        for service, port in GATEWAY_PORTS.items():
            definition = merged[service]
            assert definition["image"].endswith("0" * 40), service

            healthcheck = definition["healthcheck"]
            probe = healthcheck["test"]
            assert probe[0] == "CMD", service
            assert "wget" in probe, service
            assert f"http://127.0.0.1:{port}/healthz" in probe, service
            assert healthcheck["interval"] == "30s", service
            assert healthcheck["retries"] == 3, service
            assert healthcheck["start_period"] == "30s", service
            assert not healthcheck.get("disable"), service

            logging = definition["logging"]
            assert logging["driver"] == "json-file", service
            for option, value in LOG_OPTIONS.items():
                assert logging["options"][option] == value, (service, option)

        # The overlay alone must not smuggle in an image, a command or a port:
        # it is a permanent member of a chain it does not own. Comments are not
        # keys, so they are dropped before looking.
        keys = [
            line
            for line in OVERLAY_PATH.read_text().splitlines()
            if not line.lstrip().startswith("#")
        ]
        for forbidden in ("image", "ports", "command", "environment", "volumes"):
            offenders = [line for line in keys if re.match(rf"\s*{forbidden}:", line)]
            assert not offenders, offenders


def assert_log_rotation_matches_ansible() -> None:
    """The same 50m/5 numbers exist in the Ansible vars for the non-live
    docker_container path (ticket 101). Two sources for one fact drift in
    silence, so pin them equal here."""
    group_vars = GROUP_VARS_PATH.read_text()
    for gateway in ("fleet_gateway", "channel_gateway"):
        block = re.search(
            rf"^{gateway}_log_options:\n((?:  .*\n)+)", group_vars, re.MULTILINE
        )
        assert block, gateway
        options = dict(
            re.findall(r"^  ([a-z-]+): \"?([^\"\n]+)\"?$", block.group(1), re.MULTILINE)
        )
        assert options == LOG_OPTIONS, (gateway, options)


def run_watch(workdir: Path, docker_output: str) -> tuple[int, str]:
    """Run gateway-watch.sh against a fake docker and a fake psql. Returns the
    exit code and everything the script tried to send to psql."""
    (workdir / "docker-state").write_text(docker_output)
    sql_log = workdir / "psql.sql"
    fake_docker = workdir / "fake-docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "ps" ]; then awk \'{print NR}\' "%s"; exit 0; fi\n'
        'if [ "$1" = "inspect" ]; then sed \'s,^,/,\' "%s"; exit 0; fi\n'
        "echo \"unexpected docker $*\" >&2; exit 99\n" % ((workdir / "docker-state",) * 2)
    )
    fake_docker.chmod(fake_docker.stat().st_mode | stat.S_IXUSR)

    result = subprocess.run(
        ["bash", str(WATCH_PATH)],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "GATEWAY_WATCH_DOCKER": str(fake_docker),
            "GATEWAY_WATCH_PSQL": f"tee -a {sql_log}",
            "GATEWAY_WATCH_STATE": str(workdir / "state"),
        },
    )
    assert result.returncode == 0, result.stderr
    return result.returncode, sql_log.read_text() if sql_log.exists() else ""


def assert_watch_alerts() -> None:
    healthy = (
        "openclaw-fleet-gateway healthy 0\n"
        "openclaw-channel-gateway healthy 0\n"
        "openclaw-postgres healthy 0\n"
    )
    unhealthy = healthy.replace("openclaw-channel-gateway healthy 0", "openclaw-channel-gateway unhealthy 0")
    restarted = healthy.replace("openclaw-fleet-gateway healthy 0", "openclaw-fleet-gateway healthy 3")

    with tempfile.TemporaryDirectory(prefix="gateway-watch-contract-") as directory:
        workdir = Path(directory)

        # Baseline: a first run on an all-healthy host says nothing.
        _, sql = run_watch(workdir, healthy)
        assert sql == "", sql

        # One container turns unhealthy -> exactly one row, in the shape
        # fleet-gateway's enqueueAdminAlertIfNeeded writes.
        _, sql = run_watch(workdir, unhealthy)
        assert sql.count("INSERT INTO proactive_outbox") == 1, sql
        assert "(instance_id, trigger_type, channel, channel_uid, prompt, max_attempts)" in sql
        assert "VALUES ('admin', 'system_alert', 'web', NULL, $gwwatch$" in sql
        assert "$gwwatch$, 6);" in sql
        assert "openclaw-channel-gateway" in sql
        assert "unhealthy" in sql
        assert "openclaw-fleet-gateway" not in sql, sql

        # Still unhealthy, nothing else changed -> no second row. This is the
        # dedupe: both signals are edge triggered.
        _, sql_again = run_watch(workdir, unhealthy)
        assert sql_again == sql, sql_again

        # RestartCount grows -> one more row, naming the delta.
        _, sql_after_restart = run_watch(workdir, restarted)
        added = sql_after_restart[len(sql):]
        assert added.count("INSERT INTO proactive_outbox") == 1, added
        assert "RestartCount 0" in added and "3" in added, added
        assert "openclaw-fleet-gateway" in added, added

        # And a repeat of that same state is silent again.
        _, sql_final = run_watch(workdir, restarted)
        assert sql_final == sql_after_restart, sql_final


def assert_watch_shape() -> None:
    assert WATCH_PATH.stat().st_mode & stat.S_IXUSR, "gateway-watch.sh must be executable"
    watch = WATCH_PATH.read_text()
    # The alert row is fleet-gateway's row, not a new shape.
    assert "INSERT INTO proactive_outbox" in watch
    assert "'admin', 'system_alert', 'web', NULL" in watch
    # Both facts come from one inspect, so they cannot disagree with each other.
    assert ".State.Health.Status" in watch
    assert ".RestartCount" in watch
    assert "docker exec -i openclaw-postgres psql -U openclaw_poc -d openclaw_poc" in watch


def assert_runbook() -> None:
    runbook = RUNBOOK_PATH.read_text()
    assert "谁在看生产" in runbook
    assert "compose.healthchecks.yml" in runbook
    assert "gateway-watch.sh" in runbook
    # The three crons that already exist on mypc, so the list is the whole list.
    assert "ip-monitor.sh" in runbook
    assert "ip-daily-report.sh" in runbook
    assert "channel-recent-conversations.sh" in runbook
    # Ticket 95's application-level alert belongs on the same list.
    assert "95" in runbook
    assert "com.docker.compose.project.config_files" in runbook


def main() -> None:
    assert_overlay_merges()
    assert_log_rotation_matches_ansible()
    assert_watch_shape()
    assert_watch_alerts()
    assert_runbook()
    print("gateway healthcheck + watch contract: ok")


if __name__ == "__main__":
    main()
