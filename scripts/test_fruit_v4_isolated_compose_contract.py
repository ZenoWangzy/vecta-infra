#!/usr/bin/env python3
"""Executable contract for the isolated Fruit V4 production Compose seam."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = ROOT / "deploy/fruit-v4/docker-compose.yml"
COMPOSE_COMMAND = (
    "docker",
    "compose",
    "--env-file",
    "/dev/null",
)

PLACEHOLDER_ENV = {
    "FRUIT_V4_IMAGE_REGISTRY": "registry.invalid",
    "FRUIT_V4_IMAGE_DIGEST": "a" * 64,
    "FRUIT_V4_SOURCE_SHA": "b" * 40,
    "FRUIT_V4_INFRA_REVISION": "c" * 40,
    "FRUIT_V4_CANONICAL_NETWORK": "canonical-production-placeholder",
    "FRUIT_V4_WRITER_DATABASE_URL": (
        "postgresql://fruit_v4_writer:placeholder@db.internal.invalid:5432/fruit_v4"
    ),
    "FRUIT_V4_RUNTIME_DATABASE_URL": (
        "postgresql://fruit_v4_runtime:placeholder@db.internal.invalid:5432/fruit_v4"
    ),
    "FRUIT_V4_EXPECTED_DATABASE_HOST": "db.internal.invalid",
    "FRUIT_V4_EXPECTED_DATABASE_PORT": "5432",
    "FRUIT_V4_EXPECTED_DATABASE_PATH": "/fruit_v4",
    "FRUIT_V4_RUNTIME_DB_ROLE": "fruit_v4_runtime",
    "FRUIT_V4_RUNTIME_DB_PASSWORD": "placeholder",
    "FRUIT_V4_BACKUP_SHA256": "d" * 64,
    "FRUIT_V4_RESTORE_REHEARSAL_ID": "restore-rehearsal-placeholder",
    "FRUIT_V4_OPERATOR_APPROVAL_ID": "operator-approval-placeholder",
    "FRUIT_V4_MIGRATION_GATE": "approved-one-shot",
    "FRUIT_V4_SERVICE_SECRET": "placeholder",
    "FRUIT_V4_ALLOWED_TENANT_IDS": "placeholder",
    "FRUIT_V4_ALLOWED_EMPLOYEE_IDS": "placeholder",
}


def compose_env() -> dict[str, str]:
    env = os.environ.copy()
    for name in PLACEHOLDER_ENV:
        env.pop(name, None)
    env.update(PLACEHOLDER_ENV)
    return env


def run_config(
    *arguments: str,
    env: dict[str, str],
    migration_profile: bool = True,
) -> subprocess.CompletedProcess[str]:
    profile = ("--profile", "migration") if migration_profile else ()
    return subprocess.run(
        (
            *COMPOSE_COMMAND,
            *profile,
            "-f",
            str(COMPOSE_PATH),
            "config",
            *arguments,
        ),
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def assert_success(result: subprocess.CompletedProcess[str], operation: str) -> None:
    assert result.returncode == 0, (
        f"{operation} failed with exit {result.returncode}:\n{result.stderr}"
    )


def main() -> None:
    assert COMPOSE_PATH.exists(), f"missing Compose contract: {COMPOSE_PATH}"
    assert "env_file:" not in COMPOSE_PATH.read_text()

    env = compose_env()
    quiet = run_config("--quiet", env=env)
    assert_success(quiet, "docker compose config --quiet")

    rendered = run_config("--format", "json", env=env)
    assert_success(rendered, "docker compose config --format json")
    model = json.loads(rendered.stdout)
    setup = model["services"]["fruit-v4-setup"]
    uat = model["services"]["fruit-v4-uat"]

    expected_image = f"registry.invalid/fruit-industry-pack@sha256:{'a' * 64}"
    assert setup["image"] == uat["image"] == expected_image
    assert re.fullmatch(
        r"[^:@\s]+(?::\d+)?/fruit-industry-pack@sha256:[0-9a-f]{64}",
        expected_image,
    )
    for service in (setup, uat):
        for forbidden in ("build", "ports", "volumes"):
            assert forbidden not in service, (service["container_name"], forbidden)
        assert set(service["networks"]) == {"production"}

    assert setup["profiles"] == ["migration"]
    assert "depends_on" not in uat or "fruit-v4-setup" not in uat["depends_on"]
    assert setup["container_name"] == "fruit-v4-isolated-setup"
    assert uat["container_name"] == "fruit-v4-isolated-uat"
    assert uat["networks"]["production"]["aliases"] == ["fruit-v4-isolated-uat"]
    default_services = run_config("--services", env=env, migration_profile=False)
    assert_success(default_services, "default docker compose config --services")
    assert default_services.stdout.splitlines() == ["fruit-v4-uat"]
    assert setup["restart"] == "no"
    assert uat["restart"] == "unless-stopped"
    assert "healthcheck" in uat
    assert "http://127.0.0.1:8002/healthz" in "\n".join(
        uat["healthcheck"]["test"]
    )
    assert set(model["networks"]) == {"production"}
    assert model["networks"]["production"]["name"] == (
        "canonical-production-placeholder"
    )
    assert model["networks"]["production"]["external"] is True

    for service in (setup, uat):
        labels = service["labels"]
        assert labels["com.vecta.source.repository"] == "ZenoWangzy/vecta"
        assert labels["com.vecta.source.revision"] == "b" * 40
        assert labels["com.vecta.deployment.repository"] == "ZenoWangzy/vecta-infra"
        assert labels["com.vecta.deployment.revision"] == "c" * 40
        assert labels["org.opencontainers.image.source"] == (
            "https://github.com/ZenoWangzy/vecta"
        )

    setup_environment = setup["environment"]
    uat_environment = uat["environment"]
    for name in (
        "DATABASE_URL",
        "FRUIT_V4_RUNTIME_DATABASE_URL",
        "FRUIT_V4_EXPECTED_DATABASE_HOST",
        "FRUIT_V4_EXPECTED_DATABASE_PORT",
        "FRUIT_V4_EXPECTED_DATABASE_PATH",
        "FRUIT_V4_SOURCE_SHA",
        "FRUIT_V4_IMAGE_DIGEST",
        "FRUIT_V4_BACKUP_SHA256",
        "FRUIT_V4_RESTORE_REHEARSAL_ID",
        "FRUIT_V4_OPERATOR_APPROVAL_ID",
        "FRUIT_V4_MIGRATION_GATE",
    ):
        assert name in setup_environment
    for name in (
        "FRUIT_SERVICE_SECRET",
        "FRUIT_ALLOWED_TENANT_IDS",
        "FRUIT_ALLOWED_EMPLOYEE_IDS",
    ):
        assert name not in setup_environment
        assert name in uat_environment
    assert setup_environment["DATABASE_URL"] == PLACEHOLDER_ENV[
        "FRUIT_V4_WRITER_DATABASE_URL"
    ]
    assert uat_environment["DATABASE_URL"] == PLACEHOLDER_ENV[
        "FRUIT_V4_RUNTIME_DATABASE_URL"
    ]
    for name in ("FRUIT_RUNTIME_DB_ROLE", "FRUIT_RUNTIME_DB_PASSWORD"):
        assert name not in uat_environment

    setup_command = "\n".join(setup["command"])
    assert "new URL" in setup_command
    assert "FRUIT_V4_EXPECTED_DATABASE_HOST" in setup_command
    assert "approved-one-shot" in setup_command
    assert "execFileSync" in setup_command
    assert "packages/fruit-industry-pack/dist/db/setup.js" in setup_command
    assert uat["command"] == ["node", "packages/fruit-industry-pack/dist/cli.js"]

    for missing in PLACEHOLDER_ENV:
        missing_env = env.copy()
        missing_env.pop(missing)
        rejected = run_config("--quiet", env=missing_env)
        assert rejected.returncode != 0, f"missing {missing} did not fail closed"

    print("fruit-v4 isolated Compose contract: ok")


if __name__ == "__main__":
    main()
