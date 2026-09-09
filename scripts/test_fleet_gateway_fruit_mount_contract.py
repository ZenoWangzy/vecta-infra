#!/usr/bin/env python3
"""Executable contract for the Fleet baked-Fruit runtime boundary.

The live enterprise Compose chain is generated under /data/ocee and is not
tracked in this repository. This test exercises the same Compose-resolved JSON
mount shape and the Ansible role's authoritative negative mount assertion; it
does not claim to inspect production.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
ROLE_PATH = ROOT / "roles/vecta-app/tasks/fleet_gateway_mypc.yml"
INVENTORY_PATH = ROOT / "inventories/mypc/group_vars/mypc.yml"
FRUIT_RUNTIME_TARGET = "/app/industry-packs/fruit"


def compose_config(files: list[Path]) -> dict[str, object]:
    command = ["docker", "compose", "--env-file", "/dev/null"]
    for path in files:
        command += ["-f", str(path)]
    command += ["config", "--format", "json"]
    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def fruit_host_binds(model: dict[str, object]) -> list[dict[str, object]]:
    services = model.get("services")
    assert isinstance(services, dict)
    fleet = services.get("fleet-gateway")
    assert isinstance(fleet, dict)
    mounts = fleet.get("volumes", [])
    assert isinstance(mounts, list)
    return [
        mount
        for mount in mounts
        if isinstance(mount, dict)
        and mount.get("type") == "bind"
        and mount.get("target") == FRUIT_RUNTIME_TARGET
    ]


def assert_no_fruit_host_bind(model: dict[str, object]) -> None:
    assert fruit_host_binds(model) == []


def assert_source_contract() -> None:
    inventory = INVENTORY_PATH.read_text()
    role = ROLE_PATH.read_text()

    assert "fleet_gateway_fruit_pack_host_path" not in inventory
    assert "fleet_gateway_fruit_pack_host_path" not in role
    preflight_start = role.index(
        "- name: Require the reviewed mypc Fleet mount contract"
    )
    recreate_start = role.index(
        "- name: Recreate mypc Fleet gateway from the selected Nexus image"
    )
    postcondition_start = role.index(
        "- name: Require the recreated mypc Fleet gateway has no Fruit host bind"
    )
    assert preflight_start < recreate_start < postcondition_start

    preflight = role[preflight_start:recreate_start]
    postcondition = role[postcondition_start:]
    fruit_bind_predicate = (
        '.Type == "bind" and .Destination == "/app/industry-packs/fruit"'
    )
    assert fruit_bind_predicate not in preflight
    assert fruit_bind_predicate in postcondition
    assert "--arg fruit " not in preflight
    assert "select(.Source == $fruit" not in preflight
    assert "fleet_gateway_instances_host_path" in preflight


def assert_regression_fixtures() -> None:
    assert shutil.which("docker"), (
        "docker compose is required for mount normalization"
    )
    with tempfile.TemporaryDirectory(prefix="fleet-fruit-mount-contract-") as directory:
        workdir = Path(directory)
        base = workdir / "base.yml"
        base.write_text(
            "services:\n"
            "  fleet-gateway:\n"
            "    image: placeholder.invalid/fleet-gateway:base\n"
        )

        fixtures = {
            "short.yml": (
                "services:\n"
                "  fleet-gateway:\n"
                "    volumes:\n"
                "      - /release/fruit:/app/industry-packs/fruit:ro\n"
            ),
            "long.yml": (
                "services:\n"
                "  fleet-gateway:\n"
                "    volumes:\n"
                "      - type: bind\n"
                "        source: /release/renamed-fruit\n"
                "        target: /app/industry-packs/fruit\n"
            ),
        }
        for name, source in fixtures.items():
            candidate = workdir / name
            candidate.write_text(source)
            rendered = compose_config([base, candidate])
            try:
                assert_no_fruit_host_bind(rendered)
            except AssertionError:
                continue
            raise AssertionError(f"{name} bypassed the Fruit bind contract")


def main() -> None:
    assert_source_contract()
    assert_regression_fixtures()
    print("fleet-gateway Fruit mount contract: ok")


if __name__ == "__main__":
    main()
