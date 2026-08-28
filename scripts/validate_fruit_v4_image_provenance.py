#!/usr/bin/env python3
"""Fail-closed validation for a pulled Fruit V4 immutable image."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import subprocess
import sys
from pathlib import Path


EXPECTED_SOURCE = "https://github.com/ZenoWangzy/vecta"
IMAGE_REPOSITORY = "fruit-industry-pack"
MIGRATION_PATH_RE = re.compile(
    r"packages/fruit-industry-pack/migrations/[0-9]{4}_[a-z0-9_]+\.sql"
)
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATHS = (
    ".github/workflows/build-mypc-images.yml",
    "deploy/fruit-v4/docker-compose.yml",
    "deploy/fruit-v4/docker-compose.migration.yml",
    "docs/runbooks/fruit-v4-isolated-production-compose.md",
    "scripts/test_build_mypc_images_contract.py",
    "scripts/test_fruit_v4_isolated_compose_contract.py",
    "scripts/validate_fruit_v4_image_provenance.py",
)
HEX_40 = re.compile(r"^[0-9a-f]{40}$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class ContractError(RuntimeError):
    """The supplied release or pulled image violates the deployment contract."""


def required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value or value.strip() != value:
        raise ContractError(f"{name} is required")
    return value


def git_output(*arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ContractError("Fruit V4 contract checkout git inspection failed")
    return result.stdout.strip()


def validate_infra_checkout(infra_revision: str) -> None:
    if HEX_40.fullmatch(infra_revision) is None:
        raise ContractError(
            "FRUIT_V4_INFRA_REVISION must be 40 lowercase hex characters"
        )

    current_head = git_output("rev-parse", "HEAD")
    if HEX_40.fullmatch(current_head) is None:
        raise ContractError("current checkout HEAD is not a full lowercase Git SHA")
    if infra_revision != current_head:
        raise ContractError(
            "FRUIT_V4_INFRA_REVISION does not match current checkout HEAD"
        )

    dirty_contract_paths = git_output(
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        *CONTRACT_PATHS,
    )
    if dirty_contract_paths:
        raise ContractError(
            "Fruit V4 contract checkout has dirty tracked or untracked files"
        )


def validate_registry(registry: str) -> None:
    if (
        not registry
        or registry.strip() != registry
        or any(character in registry for character in ("/", "@"))
        or "://" in registry
    ):
        raise ContractError("FRUIT_V4_IMAGE_REGISTRY must be host[:port]")

    host: str
    port: str | None
    if registry.startswith("["):
        match = re.fullmatch(r"\[([^\]]+)\](?::([0-9]{1,5}))?", registry)
        if match is None:
            raise ContractError("FRUIT_V4_IMAGE_REGISTRY must be host[:port]")
        host, port = match.groups()
        try:
            ipaddress.IPv6Address(host)
        except ValueError as error:
            raise ContractError(
                "FRUIT_V4_IMAGE_REGISTRY contains an invalid IPv6 host"
            ) from error
    else:
        if registry.count(":") > 1:
            raise ContractError(
                "FRUIT_V4_IMAGE_REGISTRY IPv6 hosts must use brackets"
            )
        host, separator, port_value = registry.rpartition(":")
        if not separator:
            host, port = registry, None
        else:
            port = port_value
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            labels = host.split(".")
            if not labels or any(DNS_LABEL.fullmatch(label) is None for label in labels):
                raise ContractError(
                    "FRUIT_V4_IMAGE_REGISTRY contains an invalid host"
                )

    if port is not None and (not port.isdigit() or not 1 <= int(port) <= 65535):
        raise ContractError("FRUIT_V4_IMAGE_REGISTRY contains an invalid port")


def build_image_ref(registry: str, digest: str) -> str:
    validate_registry(registry)
    if HEX_64.fullmatch(digest) is None:
        raise ContractError("FRUIT_V4_IMAGE_DIGEST must be 64 lowercase hex characters")
    return f"{registry}/{IMAGE_REPOSITORY}@sha256:{digest}"


def inspect_image(image_ref: str) -> dict[str, object]:
    result = subprocess.run(
        ["docker", "image", "inspect", image_ref],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ContractError("exact pulled image inspect failed")
    try:
        inspection = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ContractError("docker image inspect did not return valid JSON") from error
    if not isinstance(inspection, list) or len(inspection) != 1:
        raise ContractError("docker image inspect must resolve exactly one image")
    value = inspection[0]
    if not isinstance(value, dict):
        raise ContractError("docker image inspect returned an invalid image object")
    return value


def validate_image_inspection(
    image_ref: str,
    source_sha: str,
    inspection: dict[str, object],
) -> None:
    if HEX_40.fullmatch(source_sha) is None:
        raise ContractError("FRUIT_V4_SOURCE_SHA must be 40 lowercase hex characters")

    config = inspection.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if not isinstance(labels, dict):
        raise ContractError("pulled image is missing immutable OCI labels")
    if labels.get("org.opencontainers.image.source") != EXPECTED_SOURCE:
        raise ContractError("pulled image OCI source repository mismatch")
    if labels.get("org.opencontainers.image.revision") != source_sha:
        raise ContractError("pulled image OCI source revision mismatch")

    repo_digests = inspection.get("RepoDigests")
    if not isinstance(repo_digests, list) or image_ref not in repo_digests:
        raise ContractError("pulled image RepoDigests does not contain the exact digest")


def validate_image_sql(
    image_ref: str, migration_path: str, expected_sha256: str
) -> None:
    if MIGRATION_PATH_RE.fullmatch(migration_path) is None:
        raise ContractError("FRUIT_V4_MIGRATION_PATH must be a canonical migration path")
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise ContractError(
            "FRUIT_V4_MIGRATION_SHA256 must be a lowercase SHA-256"
        )
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "cat", image_ref, migration_path],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ContractError("exact Fruit image migration extraction failed")
    actual_sha256 = hashlib.sha256(result.stdout).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ContractError(
            "exact Fruit image migration does not match the manifest SHA-256"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--registry-only",
        action="store_true",
        help="validate non-history UAT inputs before the explicit image pull",
    )
    arguments = parser.parse_args()

    try:
        registry = required_environment("FRUIT_V4_IMAGE_REGISTRY")
        digest = required_environment("FRUIT_V4_IMAGE_DIGEST")
        source_sha = required_environment("FRUIT_V4_SOURCE_SHA")
        infra_revision = required_environment("FRUIT_V4_INFRA_REVISION")
        image_ref = build_image_ref(registry, digest)
        if HEX_40.fullmatch(source_sha) is None:
            raise ContractError(
                "FRUIT_V4_SOURCE_SHA must be 40 lowercase hex characters"
            )
        validate_infra_checkout(infra_revision)
        if arguments.registry_only:
            if (
                os.environ.get("HISTORICAL_MIGRATION", "").lower() == "true"
                or os.environ.get("FRUIT_V4_MIGRATION_PATH") is not None
            ):
                raise ContractError(
                    "--registry-only is forbidden for history release provenance"
                )
            print("fruit-v4 non-history UAT inputs: ok")
            return 0
        inspection = inspect_image(image_ref)
        validate_image_inspection(image_ref, source_sha, inspection)
        migration_path = os.environ.get("FRUIT_V4_MIGRATION_PATH")
        migration_sha256 = os.environ.get("FRUIT_V4_MIGRATION_SHA256")
        if (migration_path is None) != (migration_sha256 is None):
            raise ContractError(
                "FRUIT_V4_MIGRATION_PATH and FRUIT_V4_MIGRATION_SHA256 must be supplied together"
            )
        if migration_path is not None and migration_sha256 is not None:
            validate_image_sql(image_ref, migration_path, migration_sha256)
    except ContractError as error:
        print(f"fruit-v4 image provenance: {error}", file=sys.stderr)
        return 1

    print("fruit-v4 image provenance: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
