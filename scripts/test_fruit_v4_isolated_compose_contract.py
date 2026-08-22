#!/usr/bin/env python3
"""Executable contract for the isolated Fruit V4 production Compose seam."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys


sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = ROOT / "deploy/fruit-v4/docker-compose.yml"
MIGRATION_COMPOSE_PATH = ROOT / "deploy/fruit-v4/docker-compose.migration.yml"
PROVENANCE_SCRIPT_PATH = ROOT / "scripts/validate_fruit_v4_image_provenance.py"
WORKFLOW_PATH = ROOT / ".github/workflows/build-mypc-images.yml"

COMPOSE_COMMAND = ("docker", "compose", "--env-file", "/dev/null")

UAT_ENV = {
    "FRUIT_V4_IMAGE_REGISTRY": "registry.invalid:5000",
    "FRUIT_V4_IMAGE_DIGEST": "a" * 64,
    "FRUIT_V4_SOURCE_SHA": "b" * 40,
    "FRUIT_V4_INFRA_REVISION": "c" * 40,
    "FRUIT_V4_CANONICAL_NETWORK": "canonical-production-placeholder",
    "FRUIT_V4_RUNTIME_DATABASE_URL": (
        "postgresql://fruit_v4_runtime:placeholder@db.internal.invalid:5432/fruit_v4"
    ),
    "FRUIT_V4_SERVICE_SECRET": "placeholder",
    "FRUIT_V4_ALLOWED_TENANT_IDS": "placeholder",
    "FRUIT_V4_ALLOWED_EMPLOYEE_IDS": "placeholder",
}

MIGRATION_ENV = {
    "FRUIT_V4_WRITER_DATABASE_URL": (
        "postgresql://fruit_v4_writer:placeholder@db.internal.invalid:5432/fruit_v4"
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
}


def clean_env(values: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    for name in (*UAT_ENV, *MIGRATION_ENV):
        env.pop(name, None)
    env.update(values)
    return env


def run_config(
    *arguments: str,
    env: dict[str, str],
    migration: bool = False,
) -> subprocess.CompletedProcess[str]:
    files = ("-f", str(COMPOSE_PATH))
    profile: tuple[str, ...] = ()
    if migration:
        files += ("-f", str(MIGRATION_COMPOSE_PATH))
        profile = ("--profile", "migration")
    return subprocess.run(
        (*COMPOSE_COMMAND, *profile, *files, "config", *arguments),
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


def rendered_model(
    env: dict[str, str],
    *,
    migration: bool = False,
) -> dict[str, object]:
    rendered = run_config("--format", "json", env=env, migration=migration)
    assert_success(rendered, "docker compose config --format json")
    return json.loads(rendered.stdout)


def load_provenance_module() -> object:
    spec = importlib.util.spec_from_file_location(
        "validate_fruit_v4_image_provenance",
        PROVENANCE_SCRIPT_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_contract_error(provenance: object, operation: object) -> None:
    try:
        operation()
    except provenance.ContractError:
        return
    raise AssertionError("operation did not fail closed")


def run_setup_preflight(
    setup: dict[str, object],
    environment_updates: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {name: str(value) for name, value in setup["environment"].items()}
    )
    environment.update(environment_updates)
    return subprocess.run(
        setup["command"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def main() -> None:
    for path in (
        COMPOSE_PATH,
        MIGRATION_COMPOSE_PATH,
        PROVENANCE_SCRIPT_PATH,
        WORKFLOW_PATH,
    ):
        assert path.exists(), f"missing Fruit V4 contract file: {path}"

    base_source = COMPOSE_PATH.read_text()
    migration_source = MIGRATION_COMPOSE_PATH.read_text()
    workflow = WORKFLOW_PATH.read_text()
    assert "env_file:" not in base_source + migration_source
    assert "fruit-v4-setup:" not in base_source
    for name in MIGRATION_ENV:
        assert name not in base_source, f"setup-only input leaked into base: {name}"
    assert (
        "uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"
        in workflow
    )
    assert "persist-credentials: false" in workflow
    assert "Validate Fruit V4 isolated Compose contract" in workflow
    assert "python3 scripts/test_fruit_v4_isolated_compose_contract.py" in workflow

    uat_env = clean_env(UAT_ENV)
    base_quiet = run_config("--quiet", env=uat_env)
    assert_success(base_quiet, "UAT-only docker compose config --quiet")
    base = rendered_model(uat_env)
    assert set(base["services"]) == {"fruit-v4-uat"}
    uat = base["services"]["fruit-v4-uat"]

    expected_image = (
        f"registry.invalid:5000/fruit-industry-pack@sha256:{'a' * 64}"
    )
    assert uat["image"] == expected_image
    assert re.fullmatch(
        r"[^/@\s]+(?::\d+)?/fruit-industry-pack@sha256:[0-9a-f]{64}",
        uat["image"],
    )
    for forbidden in ("build", "ports", "volumes"):
        assert forbidden not in uat
    assert set(uat["networks"]) == {"production"}
    assert uat["networks"]["production"]["aliases"] == ["fruit-v4-isolated-uat"]
    assert uat["container_name"] == "fruit-v4-isolated-uat"
    assert uat["restart"] == "unless-stopped"
    assert "http://127.0.0.1:8002/healthz" in "\n".join(
        uat["healthcheck"]["test"]
    )
    assert set(base["networks"]) == {"production"}
    assert base["networks"]["production"]["external"] is True
    assert base["networks"]["production"]["name"] == (
        "canonical-production-placeholder"
    )
    for name in (
        "DATABASE_URL",
        "FRUIT_SERVICE_SECRET",
        "FRUIT_ALLOWED_TENANT_IDS",
        "FRUIT_ALLOWED_EMPLOYEE_IDS",
    ):
        assert name in uat["environment"]
    for name in (
        "FRUIT_RUNTIME_DB_ROLE",
        "FRUIT_RUNTIME_DB_PASSWORD",
        "FRUIT_V4_BACKUP_SHA256",
        "FRUIT_V4_OPERATOR_APPROVAL_ID",
    ):
        assert name not in uat["environment"]

    all_env_values = {**UAT_ENV, **MIGRATION_ENV}
    all_env = clean_env(all_env_values)
    migration_quiet = run_config("--quiet", env=all_env, migration=True)
    assert_success(migration_quiet, "migration docker compose config --quiet")
    combined = rendered_model(all_env, migration=True)
    assert set(combined["services"]) == {"fruit-v4-setup", "fruit-v4-uat"}
    setup = combined["services"]["fruit-v4-setup"]
    assert setup["image"] == uat["image"] == expected_image
    assert setup["profiles"] == ["migration"]
    assert setup["restart"] == "no"
    assert setup["container_name"] == "fruit-v4-isolated-setup"
    assert set(setup["networks"]) == {"production"}
    for forbidden in ("build", "ports", "volumes"):
        assert forbidden not in setup
    assert "depends_on" not in uat or "fruit-v4-setup" not in uat["depends_on"]

    for missing in MIGRATION_ENV:
        missing_values = all_env_values.copy()
        missing_values.pop(missing)
        rejected = run_config(
            "--quiet",
            env=clean_env(missing_values),
            migration=True,
        )
        assert rejected.returncode != 0, f"missing {missing} did not fail closed"

    setup_environment = setup["environment"]
    assert setup_environment["DATABASE_URL"] == MIGRATION_ENV[
        "FRUIT_V4_WRITER_DATABASE_URL"
    ]
    assert setup_environment["FRUIT_V4_RUNTIME_DATABASE_URL"] == UAT_ENV[
        "FRUIT_V4_RUNTIME_DATABASE_URL"
    ]
    for name in (
        "FRUIT_SERVICE_SECRET",
        "FRUIT_ALLOWED_TENANT_IDS",
        "FRUIT_ALLOWED_EMPLOYEE_IDS",
    ):
        assert name not in setup_environment

    setup_command = "\n".join(setup["command"])
    assert "new URL" in setup_command
    assert "decodeURIComponent" in setup_command
    assert "FRUIT_RUNTIME_DB_ROLE" in setup_command
    assert "FRUIT_RUNTIME_DB_PASSWORD" in setup_command
    assert "execFileSync" in setup_command
    assert "packages/fruit-industry-pack/dist/db/setup.js" in setup_command

    role_mismatch = run_setup_preflight(
        setup,
        {"FRUIT_RUNTIME_DB_ROLE": "different_runtime_role"},
    )
    assert role_mismatch.returncode != 0
    assert "runtime DSN username must equal FRUIT_RUNTIME_DB_ROLE" in (
        role_mismatch.stderr
    )
    password_mismatch = run_setup_preflight(
        setup,
        {"FRUIT_RUNTIME_DB_PASSWORD": "different-password"},
    )
    assert password_mismatch.returncode != 0
    assert "runtime DSN password must equal FRUIT_RUNTIME_DB_PASSWORD" in (
        password_mismatch.stderr
    )
    empty_runtime_password = run_setup_preflight(
        setup,
        {
            "FRUIT_V4_RUNTIME_DATABASE_URL": (
                "postgresql://fruit_v4_runtime@db.internal.invalid:5432/fruit_v4"
            )
        },
    )
    assert empty_runtime_password.returncode != 0
    assert "runtime DSN password is required" in empty_runtime_password.stderr

    provenance = load_provenance_module()
    registry_only = subprocess.run(
        [sys.executable, str(PROVENANCE_SCRIPT_PATH), "--registry-only"],
        cwd=ROOT,
        env=uat_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert_success(registry_only, "valid registry-only provenance preflight")
    invalid_registry_env = uat_env.copy()
    invalid_registry_env["FRUIT_V4_IMAGE_REGISTRY"] = "registry.invalid/path"
    invalid_registry = subprocess.run(
        [sys.executable, str(PROVENANCE_SCRIPT_PATH), "--registry-only"],
        cwd=ROOT,
        env=invalid_registry_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert invalid_registry.returncode != 0
    assert "must be host[:port]" in invalid_registry.stderr
    assert_contract_error(
        provenance,
        lambda: provenance.build_image_ref(
            "registry.invalid/path",
            UAT_ENV["FRUIT_V4_IMAGE_DIGEST"],
        ),
    )
    image_ref = provenance.build_image_ref(
        UAT_ENV["FRUIT_V4_IMAGE_REGISTRY"],
        UAT_ENV["FRUIT_V4_IMAGE_DIGEST"],
    )
    valid_inspection = {
        "Config": {
            "Labels": {
                "org.opencontainers.image.source": (
                    "https://github.com/ZenoWangzy/vecta"
                ),
                "org.opencontainers.image.revision": UAT_ENV["FRUIT_V4_SOURCE_SHA"],
            }
        },
        "RepoDigests": [image_ref],
    }
    provenance.validate_image_inspection(
        image_ref,
        UAT_ENV["FRUIT_V4_SOURCE_SHA"],
        valid_inspection,
    )
    for mismatch in (
        {"Config": {"Labels": {}}, "RepoDigests": [image_ref]},
        {
            **valid_inspection,
            "Config": {
                "Labels": {
                    **valid_inspection["Config"]["Labels"],
                    "org.opencontainers.image.source": (
                        "https://github.com/someone/other"
                    ),
                }
            },
        },
        {
            **valid_inspection,
            "Config": {
                "Labels": {
                    **valid_inspection["Config"]["Labels"],
                    "org.opencontainers.image.revision": "e" * 40,
                }
            },
        },
        {**valid_inspection, "RepoDigests": []},
    ):
        assert_contract_error(
            provenance,
            lambda value=mismatch: provenance.validate_image_inspection(
                image_ref,
                UAT_ENV["FRUIT_V4_SOURCE_SHA"],
                value,
            ),
        )

    for service in (setup, uat):
        labels = service["labels"]
        assert labels["com.vecta.expected.image.source.repository"] == (
            "ZenoWangzy/vecta"
        )
        assert labels["com.vecta.expected.image.source.revision"] == "b" * 40
        assert labels["com.vecta.deployment.repository"] == "ZenoWangzy/vecta-infra"
        assert labels["com.vecta.deployment.revision"] == "c" * 40
        assert "org.opencontainers.image.source" not in labels

    print("fruit-v4 isolated Compose contract: ok")


if __name__ == "__main__":
    main()
