#!/usr/bin/env python3
"""Small static contract for the retired integration deployment path."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RETIRED_MARKERS = (
    "deploy-vtest",
    "_deploy-vtest",
    "inventories/vtest",
    "roles/deploy-vtest",
    "fruit_vtest",
    "fruit-vtest",
    "shared_pool_vtest",
    "vtest_allow_migrate",
    "vtest_required_image_repos",
    "vtest_write_quiesce_services",
    "VTEST_",
    "vtest-smoke.sh",
    "mint-vtest",
    "verify-vtest",
    "write-deploy-audit",
    "system_http_proxy",
)
LEGACY_POLICY_MARKERS = (
    "-> vtest validation ->",
    "vtest postsubmit",
    "runs the vtest lane",
    "broaden a vtest trigger",
)
RETIRED_PATHS = (
    ".github/workflows/deploy-vtest.yml",
    ".github/workflows/_deploy-vtest-job.yml",
    "inventories/vtest",
    "playbooks/app.yml",
    "playbooks/deploy.yml",
    "roles/deploy-vtest",
    "roles/fruit_vtest",
    "roles/openclaw",
    "roles/search",
    "roles/nexus/tasks/system_http_proxy.yml",
    "scripts/mint-vtest-platform-bundle.mjs",
    "scripts/prune-vtest-docker-build-state.sh",
    "scripts/verify-vtest-platform-bundle.mjs",
    "scripts/vtest-smoke.sh",
    "scripts/write-deploy-audit.sh",
)
ACTIVE_MYPC_RUNBOOKS = (
    "docs/runbooks/mypc-data-structure-compatibility.md",
)


def main() -> None:
    for relative in RETIRED_PATHS:
        path = ROOT / relative
        if path.is_dir():
            assert not any(item.is_file() for item in path.rglob("*")), relative
        else:
            assert not path.exists(), relative

    for relative in ACTIVE_MYPC_RUNBOOKS:
        content = (ROOT / relative).read_text()
        assert not content.lstrip().startswith("# Retired:"), relative

    active_roots = (
        ROOT / ".github/workflows",
        ROOT / "inventories",
        ROOT / "playbooks",
        ROOT / "roles",
        ROOT / "scripts",
        ROOT / "ansible.cfg",
    )
    for root in active_roots:
        paths = [root] if root.is_file() else sorted(
            path for path in root.rglob("*") if path.is_file()
        )
        for path in paths:
            if path == Path(__file__):
                continue
            content = path.read_text()
            for marker in RETIRED_MARKERS:
                assert marker not in content, f"{marker}: {path}"

    for path in sorted(
        path for path in (ROOT / "inventories").rglob("*") if path.is_file()
    ):
        assert "vtest" not in path.read_text().lower(), f"vtest: {path}"

    active_policy_roots = (
        ROOT / "AGENTS.md",
        ROOT / "CLAUDE.md",
        ROOT / "CONTRIBUTING.md",
    )
    for path in active_policy_roots:
        if not path.is_file():
            continue
        content = path.read_text()
        for marker in RETIRED_MARKERS + LEGACY_POLICY_MARKERS:
            assert marker not in content, f"{marker}: {path}"

    active_spec_roots = (
        ROOT / "README.md",
        ROOT / ".trellis/spec",
        ROOT / "docs",
    )
    for root in active_spec_roots:
        paths = [root] if root.is_file() else sorted(
            path for path in root.rglob("*") if path.is_file()
        )
        for path in paths:
            content = path.read_text()
            if content.lstrip().startswith("# Retired:"):
                continue
            assert "vtest" not in content.lower(), f"vtest: {path}"
            for marker in RETIRED_MARKERS + LEGACY_POLICY_MARKERS:
                assert marker not in content, f"{marker}: {path}"

    agents = (ROOT / "AGENTS.md").read_text()
    assert "vtest is permanently retired." in agents
    assert "caller-removal hotfix must merge first" in agents
    contributing = (ROOT / "CONTRIBUTING.md").read_text()
    assert "The VectA caller-removal hotfix must merge first" in contributing
    assert "Do not restore a compatibility workflow" in contributing

    mypc_compatibility = (
        ROOT / "docs/runbooks/mypc-data-structure-compatibility.md"
    ).read_text()
    for literal in (
        "mypc is the production state source.",
        "openclaw-enterprise_postgres_data",
        "openclaw-enterprise_open-webui-data",
        "/data/ocee/data/instances",
        "deploy_image_tag_requires_full_sha=false",
        "mypc_deploy_enabled=false",
        "mypc_stateful_services_enabled=false",
        "playbooks/mypc-network-reconcile.yml",
        "scripts/reconcile-open-webui-admin-network.sh --self-test",
        "唯一支持的恢复路径",
        "不得对 replacement",
        "四个 baseline IDs",
        "不是成功证据",
    ):
        assert literal in mypc_compatibility, literal

    workflow = (ROOT / ".github/workflows/build-mypc-images.yml").read_text()
    retired_environment = "".join(("v", "test"))
    assert "name: Build mypc production images" in workflow
    assert "runs-on: [self-hosted, mypc, prod-build]" in workflow
    assert "environment: production" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "SOURCE_SHA: ${{ inputs.source_sha }}" in workflow
    assert "SOURCE_BRANCH: ${{ inputs.source_branch }}" in workflow
    assert "NEXUS_DOCKER_REGISTRY: 127.0.0.1:8082" in workflow
    assert "DOCKER_BASE_IMAGE_REGISTRY: 127.0.0.1:8082" in workflow
    assert "DOCKER_BASE_IMAGE_SOURCE_REGISTRY: 127.0.0.1:8082" in workflow
    assert workflow.count("image_names:") == 2
    assert "PRODUCTION_IMAGE_NAMES: ${{ inputs.image_names || '' }}" in workflow
    assert 'docker login "$NEXUS_DOCKER_REGISTRY" -u admin --password-stdin' in workflow
    assert "run: pnpm check:production-images" in workflow
    assert "node scripts/build-push-production-images.mjs" in workflow
    assert "nice -n 10 ionice -c2 -n7" in workflow
    assert f"check:{retired_environment}-images" not in workflow
    assert f"build-push-{retired_environment}-images.mjs" not in workflow
    assert f"{retired_environment.upper()}_IMAGE_NAMES" not in workflow
    assert "GITHUB_SHA: ${{ inputs.source_sha }}" not in workflow
    assert "inventory = ./inventories/mypc/hosts.ini" in (
        ROOT / "ansible.cfg"
    ).read_text()
    infra = (ROOT / "playbooks/infra.yml").read_text()
    assert "tasks_from: docker_registry" in infra
    assert "tasks_from: system_http_proxy" not in infra
    app_role = (ROOT / "roles/vecta-app/tasks/main.yml").read_text()
    assert "vecta-app requires an explicit mypc inventory target." in app_role
    print("retired environment contract: ok")


if __name__ == "__main__":
    main()
