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
    "VTEST_",
    "vtest-smoke.sh",
    "mint-vtest",
    "verify-vtest",
    "write-deploy-audit",
    "system_http_proxy",
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


def main() -> None:
    for relative in RETIRED_PATHS:
        path = ROOT / relative
        if path.is_dir():
            assert not any(item.is_file() for item in path.rglob("*")), relative
        else:
            assert not path.exists(), relative

    active_roots = (
        ROOT / ".github/workflows",
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

    workflow = (ROOT / ".github/workflows/build-mypc-images.yml").read_text()
    assert "name: Build mypc production images" in workflow
    assert "runs-on: [self-hosted, mypc, prod-build]" in workflow
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
