#!/usr/bin/env python3
"""Static contract for the manually dispatched mypc release workflow."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE = (ROOT / ".github/workflows/mypc-release.yml").read_text()
BUILD = (ROOT / ".github/workflows/build-mypc-images.yml").read_text()


def require(fragment: str, source: str = RELEASE) -> None:
    assert fragment in source, fragment


def main() -> None:
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
    require("scripts/deploy-lock.sh")
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
        "secrets:\n      MYPC_NEXUS_ADMIN_PASSWORD:\n        required: true\n      VECTA_READ_TOKEN:\n        required: true",
        BUILD,
    )
    require("github.event_name == 'workflow_call'", BUILD)

    deploy = RELEASE.split("  deploy:", 1)[1]
    first_deploy_step = deploy.split("    steps:\n", 1)[1].lstrip().splitlines()[0]
    assert first_deploy_step == "- name: Preflight root SSH to local runner"
    assert RELEASE.count("ssh -o BatchMode=yes -o ConnectTimeout=5 root@127.0.0.1 true") == 1
    assert "secrets: inherit" not in RELEASE
    assert "9222" not in RELEASE
    assert "run-release.sh" not in RELEASE
    assert "runs-on: [self-hosted, mypc, prod-build]" in RELEASE
    assert RELEASE.count("secrets.VECTA_PRODUCTION_ACCEPTANCE_ENV") == 1
    assert "secrets.VECTA_RELEASE_TOKEN" not in RELEASE
    print("mypc release workflow contract: PASS")


if __name__ == "__main__":
    main()
