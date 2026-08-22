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
from unittest import mock


sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = ROOT / "deploy/fruit-v4/docker-compose.yml"
MIGRATION_COMPOSE_PATH = ROOT / "deploy/fruit-v4/docker-compose.migration.yml"
PROVENANCE_SCRIPT_PATH = ROOT / "scripts/validate_fruit_v4_image_provenance.py"
WORKFLOW_PATH = ROOT / ".github/workflows/build-mypc-images.yml"
RUNBOOK_PATH = ROOT / "docs/runbooks/fruit-v4-isolated-production-compose.md"

COMPOSE_COMMAND = ("docker", "compose", "--env-file", "/dev/null")


def read_current_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    head = result.stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{40}", head)
    return head


CURRENT_HEAD = read_current_head()

UAT_ENV = {
    "FRUIT_V4_IMAGE_REGISTRY": "registry.invalid:5000",
    "FRUIT_V4_IMAGE_DIGEST": "a" * 64,
    "FRUIT_V4_SOURCE_SHA": "b" * 40,
    "FRUIT_V4_INFRA_REVISION": CURRENT_HEAD,
    "FRUIT_V4_CANONICAL_NETWORK": "canonical-production-placeholder",
    "FRUIT_V4_RUNTIME_DATABASE_URL": (
        "postgresql://fruit_v4_runtime:placeholder@db.internal.invalid:5432/fruit_v4"
    ),
    "FRUIT_V4_WRITER_DATABASE_URL": (
        "postgresql://fruit_v4_writer:placeholder@db.internal.invalid:5432/fruit_v4"
    ),
    "FRUIT_V4_EXPECTED_DATABASE_HOST": "db.internal.invalid",
    "FRUIT_V4_EXPECTED_DATABASE_PORT": "5432",
    "FRUIT_V4_EXPECTED_DATABASE_PATH": "/fruit_v4",
    "FRUIT_V4_SERVICE_SECRET": "placeholder",
    "FRUIT_V4_ALLOWED_TENANT_IDS": "placeholder",
    "FRUIT_V4_ALLOWED_EMPLOYEE_IDS": "placeholder",
}

MIGRATION_ENV = {
    "FRUIT_V4_MIGRATION_DATABASE_URL": (
        "postgresql://fruit_v4_migration:placeholder@db.internal.invalid:5432/fruit_v4"
    ),
    "FRUIT_V4_RUNTIME_DB_ROLE": "fruit_v4_runtime",
    "FRUIT_V4_RUNTIME_DB_PASSWORD": "placeholder",
    "FRUIT_V4_WRITER_ROLE": "fruit_v4_writer",
    "FRUIT_V4_WRITER_PASSWORD": "placeholder",
    "FRUIT_V4_BACKUP_SHA256": "d" * 64,
    "FRUIT_V4_RESTORE_REHEARSAL_ID": "restore-rehearsal-placeholder",
    "FRUIT_V4_OPERATOR_APPROVAL_ID": "operator-approval-placeholder",
    "FRUIT_V4_MIGRATION_GATE": "approved-migration",
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


def run_uat_preflight(
    uat: dict[str, object],
    environment_updates: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {name: str(value) for name, value in uat["environment"].items()}
    )
    environment.update(environment_updates)
    return subprocess.run(
        uat["command"],
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
        RUNBOOK_PATH,
    ):
        assert path.exists(), f"missing Fruit V4 contract file: {path}"

    base_source = COMPOSE_PATH.read_text()
    migration_source = MIGRATION_COMPOSE_PATH.read_text()
    workflow = WORKFLOW_PATH.read_text()
    runbook = RUNBOOK_PATH.read_text()
    assert "env_file:" not in base_source + migration_source
    assert "fruit-v4-setup:" not in base_source
    for name in MIGRATION_ENV:
        assert name not in base_source, f"setup-only input leaked into base: {name}"
    assert "uses: actions/checkout@" not in workflow
    assert 'git -C "$target" checkout --detach --force "$GITHUB_SHA"' in workflow
    assert 'test "$(git -C "$target" rev-parse HEAD)" = "$GITHUB_SHA"' in workflow
    assert "INFRA_READ_TOKEN: ${{ github.token }}" in workflow
    assert "Validate Fruit V4 isolated Compose contract" in workflow
    assert "python3 scripts/test_fruit_v4_isolated_compose_contract.py" in workflow
    assert "approved-one-shot" not in base_source + migration_source + runbook
    assert "one-shot" not in runbook.lower()
    assert "profile is a Compose service selector only" in runbook
    assert "cannot atomically consume" in runbook
    assert "cannot prevent" in runbook

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
        "FRUIT_V4_WRITER_DATABASE_URL",
        "FRUIT_SERVICE_SECRET",
        "FRUIT_ALLOWED_TENANT_IDS",
        "FRUIT_ALLOWED_EMPLOYEE_IDS",
    ):
        assert name in uat["environment"]
    assert uat["environment"]["FRUIT_MODEL_PATH"] == (
        "/app/packages/fruit-industry-pack/model/fruit-model-v4.yaml"
    )
    assert uat["environment"]["FRUIT_CONTROLLED_ENTRY_ENABLED"] == "true"
    uat_command = "\n".join(uat["command"])
    assert "writer and runtime DSN usernames must differ" in uat_command
    assert "must not include query parameters or fragments" in uat_command
    overlapping_uat_identities = run_uat_preflight(
        uat,
        {"FRUIT_V4_WRITER_DATABASE_URL": UAT_ENV["FRUIT_V4_RUNTIME_DATABASE_URL"]},
    )
    assert overlapping_uat_identities.returncode != 0
    assert "writer and runtime DSN usernames must differ" in (
        overlapping_uat_identities.stderr
    )
    query_bearing_uat_dsn = run_uat_preflight(
        uat,
        {
            "DATABASE_URL": UAT_ENV["FRUIT_V4_RUNTIME_DATABASE_URL"]
            + "?host=unapproved.internal"
        },
    )
    assert query_bearing_uat_dsn.returncode != 0
    assert "must not include query parameters or fragments" in (
        query_bearing_uat_dsn.stderr
    )
    wrong_uat_endpoint = run_uat_preflight(
        uat,
        {
            "FRUIT_V4_WRITER_DATABASE_URL": (
                "postgresql://fruit_v4_writer:placeholder@"
                "unapproved.internal:5432/fruit_v4"
            )
        },
    )
    assert wrong_uat_endpoint.returncode != 0
    assert "does not match the approved database host/port/path" in (
        wrong_uat_endpoint.stderr
    )
    for name in (
        "FRUIT_RUNTIME_DB_ROLE",
        "FRUIT_RUNTIME_DB_PASSWORD",
        "FRUIT_V4_BACKUP_SHA256",
        "FRUIT_V4_OPERATOR_APPROVAL_ID",
    ):
        assert name not in uat["environment"]
    for missing in UAT_ENV:
        missing_values = UAT_ENV.copy()
        missing_values.pop(missing)
        rejected = run_config("--quiet", env=clean_env(missing_values))
        assert rejected.returncode != 0, f"missing {missing} did not fail closed"

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
        "FRUIT_V4_MIGRATION_DATABASE_URL"
    ]
    assert setup_environment["FRUIT_V4_RUNTIME_DATABASE_URL"] == UAT_ENV[
        "FRUIT_V4_RUNTIME_DATABASE_URL"
    ]
    assert setup_environment["FRUIT_V4_WRITER_DATABASE_URL"] == UAT_ENV[
        "FRUIT_V4_WRITER_DATABASE_URL"
    ]
    assert setup_environment["FRUIT_CONTROLLED_ENTRY_ENABLED"] == "true"
    assert setup_environment["FRUIT_V4_WRITER_ROLE"] == MIGRATION_ENV[
        "FRUIT_V4_WRITER_ROLE"
    ]
    assert setup_environment["FRUIT_V4_WRITER_PASSWORD"] == MIGRATION_ENV[
        "FRUIT_V4_WRITER_PASSWORD"
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
    query_bearing_setup_dsn = run_setup_preflight(
        setup,
        {
            "FRUIT_V4_RUNTIME_DATABASE_URL": UAT_ENV[
                "FRUIT_V4_RUNTIME_DATABASE_URL"
            ]
            + "?host=unapproved.internal"
        },
    )
    assert query_bearing_setup_dsn.returncode != 0
    assert "must not include query parameters or fragments" in (
        query_bearing_setup_dsn.stderr
    )

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
    writer_role_mismatch = run_setup_preflight(
        setup,
        {"FRUIT_V4_WRITER_ROLE": "different_writer_role"},
    )
    assert writer_role_mismatch.returncode != 0
    assert "writer DSN username must equal FRUIT_V4_WRITER_ROLE" in (
        writer_role_mismatch.stderr
    )
    writer_password_mismatch = run_setup_preflight(
        setup,
        {"FRUIT_V4_WRITER_PASSWORD": "different-password"},
    )
    assert writer_password_mismatch.returncode != 0
    assert "writer DSN password must equal FRUIT_V4_WRITER_PASSWORD" in (
        writer_password_mismatch.stderr
    )
    overlapping_runtime_writer = run_setup_preflight(
        setup,
        {
            "FRUIT_V4_WRITER_DATABASE_URL": UAT_ENV[
                "FRUIT_V4_RUNTIME_DATABASE_URL"
            ]
        },
    )
    assert overlapping_runtime_writer.returncode != 0
    assert "writer and runtime DSN usernames must differ" in (
        overlapping_runtime_writer.stderr
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
    wrong_gate = run_setup_preflight(
        setup,
        {"FRUIT_V4_MIGRATION_GATE": "approved-one-shot"},
    )
    assert wrong_gate.returncode != 0
    assert "FRUIT_V4_MIGRATION_GATE is not approved-migration" in wrong_gate.stderr

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
    invalid_revision_env = uat_env.copy()
    invalid_revision_env["FRUIT_V4_INFRA_REVISION"] = "not-a-sha"
    invalid_revision = subprocess.run(
        [sys.executable, str(PROVENANCE_SCRIPT_PATH), "--registry-only"],
        cwd=ROOT,
        env=invalid_revision_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert invalid_revision.returncode != 0
    assert "must be 40 lowercase hex characters" in invalid_revision.stderr
    different_revision_env = uat_env.copy()
    replacement = "0" if CURRENT_HEAD[0] != "0" else "1"
    different_revision_env["FRUIT_V4_INFRA_REVISION"] = (
        replacement + CURRENT_HEAD[1:]
    )
    different_revision = subprocess.run(
        [sys.executable, str(PROVENANCE_SCRIPT_PATH), "--registry-only"],
        cwd=ROOT,
        env=different_revision_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert different_revision.returncode != 0
    assert "does not match current checkout HEAD" in different_revision.stderr
    with mock.patch.object(
        provenance,
        "git_output",
        side_effect=[
            CURRENT_HEAD,
            " M deploy/fruit-v4/docker-compose.yml",
        ],
    ) as git_output_mock:
        assert_contract_error(
            provenance,
            lambda: provenance.validate_infra_checkout(CURRENT_HEAD),
        )
    assert git_output_mock.call_args_list == [
        mock.call("rev-parse", "HEAD"),
        mock.call(
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            *provenance.CONTRACT_PATHS,
        ),
    ]
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
        assert labels["com.vecta.deployment.revision"] == CURRENT_HEAD
        assert "org.opencontainers.image.source" not in labels

    print("fruit-v4 isolated Compose contract: ok")


if __name__ == "__main__":
    main()
