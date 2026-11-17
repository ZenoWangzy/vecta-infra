#!/usr/bin/env python3
"""Static contract for the manually dispatched mypc release workflow."""

import json
from pathlib import Path
import subprocess

try:
    import yaml
except ImportError:
    yaml = None


ROOT = Path(__file__).resolve().parents[1]
RELEASE_PATH = ROOT / ".github/workflows/mypc-release.yml"
BUILD_PATH = ROOT / ".github/workflows/build-mypc-images.yml"
FETCH_SCRIPT_PATH = ROOT / "scripts/fetch-vecta-source.sh"
RELEASE = RELEASE_PATH.read_text()
BUILD = BUILD_PATH.read_text()


def require(fragment: str, source: str = RELEASE) -> None:
    assert fragment in source, fragment


def parse_workflow_yaml(source: str) -> dict[str, object]:
    if yaml is not None:
        parsed = yaml.safe_load(source)
    else:
        parser = (
            "const YAML = require('yaml'); "
            "const parsed = YAML.parse(require('fs').readFileSync(0, 'utf8')); "
            "process.stdout.write(JSON.stringify(parsed));"
        )
        result = subprocess.run(
            ["node", "-e", parser], input=source, text=True, capture_output=True, check=True
        )
        parsed = json.loads(result.stdout)
    assert isinstance(parsed, dict)
    return parsed


def steps_for(workflow: dict[str, object], job_name: str) -> list[dict[str, object]]:
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    job = jobs[job_name]
    assert isinstance(job, dict)
    steps = job["steps"]
    assert isinstance(steps, list)
    return steps


def main() -> None:
    release = parse_workflow_yaml(RELEASE)
    build = parse_workflow_yaml(BUILD)

    for name in ("source_sha", "mode", "services", "dry_run"):
        require(f"      {name}:")
    require("workflow_dispatch:")
    require("type: choice\n        required: true\n        default: release")
    require("          - release\n          - hotfix")
    require("type: boolean")
    require("default: false")
    require("runs-on: [self-hosted, mypc, prod-build]")
    require("environment: production")
    require("source_sha must be a full lowercase 40-character SHA")
    require("scripts/verify-vecta-postsubmit.py")
    require("--branch main")
    require("uses: ./.github/workflows/build-mypc-images.yml")
    require("image_names: ${{ inputs.services }}")
    require("MYPC_NEXUS_ADMIN_PASSWORD: ${{ secrets.MYPC_NEXUS_ADMIN_PASSWORD }}")
    require("VECTA_READ_TOKEN: ${{ secrets.VECTA_READ_TOKEN }}")
    require("DEPLOY_HOST: root@127.0.0.1")
    require("ssh -o BatchMode=yes -o ConnectTimeout=5 root@127.0.0.1 true")
    require("openssh-client")
    require("bash vecta/scripts/release/mypc-release.sh")
    require("RELEASE_BUILD_ALREADY_COMPLETED:")
    require("$HOME/.vecta-agent/mypc-release")
    require("secrets.VECTA_PRODUCTION_ACCEPTANCE_ENV")
    require("secrets.VECTA_PRODUCTION_RELEASE_TOKEN")
    require("secrets.VECTA_PRODUCTION_READ_TOKEN")
    require("Remove authenticated acceptance credentials")
    require("inputs.dry_run == true")
    require("without acquiring the production lock or changing containers")
    require("workflow_call:\n    inputs:", BUILD)
    require(
        "secrets:\n      MYPC_NEXUS_ADMIN_PASSWORD:\n        required: true\n"
        "      VECTA_READ_TOKEN:\n        required: true",
        BUILD,
    )
    require("github.event_name == 'workflow_call'", BUILD)

    vecta_checkouts = []
    for job_name in ("validate", "deploy"):
        for step in steps_for(release, job_name):
            options = step.get("with", {})
            if isinstance(options, dict) and options.get("repository") == "ZenoWangzy/vecta":
                vecta_checkouts.append(step)
                assert options.get("fetch-depth") not in (0, "0"), step
    assert not vecta_checkouts, "VectA source must use the shared fetch script, not actions/checkout"

    for job_name in ("validate", "deploy"):
        fetch_steps = [
            step for step in steps_for(release, job_name)
            if "bash scripts/fetch-vecta-source.sh --with-main-history" in str(step.get("run", ""))
        ]
        assert len(fetch_steps) == 1, f"{job_name} must fetch VectA source and main history with the shared script"

    engine_build_steps = [
        step for step in steps_for(release, "deploy")
        if step.get("name") == "Build the pinned Vecta engine pack"
    ]
    assert len(engine_build_steps) == 1
    engine_build_run = str(engine_build_steps[0].get("run", ""))
    assert 'VECTA_DIR="$GITHUB_WORKSPACE/vecta"' in engine_build_run
    assert 'ZF_TARGET_CHECKOUT="$GITHUB_WORKSPACE/vecta"' in engine_build_run

    assert FETCH_SCRIPT_PATH.is_file(), FETCH_SCRIPT_PATH
    build_fetch_steps = [
        step for step in steps_for(build, "build")
        if "Download selected VectA source" in str(step.get("name", ""))
    ]
    assert len(build_fetch_steps) == 1
    assert "bash scripts/fetch-vecta-source.sh" in str(build_fetch_steps[0].get("run", ""))

    deploy = RELEASE.split("  deploy:", 1)[1]
    first_deploy_step = deploy.split("    steps:\n", 1)[1].lstrip().splitlines()[0]
    assert first_deploy_step == "- name: Preflight root SSH to local runner"
    assert RELEASE.count("ssh -o BatchMode=yes -o ConnectTimeout=5 root@127.0.0.1 true") == 1
    assert "secrets: inherit" not in RELEASE
    assert "RELEASE_DEPLOY_LOCK_SCRIPT" not in RELEASE
    assert "9222" not in RELEASE
    assert "run-release.sh" not in RELEASE
    assert "runs-on: [self-hosted, mypc, prod-build]" in RELEASE
    assert RELEASE.count("secrets.VECTA_PRODUCTION_ACCEPTANCE_ENV") == 1
    assert "secrets.VECTA_RELEASE_TOKEN" not in RELEASE
    print("mypc release workflow contract: PASS")


if __name__ == "__main__":
    main()
