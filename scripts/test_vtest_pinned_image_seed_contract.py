#!/usr/bin/env python3
"""Static contract for vtest pinned third-party image seeding."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
ROLE_MAIN = (ROOT / "roles/deploy-vtest/tasks/main.yml").read_text()
PREFLIGHT = (ROOT / "roles/deploy-vtest/tasks/preflight.yml").read_text()
INVENTORY = (ROOT / "inventories/vtest/group_vars/vtest.yml").read_text()
OPEN_WEBUI_ROLE = (ROOT / "roles/open-webui/tasks/main.yml").read_text()


def task_block(text: str, name: str) -> str:
    match = re.search(rf"^- name: {re.escape(name)}$", text, re.MULTILINE)
    if match is None:
        raise AssertionError(f"missing task: {name}")
    next_task = re.search(r"^- name: ", text[match.end() :], re.MULTILINE)
    end = len(text) if next_task is None else match.end() + next_task.start()
    return text[match.start() : end]


class VtestPinnedImageSeedContractTest(unittest.TestCase):
    def test_inventory_seeds_only_open_webui_images_for_selected_tags(self) -> None:
        self.assertEqual(
            re.findall(r'^\s+image: "\{\{ ([a-z0-9_]+) \}\}"$', OPEN_WEBUI_ROLE, re.MULTILINE),
            ["open_webui_image", "open_webui_nginx_image", "kk_file_view_image", "onlyoffice_image"],
        )
        for exact_ref in (
            'open_webui_image: "{{ nexus_docker_registry }}/open-webui/open-webui:v0.9.2"',
            'open_webui_nginx_image: "{{ nexus_docker_registry }}/library/nginx:alpine"',
            'kk_file_view_image: "{{ nexus_docker_registry }}/keking/kkfileview:latest"',
            'onlyoffice_image: "{{ nexus_docker_registry }}/onlyoffice/documentserver:8.2"',
        ):
            self.assertIn(exact_ref, INVENTORY)

        start = INVENTORY.index("vtest_pinned_third_party_images:")
        end = INVENTORY.index("\n\n", start)
        self.assertEqual(
            INVENTORY[start:end],
            "vtest_pinned_third_party_images:\n"
            '  - "{{ open_webui_image }}"\n'
            '  - "{{ open_webui_nginx_image }}"\n'
            '  - "{{ kk_file_view_image }}"\n'
            '  - "{{ onlyoffice_image }}"',
        )

        seed = task_block(
            PREFLIGHT,
            "Preflight — seed pinned third-party images needed by selected tags",
        )
        self.assertIn('loop: "{{ vtest_pinned_third_party_images | default([]) }}"', seed)
        self.assertIn("'open-webui' in ansible_run_tags or 'all' in ansible_run_tags", seed)
        self.assertIn("tags: open-webui", seed)

    def test_seed_is_pull_first_idempotent_and_uses_only_the_exact_ref(self) -> None:
        seed = task_block(PREFLIGHT, "Preflight — seed pinned third-party images needed by selected tags")
        pull = 'docker pull "$VTEST_PINNED_IMAGE"'
        inspect = 'docker image inspect "$VTEST_PINNED_IMAGE"'
        push = 'docker push "$VTEST_PINNED_IMAGE"'
        first_pull = seed.index(pull)
        local_inspect = seed.index(inspect)
        image_push = seed.index(push)
        verify_pull = seed.index(pull, first_pull + 1)

        self.assertLess(first_pull, local_inspect)
        self.assertLess(local_inspect, image_push)
        self.assertLess(image_push, verify_pull)
        self.assertLess(seed.index("exit 0"), local_inspect)
        self.assertLess(seed.index("exit 1"), image_push)
        self.assertIn('VTEST_PINNED_IMAGE: "{{ item }}"', seed)
        self.assertIn("register: vtest_pinned_third_party_seed", seed)
        self.assertIn(
            "changed_when: \"'VTEST_PINNED_IMAGE_SEEDED' in vtest_pinned_third_party_seed.stdout_lines\"",
            seed,
        )
        for forbidden in (
            "docker manifest inspect",
            "docker tag",
            "ghcr.io",
            "docker.io",
            "NEXUS_ADMIN_PASSWORD",
            "--password",
        ):
            self.assertNotIn(forbidden, seed)

    def test_missing_local_image_fails_before_any_service_mutation(self) -> None:
        seed = task_block(PREFLIGHT, "Preflight — seed pinned third-party images needed by selected tags")
        preflight_import = ROLE_MAIN.index("- import_tasks: preflight.yml")
        quiesce = ROLE_MAIN.index("- import_tasks: quiesce.yml")
        deploy = ROLE_MAIN.index("- import_tasks: deploy.yml")

        self.assertIn('if ! docker image inspect "$VTEST_PINNED_IMAGE"', seed)
        self.assertIn("exit 1", seed)
        self.assertLess(preflight_import, quiesce)
        self.assertLess(preflight_import, deploy)


if __name__ == "__main__":
    unittest.main()
