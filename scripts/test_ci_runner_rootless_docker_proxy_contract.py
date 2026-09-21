#!/usr/bin/env python3
"""Static safety contract for CI-only rootless Docker proxy management."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    inventory = (ROOT / "inventories/mypc/group_vars/mypc.yml").read_text()
    tasks = (ROOT / "roles/ci-runner/tasks/main.yml").read_text()
    playbook = (ROOT / "playbooks/mypc-ci-runners.yml").read_text()

    assert inventory.count("name: vecta-ci\n") == 1
    assert inventory.count("name: vecta-ci-2\n") == 1
    assert 'ci_rootless_docker_proxy: "http://127.0.0.1:3129"' in inventory
    assert 'ci_rootless_docker_probe_image: "pgvector/pgvector:pg16"' in inventory
    assert "scope: user" in tasks
    assert "become_user:" in tasks
    assert "DOCKER_HOST: \"unix:///run/user/{{ item.uid }}/docker.sock\"" in tasks
    assert "/var/run/docker.sock" not in tasks
    assert "docker group" not in (inventory + tasks + playbook).lower()
    assert "mypc_deploy_enabled | default(false) | bool" in playbook
    print("CI runner rootless Docker proxy contract: ok")


if __name__ == "__main__":
    main()
