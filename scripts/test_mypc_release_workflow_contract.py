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
    require("scripts/deploy-lock.sh")
    require("bash vecta/scripts/release/mypc-release.sh")
    require("RELEASE_BUILD_ALREADY_COMPLETED:")
    require("$HOME/.vecta-agent/mypc-release")
    require("secrets.VECTA_PRODUCTION_ACCEPTANCE_ENV")
    require("secrets.VECTA_RELEASE_TOKEN")
    require("Remove authenticated acceptance credentials")
    require("inputs.dry_run == true")
    require("without compose changes")
    require("workflow_call:\n    inputs:", BUILD)
    require("github.event_name == 'workflow_call'", BUILD)

    assert "ssh " not in RELEASE
    assert "9222" not in RELEASE
    assert "run-release.sh" not in RELEASE
    assert "runs-on: [self-hosted, mypc, prod-build]" in RELEASE
    assert RELEASE.count("secrets.VECTA_PRODUCTION_ACCEPTANCE_ENV") == 1
    print("mypc release workflow contract: PASS")


if __name__ == "__main__":
    main()
