#!/usr/bin/env python3
"""Static contract for the isolated Fruit V4 production Compose seam."""

from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = ROOT / "deploy/fruit-v4/docker-compose.yml"


def service_block(compose: str, service: str, next_service: str | None = None) -> str:
    start = compose.index(f"  {service}:\n")
    block = compose[start:]
    if next_service is not None:
        block = block.split(f"\n  {next_service}:\n", 1)[0]
    else:
        block = block.split("\nnetworks:\n", 1)[0]
    return block


def main() -> None:
    assert COMPOSE_PATH.exists(), f"missing Compose contract: {COMPOSE_PATH}"
    compose = COMPOSE_PATH.read_text()
    setup = service_block(compose, "fruit-v4-setup", "fruit-v4-uat")
    uat = service_block(compose, "fruit-v4-uat")

    image = (
        'x-fruit-v4-image: &fruit-v4-image '
        '"${FRUIT_V4_IMAGE_REGISTRY:?Set FRUIT_V4_IMAGE_REGISTRY}/fruit-industry-pack'
        '@sha256:${FRUIT_V4_IMAGE_DIGEST:?Set FRUIT_V4_IMAGE_DIGEST}"'
    )
    assert image in compose
    assert compose.count("    image: *fruit-v4-image\n") == 2
    assert "build:" not in compose
    assert "latest" not in compose
    assert "env_file:" not in compose
    assert "ports:" not in compose
    assert "volumes:" not in compose

    assert "external: true" in compose
    assert "name: ${FRUIT_V4_CANONICAL_NETWORK:?Set FRUIT_V4_CANONICAL_NETWORK}" in compose
    assert "networks: [production]" in setup
    assert "networks:" in uat and "production:" in uat

    assert 'command: ["node", "packages/fruit-industry-pack/dist/db/setup.js"]' in setup
    assert 'command: ["node", "packages/fruit-industry-pack/dist/cli.js"]' in uat
    assert "restart: \"no\"" in setup
    assert "restart: unless-stopped" in uat
    assert "condition: service_completed_successfully" in uat
    assert "healthcheck:" in uat
    assert "http://127.0.0.1:8002/healthz" in uat

    assert "FRUIT_V4_WRITER_DATABASE_URL" in setup
    assert "FRUIT_V4_RUNTIME_DB_ROLE" in setup
    assert "FRUIT_V4_RUNTIME_DB_PASSWORD" in setup
    assert "FRUIT_V4_WRITER_DATABASE_URL" not in uat
    assert "FRUIT_V4_RUNTIME_DB_PASSWORD" not in uat
    for name in (
        "FRUIT_V4_RUNTIME_DATABASE_URL",
        "FRUIT_V4_SERVICE_SECRET",
        "FRUIT_V4_ALLOWED_TENANT_IDS",
        "FRUIT_V4_ALLOWED_EMPLOYEE_IDS",
    ):
        assert name in uat
        assert name not in setup

    for name in (
        "FRUIT_V4_IMAGE_REGISTRY",
        "FRUIT_V4_IMAGE_DIGEST",
        "FRUIT_V4_SOURCE_SHA",
        "FRUIT_V4_CANONICAL_NETWORK",
        "FRUIT_V4_WRITER_DATABASE_URL",
        "FRUIT_V4_RUNTIME_DATABASE_URL",
        "FRUIT_V4_RUNTIME_DB_ROLE",
        "FRUIT_V4_RUNTIME_DB_PASSWORD",
        "FRUIT_V4_SERVICE_SECRET",
        "FRUIT_V4_ALLOWED_TENANT_IDS",
        "FRUIT_V4_ALLOWED_EMPLOYEE_IDS",
    ):
        assert f"${{{name}:?" in compose, name
        assert f"${{{name}:-" not in compose, name

    for literal in (
        "com.vecta.contract: fruit-v4-isolated-production-compose",
        "com.vecta.owner: vecta-infra",
        "org.opencontainers.image.source: https://github.com/ZenoWangzy/vecta-infra",
        "com.vecta.image.digest: sha256:${FRUIT_V4_IMAGE_DIGEST:?Set FRUIT_V4_IMAGE_DIGEST}",
        "com.vecta.source.revision: ${FRUIT_V4_SOURCE_SHA:?Set FRUIT_V4_SOURCE_SHA}",
        "container_name: fruit-v4-isolated-setup",
        "container_name: fruit-v4-isolated-uat",
        "aliases: [fruit-v4-isolated-uat]",
    ):
        assert literal in compose, literal

    for forbidden in ("poc-db-password", "change-me", "postgresql://", "tenant-", "employee-"):
        assert forbidden not in compose, forbidden

    assert re.search(r"^name: fruit-v4-isolated-production$", compose, re.MULTILINE)
    print("fruit-v4 isolated Compose contract: ok")


if __name__ == "__main__":
    main()
