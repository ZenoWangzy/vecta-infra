#!/usr/bin/env python3
"""Minimal static contract for Nexus outbound-proxy automation."""

from pathlib import Path
import re
import subprocess
import unittest
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
ROLE = (ROOT / "roles/nexus/tasks/system_http_proxy.yml").read_text()
NEXUS_MAIN = (ROOT / "roles/nexus/tasks/main.yml").read_text()
DEFAULTS = (ROOT / "roles/nexus/defaults/main.yml").read_text()
INFRA_PLAYBOOK = (ROOT / "playbooks/infra.yml").read_text()
DEPLOY_PLAYBOOK = (ROOT / "playbooks/deploy.yml").read_text()
APP_PLAYBOOK = (ROOT / "playbooks/app.yml").read_text()
DEPLOY_VTEST = (ROOT / "roles/deploy-vtest/tasks/deploy.yml").read_text()
WORKFLOW = (ROOT / ".github/workflows/_deploy-vtest-job.yml").read_text()


def task_block(name: str) -> str:
    match = re.search(rf"^(?P<indent> *)- name: {re.escape(name)}$", ROLE, re.MULTILINE)
    if match is None:
        raise AssertionError(f"missing task: {name}")
    next_task = re.search(rf"^{' ' * len(match.group('indent'))}- name: ", ROLE[match.end() :], re.MULTILINE)
    end = len(ROLE) if next_task is None else match.end() + next_task.start()
    return ROLE[match.start() : end]


def nexus_main_task_block(name: str) -> str:
    match = re.search(rf"^- name: {re.escape(name)}$", NEXUS_MAIN, re.MULTILINE)
    if match is None:
        raise AssertionError(f"missing Nexus main task: {name}")
    next_task = re.search(r"^- name: ", NEXUS_MAIN[match.end() :], re.MULTILINE)
    end = len(NEXUS_MAIN) if next_task is None else match.end() + next_task.start()
    return NEXUS_MAIN[match.start() : end]


def workflow_step(name: str) -> str:
    start = WORKFLOW.index(f"      - name: {name}")
    end = WORKFLOW.find("\n      - ", start + 1)
    return WORKFLOW[start:] if end == -1 else WORKFLOW[start:end]


def compact(value: str) -> str:
    return " ".join(value.split())


class NexusOutboundProxyContractTest(unittest.TestCase):
    def test_git_proxy_is_preserved_and_proxy_secrets_are_step_scoped(self) -> None:
        job_env = WORKFLOW.split("    env:\n", 1)[1].split("    steps:\n", 1)[0]
        git_proxy = workflow_step("Configure git proxy and check out vecta-infra")
        deploy = workflow_step("Deploy via Ansible")

        self.assertNotIn("PROXY_USERNAME:", job_env)
        self.assertNotIn("PROXY_PASSWORD:", job_env)
        self.assertNotIn("PROXY_PREVIOUS_PASSWORD:", job_env)
        self.assertNotIn("NEXUS_ADMIN_PASSWORD:", job_env)
        self.assertIn("GIT_CONFIG_COUNT=5", git_proxy)
        self.assertIn("GIT_CONFIG_KEY_0=http.lowSpeedLimit", git_proxy)
        self.assertIn("GIT_CONFIG_KEY_1=http.lowSpeedTime", git_proxy)
        self.assertIn("GIT_CONFIG_KEY_2=http.proxy", git_proxy)
        self.assertIn("GIT_CONFIG_KEY_3=https.proxy", git_proxy)
        self.assertIn("GIT_CONFIG_KEY_4=remote.origin.url", git_proxy)
        self.assertIn("GIT_CONFIG_VALUE_4=https://github.com/ZenoWangzy/vecta-infra.git", git_proxy)
        self.assertIn('GIT_TERMINAL_PROMPT: "0"', git_proxy)
        self.assertIn("unset proxy_url proxy_username_encoded proxy_password_encoded GIT_CONFIG_COUNT", git_proxy)
        self.assertIn('proxy_username_encoded="$(printf \'%s\' "${PROXY_USERNAME}" | od -An -tx1 | tr -d \' \\n\' | sed \'s/../%&/g\')"', git_proxy)
        self.assertIn('proxy_password_encoded="$(printf \'%s\' "${PROXY_PASSWORD}" | od -An -tx1 | tr -d \' \\n\' | sed \'s/../%&/g\')"', git_proxy)
        self.assertIn('proxy_url="http://${proxy_username_encoded}:${proxy_password_encoded}@geraldsynnas.ddns.net:8888"', git_proxy)
        self.assertNotIn('proxy_url="http://${PROXY_USERNAME}:${PROXY_PASSWORD}@', git_proxy)
        self.assertNotIn("git config --global", git_proxy)
        self.assertNotIn('curl -x "$PROXY"', git_proxy)
        self.assertNotIn('git -C "$GITHUB_WORKSPACE" fetch "$proxy_url"', git_proxy)
        self.assertNotRegex(git_proxy, r"git fetch[^\n]*proxy_url")
        self.assertNotIn("remote add origin https://", git_proxy)
        self.assertNotIn("GITHUB_TOKEN@", git_proxy)
        self.assertNotIn("https://x-access-token:", git_proxy)
        self.assertNotIn("GITHUB_TOKEN", git_proxy)
        self.assertNotIn("git_auth", git_proxy)
        self.assertNotIn("extraHeader", git_proxy)
        self.assertNotIn("Authorization:", git_proxy)
        self.assertNotIn("base64", git_proxy)
        self.assertNotIn("GIT_CONFIG_KEY_5", git_proxy)
        self.assertNotIn("GIT_CONFIG_VALUE_5", git_proxy)
        for leaked_log in (
            'echo "$proxy_url"',
            'echo "${PROXY_USERNAME}"',
            'echo "${PROXY_PASSWORD}"',
            "set -x",
            "GIT_TRACE",
        ):
            self.assertNotIn(leaked_log, git_proxy)
        self.assertNotIn("actions/checkout", WORKFLOW)
        self.assertIn("PROXY_USERNAME: ${{ secrets.PROXY_USERNAME }}", git_proxy)
        self.assertIn("PROXY_PASSWORD: ${{ secrets.PROXY_PASSWORD }}", git_proxy)
        self.assertIn("PROXY_USERNAME: ${{ secrets.PROXY_USERNAME }}", deploy)
        self.assertIn("PROXY_PASSWORD: ${{ secrets.PROXY_PASSWORD }}", deploy)
        self.assertIn("PROXY_PREVIOUS_PASSWORD: ${{ secrets.PROXY_PREVIOUS_PASSWORD }}", deploy)
        self.assertIn("NEXUS_ADMIN_PASSWORD: ${{ secrets.NEXUS_ADMIN_PASSWORD }}", deploy)

    def test_proxy_url_percent_encodes_special_character_credentials(self) -> None:
        encoder = "printf '%s' \"$1\" | od -An -tx1 | tr -d ' \\n' | sed 's/../%&/g'"
        username = subprocess.run(
            ["bash", "-c", encoder, "--", "user@x"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        password = subprocess.run(
            ["bash", "-c", encoder, "--", "p:a%"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        proxy_url = f"http://{username}:{password}@geraldsynnas.ddns.net:8888"
        parsed = urlsplit(proxy_url)

        self.assertEqual(username, "%75%73%65%72%40%78")
        self.assertEqual(password, "%70%3a%61%25")
        self.assertEqual(parsed.scheme, "http")
        self.assertEqual(parsed.hostname, "geraldsynnas.ddns.net")
        self.assertEqual(parsed.port, 8888)

    def test_public_checkout_is_direct_first_and_proxy_fallback_is_lazy(self) -> None:
        git_proxy = workflow_step("Configure git proxy and check out vecta-infra")
        fetch = "timeout 30s git fetch --depth=1 origin main"
        first_fetch = git_proxy.index(fetch)
        fallback_credentials = git_proxy.index("proxy_username_encoded=")
        second_fetch = git_proxy.index(fetch, first_fetch + 1)

        self.assertEqual(git_proxy.count(fetch), 2)
        self.assertLess(first_fetch, fallback_credentials)
        self.assertLess(fallback_credentials, second_fetch)
        self.assertLess(second_fetch, git_proxy.index("git checkout --force FETCH_HEAD"))
        self.assertIn(f"if {fetch}; then", git_proxy)
        self.assertIn("direct public GitHub checkout succeeded", git_proxy)
        self.assertIn("direct public GitHub checkout failed; retrying through configured proxy", git_proxy)
        self.assertIn("configured proxy GitHub checkout failed", git_proxy)
        self.assertIn('GIT_CONFIG_NOSYSTEM: "1"', git_proxy)
        self.assertIn("GIT_CONFIG_GLOBAL: /dev/null", git_proxy)
        self.assertIn("GIT_CONFIG_VALUE_1=10", git_proxy)
        self.assertIn('if [ -z "${PROXY_USERNAME:-}" ] || [ -z "${PROXY_PASSWORD:-}" ]; then', git_proxy)
        self.assertNotIn("curl -sm 8", git_proxy)

    def test_secure_checkout_cleans_only_the_validated_github_workspace(self) -> None:
        git_proxy = workflow_step("Configure git proxy and check out vecta-infra")
        final_guard = git_proxy.index('[ "$(pwd -P)" = "$workspace_resolved" ]')
        remove_git = git_proxy.index("rm -rf -- .git")
        init = git_proxy.index("git init --quiet")
        fetch = git_proxy.index("git fetch --depth=1 origin main")
        checkout = git_proxy.index("git checkout --force FETCH_HEAD")
        reset = git_proxy.index("git reset --hard FETCH_HEAD")
        clean = git_proxy.index("git clean -ffdx")

        for guard in (
            'workspace_input="${GITHUB_WORKSPACE:-}"',
            '[ -n "$workspace_input" ]',
            'case "$workspace_input" in',
            '/*) ;;',
            '[ -d "$workspace_input" ]',
            'workspace_resolved="$(cd -- "$workspace_input" && pwd -P)"',
            '[ "$workspace_resolved" = "$GITHUB_WORKSPACE" ]',
            '[ "$workspace_resolved" != "/" ]',
            '[ "$workspace_resolved" != "$home_resolved" ]',
            '[ "$(pwd -P)" = "$workspace_resolved" ]',
        ):
            self.assertIn(guard, git_proxy, guard)
            self.assertLess(git_proxy.index(guard), reset, guard)
        for guard in (
            'runner_workspace_input="${RUNNER_WORKSPACE:-}"',
            '[ -n "$runner_workspace_input" ]',
            'case "$runner_workspace_input" in',
            '[ -d "$runner_workspace_input" ]',
            'runner_workspace_resolved="$(cd -- "$runner_workspace_input" && pwd -P)"',
            '[ "$runner_workspace_resolved" = "$RUNNER_WORKSPACE" ]',
            '[ "$runner_workspace_resolved" != "/" ]',
            '[ "$runner_workspace_resolved" != "$home_resolved" ]',
            'case "$workspace_resolved" in',
            '"$runner_workspace_resolved"/*) ;;',
        ):
            self.assertIn(guard, git_proxy, guard)
            self.assertLess(git_proxy.index(guard), reset, guard)
        for command in (
            "rm -rf -- .git",
            "git init --quiet",
            "git fetch --depth=1 origin main",
            "git checkout --force FETCH_HEAD",
            "git reset --hard FETCH_HEAD",
            "git clean -ffdx",
        ):
            self.assertGreater(git_proxy.index(command), git_proxy.index('"$runner_workspace_resolved"/*) ;;'), command)
        self.assertEqual(git_proxy.count("rm -rf"), 1)
        self.assertLess(final_guard, remove_git)
        self.assertLess(remove_git, init)
        self.assertLess(init, fetch)
        self.assertLess(checkout, reset)
        self.assertLess(reset, clean)
        self.assertNotIn('git -C "$GITHUB_WORKSPACE"', git_proxy)

    def test_prune_skips_safely_when_checkout_did_not_provide_the_script(self) -> None:
        prune = workflow_step("Prune vtest Docker build state")

        guard = "if [ ! -f scripts/prune-vtest-docker-build-state.sh ]; then"
        run = "bash scripts/prune-vtest-docker-build-state.sh"
        self.assertIn("if: always()", prune)
        self.assertIn(guard, prune)
        self.assertIn("checkout did not provide its script", prune)
        self.assertIn("exit 0", prune)
        self.assertIn(run, prune)
        self.assertLess(prune.index(guard), prune.index(run))

    def test_deploy_restores_github_proxy_values_after_sourcing_host_env(self) -> None:
        deploy = workflow_step("Deploy via Ansible")
        source_at = deploy.index(". /data/ocee/.env")
        for value in (
            "ci_nexus_admin_password=",
            "ci_proxy_username_set",
            "ci_proxy_password_set",
            "ci_proxy_previous_password_set",
            "ci_proxy_username=",
            "ci_proxy_password=",
            "ci_proxy_previous_password=",
        ):
            self.assertLess(deploy.index(value), source_at, value)
        for value in (
            "export NEXUS_ADMIN_PASSWORD=\"$ci_nexus_admin_password\"",
            "export PROXY_USERNAME=\"$ci_proxy_username\"",
            "export PROXY_PASSWORD=\"$ci_proxy_password\"",
            "export PROXY_PREVIOUS_PASSWORD=\"$ci_proxy_previous_password\"",
            "unset PROXY_USERNAME",
            "unset PROXY_PASSWORD",
            "unset PROXY_PREVIOUS_PASSWORD",
        ):
            self.assertGreater(deploy.index(value), source_at, value)
        self.assertGreater(
            deploy.rindex(
                "unset NEXUS_ADMIN_PASSWORD PROXY_USERNAME PROXY_PASSWORD PROXY_PREVIOUS_PASSWORD"
            ),
            source_at,
        )

    def test_secret_carrying_tasks_do_not_log(self) -> None:
        for name in (
            "Refuse incomplete Nexus outbound proxy secrets",
            "Restrict Nexus System HTTP proxy automation to vtest",
            "Initialize Nexus System HTTP recovery state",
            "Read Nexus version before System HTTP automation",
            "Require the pinned Nexus version for System HTTP automation",
            "Read Nexus System HTTP settings before update",
            "Validate the pinned Nexus System HTTP read response",
            "Refuse Nexus System HTTP settings outside the recoverable contract",
            "Decide whether Nexus System HTTP needs an explicit password rotation",
            "Invalidate cache before probing an unrotated enabled Nexus proxy",
            "Probe an unrotated enabled Nexus proxy after cache invalidation",
            "Require the old password before recoverable proxy rotation",
            "Mark Nexus System HTTP update as started",
            "Update Nexus System HTTP proxy through the pinned ExtDirect contract",
            "Validate the pinned Nexus System HTTP update response",
            "Read Nexus System HTTP settings after update",
            "Verify Nexus System HTTP settings after update",
            "Invalidate the Docker proxy cache after System HTTP update",
            "Probe the authenticated Node manifest through Nexus",
            "Restore Nexus System HTTP settings from the pre-update snapshot",
            "Read Nexus System HTTP metadata after rollback",
            "Invalidate cache after restoring an enabled Nexus proxy snapshot",
            "Probe an enabled Nexus proxy after rollback",
            "Determine whether Nexus System HTTP rollback was verified",
        ):
            self.assertIn("no_log: true", task_block(name), name)

    def test_extdirect_is_version_pinned_and_read_before_write(self) -> None:
        self.assertIn('nexus_outbound_proxy_version: "3.94.0-12"', DEFAULTS)
        self.assertIn('nexus_outbound_proxy_password_placeholder: "#~NXRM~PLACEHOLDER~PASSWORD~#"', DEFAULTS)
        self.assertLess(
            ROLE.index("Read Nexus version before System HTTP automation"),
            ROLE.index("Read Nexus System HTTP settings before update"),
        )
        self.assertLess(
            ROLE.index("Read Nexus System HTTP settings before update"),
            ROLE.index("Update Nexus System HTTP proxy through the pinned ExtDirect contract"),
        )
        self.assertIn("Nexus System HTTP read response does not match", ROLE)
        self.assertIn("Nexus System HTTP update response does not match", ROLE)
        self.assertIn("Read Nexus System HTTP settings after update", ROLE)
        self.assertIn("The placeholder confirms redaction, never password equality.", ROLE)
        self.assertIn("PROXY_PREVIOUS_PASSWORD is required", ROLE)
        self.assertIn("PROXY_PREVIOUS_PASSWORD:\n        required: false", WORKFLOW)

    def test_system_http_entry_is_isolated_from_full_nexus_role_side_effects(self) -> None:
        self.assertIn("ansible.builtin.import_role:", INFRA_PLAYBOOK)
        self.assertIn("name: nexus", INFRA_PLAYBOOK)
        self.assertIn("tasks_from: system_http_proxy", INFRA_PLAYBOOK)
        self.assertIn("tags: [nexus]", INFRA_PLAYBOOK)
        self.assertNotIn("- role: nexus", INFRA_PLAYBOOK)

        self.assertIn(
            "ansible.builtin.import_tasks: system_http_proxy.yml", NEXUS_MAIN
        )
        self.assertNotIn("coreui_HttpSettings", NEXUS_MAIN)
        for forbidden in (
            "Compose Nexus proxy repository definitions",
            "Create or update Nexus proxy repositories",
            "Configure Docker daemon registry mirror",
            "Write .npmrc pointing to Nexus npm proxy",
            "copy:",
            "notify:",
        ):
            self.assertNotIn(forbidden, ROLE)

    def test_full_deploy_chain_has_one_isolated_system_http_entry(self) -> None:
        self.assertIn("- import_playbook: infra.yml", DEPLOY_PLAYBOOK)
        self.assertIn("- import_playbook: app.yml", DEPLOY_PLAYBOOK)
        self.assertIn("- role: deploy-vtest", APP_PLAYBOOK)
        self.assertEqual(INFRA_PLAYBOOK.count("tasks_from: system_http_proxy"), 1)
        self.assertNotIn("name: nexus", DEPLOY_VTEST)
        self.assertNotIn("include_role:\n    name: nexus", DEPLOY_VTEST)

    def test_full_nexus_role_refuses_partial_proxy_secrets_before_repository_writes(self) -> None:
        gate = "Refuse partial Nexus outbound proxy secrets before repository writes"
        gate_block = nexus_main_task_block(gate)
        expected_pair_gate = """(lookup('env', 'PROXY_USERNAME') | length == 0)
        ==
        (lookup('env', 'PROXY_PASSWORD') | length == 0)"""

        self.assertIn(compact(expected_pair_gate), compact(gate_block))
        self.assertIn("must be supplied together", gate_block)
        self.assertIn("no_log: true", gate_block)
        for repository_write in (
            "Create or update Nexus proxy repositories",
            "Create or update Docker hosted repo",
            "Create or update Docker proxy repo",
            "Create or update Docker group repo",
        ):
            self.assertLess(NEXUS_MAIN.index(gate), NEXUS_MAIN.index(repository_write))

    def test_system_http_entry_is_vtest_only_and_mypc_guards_always_run(self) -> None:
        target_gate = task_block("Restrict Nexus System HTTP proxy automation to vtest")
        self.assertIn("'vtest' in group_names", target_gate)
        self.assertIn("inventory_hostname in (groups.get('vtest', []))", target_gate)
        self.assertIn("tags: [always]", INFRA_PLAYBOOK)
        self.assertIn("tags: [always]", APP_PLAYBOOK)

    def test_system_http_entry_fails_closed_for_required_secrets_and_disabled_non_proxy_hosts(self) -> None:
        secret_gate = task_block("Refuse incomplete Nexus outbound proxy secrets")
        settings_gate = task_block(
            "Refuse Nexus System HTTP settings outside the recoverable contract"
        )
        fail_closed = workflow_step("Fail closed if secrets missing")
        expected_disabled_non_proxy_gate = """(nexus_http_settings_before.json[0].result.data.httpEnabled | default(false) | bool)
            or
            (nexus_http_settings_before.json[0].result.data.nonProxyHosts | default([], true) | length == 0)
            or
            (
              (nexus_http_settings_before.json[0].result.data.nonProxyHosts | default([], true) | sort)
              == (nexus_outbound_proxy_non_proxy_hosts | sort)
            )"""

        for secret in (
            "NEXUS_ADMIN_PASSWORD",
            "PROXY_USERNAME",
            "PROXY_PASSWORD",
        ):
            self.assertIn(f"lookup('env', '{secret}') | length > 0", secret_gate)
        self.assertIn("no_log: true", secret_gate)
        self.assertNotIn("PROXY_PREVIOUS_PASSWORD", secret_gate)
        self.assertIn(
            "not (nexus_http_settings_before.json[0].result.data.httpsEnabled",
            settings_gate,
        )
        self.assertIn(
            compact(expected_disabled_non_proxy_gate), compact(settings_gate)
        )
        self.assertIn("NEXUS_ADMIN_PASSWORD: ${{ secrets.NEXUS_ADMIN_PASSWORD }}", fail_closed)
        self.assertIn("NEXUS_ADMIN_PASSWORD: ${{ secrets.NEXUS_ADMIN_PASSWORD }}", workflow_step("Deploy via Ansible"))

    def test_nexus_status_server_header_is_exact_and_empty_body_safe(self) -> None:
        version_read = task_block("Read Nexus version before System HTTP automation")
        version_gate = task_block("Require the pinned Nexus version for System HTTP automation")
        expected_condition = """nexus_outbound_proxy_version_check.server | default('')
          == 'Nexus/' ~ nexus_outbound_proxy_version ~ ' (COMMUNITY)'"""

        self.assertIn("method: GET", version_read)
        self.assertIn("status_code: 200", version_read)
        self.assertIn(compact(expected_condition), compact(version_gate))
        self.assertNotIn("nexus_outbound_proxy_version_check.json", version_gate)

        expected_header = "Nexus/3.94.0-12 (COMMUNITY)"
        for actual_header, accepted in (
            (expected_header, True),
            (None, False),
            ("", False),
            ("Nexus/3.93.0-01 (COMMUNITY)", False),
            ("Nexus/3.94.0-12 (PRO)", False),
        ):
            with self.subTest(server=actual_header):
                self.assertEqual((actual_header or "") == expected_header, accepted)

    def test_three_proxy_update_branches_are_task_scoped(self) -> None:
        decision = task_block("Decide whether Nexus System HTTP needs an explicit password rotation")
        previous_password_gate = task_block("Require the old password before recoverable proxy rotation")
        update = task_block("Update Nexus System HTTP proxy through the pinned ExtDirect contract")
        unrotated_invalidation = task_block("Invalidate cache before probing an unrotated enabled Nexus proxy")
        unrotated_probe = task_block("Probe an unrotated enabled Nexus proxy after cache invalidation")
        unrotated_when = """when:
        - nexus_outbound_proxy_existing_enabled | bool
        - not (nexus_outbound_proxy_needs_update | bool)"""
        expected_decision = """nexus_outbound_proxy_needs_update: >-
          {{
            not (nexus_outbound_proxy_existing_enabled | bool)
            or (lookup('env', 'PROXY_PREVIOUS_PASSWORD') | length > 0)
          }}"""
        expected_previous_password_gate = """not (nexus_outbound_proxy_needs_update | bool)
            or nexus_http_settings_before.json[0].result.data.httpAuthPassword != nexus_outbound_proxy_password_placeholder
            or (lookup('env', 'PROXY_PREVIOUS_PASSWORD') | length > 0)"""

        # An enabled proxy with PROXY_PREVIOUS_PASSWORD always writes the new password.
        self.assertIn(compact(expected_decision), compact(decision))
        self.assertIn("when: nexus_outbound_proxy_needs_update | bool", update)
        self.assertIn("'httpAuthPassword': lookup('env', 'PROXY_PASSWORD')", update)
        self.assertIn(compact(expected_previous_password_gate), compact(previous_password_gate))

        # An enabled proxy without the previous password only invalidates and probes.
        self.assertIn(unrotated_when, unrotated_invalidation)
        self.assertIn("status_code: 204", unrotated_invalidation)
        self.assertIn(unrotated_when, unrotated_probe)
        self.assertIn("status_code: 200", unrotated_probe)
        self.assertIn("until: nexus_docker_manifest_unrotated_probe.status == 200", unrotated_probe)

        # A disabled snapshot takes the first-write branch.
        self.assertIn(compact(expected_decision), compact(decision))
        self.assertIn("when: nexus_outbound_proxy_needs_update | bool", update)

        self.assertNotIn("nexus_docker_manifest_preflight", ROLE)
        self.assertIn("nexus_outbound_proxy_non_proxy_hosts | sort", ROLE)
        self.assertIn("== (nexus_outbound_proxy_non_proxy_hosts | sort)", ROLE)
        self.assertNotIn("| union(", ROLE)

    def test_redacted_snapshot_requires_previous_password_for_each_write_branch(self) -> None:
        previous_password_gate = task_block("Require the old password before recoverable proxy rotation")
        rollback_guard = task_block("Refuse unverifiable Nexus System HTTP rollback")
        rollback_restore = task_block("Restore Nexus System HTTP settings from the pre-update snapshot")

        self.assertIn("not (nexus_outbound_proxy_needs_update | bool)", previous_password_gate)
        self.assertIn("httpAuthPassword != nexus_outbound_proxy_password_placeholder", previous_password_gate)
        self.assertIn("PROXY_PREVIOUS_PASSWORD", previous_password_gate)
        self.assertNotIn("httpAuthEnabled", previous_password_gate)

        # A redacted password requires a previous value whenever the branch writes.
        # httpEnabled x previous-password: disabled/no is blocked; all other
        # branches either have the recovery value or make no write.
        for http_enabled, has_previous_password, writes, accepted in (
            (False, False, True, False),
            (False, True, True, True),
            (True, False, False, True),
            (True, True, True, True),
        ):
            with self.subTest(
                http_enabled=http_enabled,
                has_previous_password=has_previous_password,
            ):
                needs_update = not http_enabled or has_previous_password
                requires_previous_password = needs_update
                self.assertEqual(needs_update, writes)
                self.assertEqual(
                    not requires_previous_password or has_previous_password,
                    accepted,
                )

        self.assertLess(ROLE.index(rollback_guard), ROLE.index(rollback_restore))
        self.assertIn("httpAuthPassword != nexus_outbound_proxy_password_placeholder", rollback_guard)
        self.assertIn("PROXY_PREVIOUS_PASSWORD", rollback_guard)
        rollback_write_guard = """httpAuthPassword
          != nexus_outbound_proxy_password_placeholder
          or (lookup('env', 'PROXY_PREVIOUS_PASSWORD') | length > 0)"""
        self.assertIn(compact(rollback_write_guard), compact(rollback_restore))

    def test_rollback_verification_requires_enabled_snapshot_probe(self) -> None:
        rollback_restore = task_block("Restore Nexus System HTTP settings from the pre-update snapshot")
        rollback_metadata = task_block("Read Nexus System HTTP metadata after rollback")
        rollback_invalidation = task_block("Invalidate cache after restoring an enabled Nexus proxy snapshot")
        rollback_probe = task_block("Probe an enabled Nexus proxy after rollback")
        rollback_verified = task_block("Determine whether Nexus System HTTP rollback was verified")
        enabled_rollback_when = """when:
        - nexus_outbound_proxy_update_started | default(false) | bool
        - nexus_outbound_proxy_existing_enabled | default(false) | bool"""

        self.assertIn("when: nexus_outbound_proxy_update_started | default(false) | bool", rollback_metadata)
        self.assertIn("lookup('env', 'PROXY_PREVIOUS_PASSWORD')", rollback_restore)
        self.assertIn(
            "nexus_http_settings_before.json[0].result.data.httpAuthPassword == nexus_outbound_proxy_password_placeholder",
            rollback_restore,
        )
        self.assertIn(enabled_rollback_when, rollback_invalidation)
        self.assertIn("status_code: 204", rollback_invalidation)
        self.assertIn(enabled_rollback_when, rollback_probe)
        self.assertIn("status_code: 200", rollback_probe)
        self.assertIn("until: nexus_docker_manifest_rollback_probe.status == 200", rollback_probe)

        # Exact metadata is sufficient for disabled snapshots; enabled snapshots also
        # require cache invalidation and a fresh authenticated manifest response.
        metadata_clause = """and (
                (
                  nexus_http_settings_rollback_read.json[0].result.data
                  | combine({'httpAuthPassword': None, 'httpsAuthPassword': None}, recursive=True)
                )
                ==
                (
                  nexus_http_settings_before.json[0].result.data
                  | combine({'httpAuthPassword': None, 'httpsAuthPassword': None}, recursive=True)
                )
              )"""
        enabled_probe_clause = """and (
                not (nexus_outbound_proxy_existing_enabled | default(false) | bool)
                or
                (
                  nexus_docker_manifest_rollback_invalidation.status | default(0) | int == 204
                  and (
                    nexus_docker_manifest_rollback_probe.results | default([])
                    | map(attribute='status') | list
                  ) == [200, 200]
                )
              )"""
        rollback_contract = compact(rollback_verified)
        self.assertIn(compact(metadata_clause), rollback_contract)
        self.assertIn(compact(enabled_probe_clause), rollback_contract)
        self.assertRegex(
            rollback_contract,
            re.escape(compact(metadata_clause)) + r".*" + re.escape(compact(enabled_probe_clause)),
        )

    def test_cache_is_invalidated_before_authenticated_manifest_probe(self) -> None:
        invalidate = ROLE.index("Invalidate the Docker proxy cache after System HTTP update")
        probe = ROLE.index("Probe the authenticated Node manifest through Nexus")
        self.assertLess(invalidate, probe)
        self.assertIn("/repositories/{{ nexus_docker_proxy_repo }}/invalidate-cache", ROLE)
        self.assertIn("/v2/library/node/manifests/{{ item }}", ROLE)
        final_probe = task_block("Probe the authenticated Node manifest through Nexus")
        self.assertIn("loop: [20-alpine, 20-slim]", final_probe)
        self.assertIn("status_code: 200", final_probe)
        self.assertIn("retries: 5", final_probe)


if __name__ == "__main__":
    unittest.main()
