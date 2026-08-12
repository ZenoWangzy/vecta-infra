#!/usr/bin/env python3
"""Static contract for the immutable employee-runtime vtest handoff."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = (ROOT / "inventories/vtest/group_vars/vtest.yml").read_text()
PREFLIGHT = (ROOT / "roles/deploy-vtest/tasks/preflight.yml").read_text()
NEXUS_DEFAULTS = (ROOT / "roles/nexus/defaults/main.yml").read_text()
NEXUS_GHCR = (ROOT / "roles/nexus/tasks/ghcr_proxy.yml").read_text()
NEXUS_GROUP = (ROOT / "roles/nexus/tasks/docker_group.yml").read_text()
INFRA_PLAYBOOK = (ROOT / "playbooks/infra.yml").read_text()
FLEET = (ROOT / "roles/vecta-app/tasks/fleet_gateway.yml").read_text()
CHANNEL = (ROOT / "roles/vecta-app/tasks/channel_gateway.yml").read_text()
MYPC_CHANNEL = (ROOT / "roles/vecta-app/tasks/channel_gateway_mypc.yml").read_text()


def task_block(text: str, name: str) -> str:
    match = re.search(rf"^- name: {re.escape(name)}$", text, re.MULTILINE)
    if match is None:
        raise AssertionError(f"missing task: {name}")
    next_task = re.search(r"^- name: ", text[match.end() :], re.MULTILINE)
    end = len(text) if next_task is None else match.end() + next_task.start()
    return text[match.start() : end]


class EmployeeRuntimeContractTest(unittest.TestCase):
    def test_vtest_requires_the_immutable_employee_runtime_tag(self) -> None:
        self.assertIn(
            'hermes_runtime_image_version: "{{ nexus_docker_registry }}/employee-runtime:{{ deploy_sha }}"',
            INVENTORY,
        )
        required_images = INVENTORY.split("vtest_required_image_repos:\n", 1)[1].split("\n\n", 1)[0]
        self.assertIn("  - employee-runtime", required_images)
        manifest_check = task_block(PREFLIGHT, "Preflight — check required Nexus image manifests")
        self.assertIn("/v2/{{ item }}/manifests/{{ deploy_sha }}", manifest_check)
        self.assertIn("status_code: 200", manifest_check)

    def test_ghcr_proxy_is_in_the_nexus_docker_group(self) -> None:
        for exact_value in (
            "nexus_ghcr_proxy_repo: vecta-ghcr-remote",
            'nexus_ghcr_remote_index_url: "https://ghcr.io"',
            'nexus_ghcr_remote_url: "https://ghcr.io"',
        ):
            self.assertIn(exact_value, NEXUS_DEFAULTS)
        proxy = task_block(NEXUS_GHCR, "Create or update GitHub Container Registry Docker proxy repo")
        self.assertIn('name: "{{ nexus_ghcr_proxy_repo }}"', proxy)
        self.assertIn('remoteUrl: "{{ nexus_ghcr_remote_url }}"', proxy)
        self.assertIn('indexUrl: "{{ nexus_ghcr_remote_index_url }}"', proxy)
        group = task_block(NEXUS_GROUP, "Update Nexus Docker group repo members")
        self.assertIn('- "{{ nexus_ghcr_proxy_repo }}"', group)
        self.assertIn("tasks_from: docker_registry", INFRA_PLAYBOOK)
        self.assertIn("tags: [nexus]", INFRA_PLAYBOOK)

    def test_vtest_fleet_and_channel_resolve_the_linux_host_gateway(self) -> None:
        self.assertIn(
            "fleet_gateway_etc_hosts:\n  host.docker.internal: host-gateway",
            INVENTORY,
        )
        self.assertIn(
            "channel_gateway_etc_hosts:\n  host.docker.internal: host-gateway",
            INVENTORY,
        )
        self.assertIn('etc_hosts: "{{ fleet_gateway_etc_hosts | default(omit) }}"', FLEET)
        self.assertIn('etc_hosts: "{{ channel_gateway_etc_hosts | default(omit) }}"', CHANNEL)

    def test_vtest_channel_shares_fleet_database_and_gates_the_e2e_cli(self) -> None:
        deploy_channel_gateway = task_block(CHANNEL, "Deploy channel-gateway")
        self.assertTrue(
            deploy_channel_gateway.rstrip().endswith("  no_log: true"),
            "channel-gateway DATABASE_URL must be hidden at task level",
        )
        self.assertIn(
            "DATABASE_URL: \"{{ (shared_pool_vtest_e2e_enabled | default(false) | bool) | ternary('",
            CHANNEL,
        )
        self.assertIn("'postgresql://' ~ (postgres_user | default('openclaw_poc'))", CHANNEL)
        self.assertIn("~ '@openclaw-postgres:5432/' ~ (postgres_db | default('openclaw_poc')), omit) }}\"", CHANNEL)
        self.assertIn(
            'SHARED_POOL_VTEST_E2E_ENABLED: "{{ (shared_pool_vtest_e2e_enabled | default(false) | bool) | ternary(\'1\', omit) }}"',
            CHANNEL,
        )
        self.assertIn("shared_pool_vtest_e2e_enabled: true", INVENTORY)
        self.assertNotIn("SHARED_POOL_VTEST_E2E_ENABLED", MYPC_CHANNEL)


if __name__ == "__main__":
    unittest.main()
