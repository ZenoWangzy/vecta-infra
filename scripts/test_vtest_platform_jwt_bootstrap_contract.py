#!/usr/bin/env python3
"""Keep the temporary reusable-workflow JWT declarations declaration-only."""

from pathlib import Path
import json
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github/workflows/_deploy-vtest-job.yml"
WORKFLOW = WORKFLOW_PATH.read_text()
LEGACY_NAMES = (
    "VTEST_PLATFORM_SERVICE_JWT_PUBLIC_KEY",
    "VTEST_CHANNEL_PLATFORM_SERVICE_TOKEN",
    "VTEST_FLEET_PLATFORM_SERVICE_TOKEN",
    "VTEST_FLEET_FRUIT_PLATFORM_TOKEN",
)


def workflow_call_secrets() -> dict[str, object]:
    """Read the workflow through a YAML AST, not indentation assumptions."""

    ruby = r'''
require "json"
require "yaml"
document = YAML.safe_load($stdin.read, aliases: true)
workflow = document["on"] || document[true]
secrets = workflow.fetch("workflow_call").fetch("secrets")
puts JSON.generate(secrets)
'''
    result = subprocess.run(
        ["ruby", "-ryaml", "-rjson", "-e", ruby],
        input=WORKFLOW,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"workflow YAML AST parse failed: {result.stderr}")
    return json.loads(result.stdout)


class VtestPlatformJwtBootstrapContractTest(unittest.TestCase):
    def test_legacy_inputs_are_optional_workflow_call_declarations(self) -> None:
        secrets = workflow_call_secrets()
        expected_names = set(LEGACY_NAMES) | {
            "VTEST_DATABASE_URL",
            "SERVICE_KEY",
            "NEXUS_ADMIN_PASSWORD",
            "WECOM_CORP_ID",
            "WECOM_CONTACTS_SECRET",
            "WECOM_BOT_ID",
            "WECOM_BOT_SECRET",
            "PROXY_USERNAME",
            "PROXY_PASSWORD",
            "PROXY_PREVIOUS_PASSWORD",
        }
        self.assertEqual(set(secrets), expected_names)
        for name in LEGACY_NAMES:
            self.assertEqual(secrets[name], {"required": False})

    def test_legacy_inputs_are_never_consumed_as_secrets(self) -> None:
        direct_reference = re.compile(r"\$\{\{\s*secrets\.([A-Z0-9_]+)\s*\}\}")
        for path in ROOT.rglob("*"):
            if not path.is_file() or ".git" in path.parts:
                continue
            text = path.read_text(errors="replace")
            for match in direct_reference.finditer(text):
                self.assertNotIn(
                    match.group(1),
                    LEGACY_NAMES,
                    f"obsolete JWT secret consumed in {path.relative_to(ROOT)}",
                )

    def test_bootstrap_comment_and_removal_condition_are_recorded_without_values(self) -> None:
        self.assertIn("bootstrap-only; unused; remove immediately after Vecta PR454 merges", WORKFLOW)
        runbook = (ROOT / "docs/runbooks/vtest-platform-jwt-bootstrap.md").read_text()
        self.assertIn("31614583502", runbook)
        self.assertIn("31616996956", runbook)
        self.assertIn("31617601252", runbook)
        self.assertIn("PR `#454` 合并后", runbook)
        self.assertNotRegex(runbook, r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


if __name__ == "__main__":
    unittest.main()
