#!/usr/bin/env python3
"""Executable contract for the mypc production image workflow seam."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github/workflows/build-mypc-images.yml"
BUILDX_VERSION = "v0.34.1"
BUILDX_SHA256 = (
    "f1332ddb9010bd0b72628266c3a906d9a6979848033df4c8d9bd2cd113bae12b"
)


def job_allowed(event_name: str, repository: str) -> bool:
    return (
        event_name == "workflow_call" and repository == "ZenoWangzy/vecta"
    ) or (
        event_name == "workflow_dispatch"
        and repository == "ZenoWangzy/vecta-infra"
    )


def extract_job_condition(workflow: str) -> str:
    condition = workflow.split("    if: >-\n", 1)[1].split(
        "    runs-on:", 1
    )[0]
    return " ".join(line.strip() for line in condition.splitlines())


def extract_step_script(workflow: str, step_name: str) -> str:
    lines = workflow.splitlines()
    marker = f"      - name: {step_name}"
    start = lines.index(marker)
    run_line = next(
        index
        for index in range(start + 1, len(lines))
        if lines[index] == "        run: |"
    )
    body: list[str] = []
    for line in lines[run_line + 1 :]:
        if line and len(line) - len(line.lstrip()) <= 8:
            break
        body.append(line[10:] if line else "")
    return "\n".join(body)


def assert_static_contract(workflow: str) -> None:
    expected_condition = (
        "(github.event_name == 'workflow_call' && "
        "github.repository == 'ZenoWangzy/vecta') || "
        "(github.event_name == 'workflow_dispatch' && "
        "github.repository == 'ZenoWangzy/vecta-infra')"
    )
    assert extract_job_condition(workflow) == expected_condition

    allowed = (
        ("workflow_call", "ZenoWangzy/vecta"),
        ("workflow_dispatch", "ZenoWangzy/vecta-infra"),
    )
    rejected = (
        ("workflow_call", "ZenoWangzy/vecta-infra"),
        ("workflow_dispatch", "ZenoWangzy/vecta"),
        ("workflow_call", "someone/fork"),
        ("push", "ZenoWangzy/vecta-infra"),
        ("pull_request", "ZenoWangzy/vecta"),
    )
    assert all(job_allowed(*case) for case in allowed)
    assert not any(job_allowed(*case) for case in rejected)

    required = (
        "runs-on: [self-hosted, mypc, prod-build]",
        "environment: production",
        "permissions:\n  contents: read",
        "SOURCE_SHA: ${{ inputs.source_sha }}",
        "SOURCE_BRANCH: ${{ inputs.source_branch }}",
        "grep -Eq '^[0-9a-f]{40}$'",
        'if [ "$branch_sha" != "$SOURCE_SHA" ]; then',
        '"https://api.github.com/repos/ZenoWangzy/vecta/tarball/${SOURCE_SHA}"',
        "NEXUS_DOCKER_REGISTRY: 127.0.0.1:8082",
        "DOCKER_BASE_IMAGE_REGISTRY: 127.0.0.1:8082",
        "DOCKER_BASE_IMAGE_SOURCE_REGISTRY: 127.0.0.1:8082",
        "PRODUCTION_IMAGE_NAMES: ${{ inputs.image_names || '' }}",
        f"BUILDX_VERSION: {BUILDX_VERSION}",
        f"BUILDX_LINUX_AMD64_SHA256: {BUILDX_SHA256}",
        "command -v nice >/dev/null",
        "command -v ionice >/dev/null",
        "command -v sha256sum >/dev/null",
        'docker_config="$(mktemp -d "${RUNNER_TEMP}/vecta-docker-config.XXXXXX")"',
        'trap \'rm -rf "$docker_config"\' EXIT',
        'export DOCKER_CONFIG="$docker_config"',
        "https://github.com/docker/buildx/releases/download/"
        "${BUILDX_VERSION}/buildx-${BUILDX_VERSION}.linux-amd64",
        "sha256sum --check -",
        'actual_buildx_version="$(docker buildx version | awk',
        "docker buildx use default",
        'builder_info="$(docker buildx inspect default)"',
        "'^Name:[[:space:]]+default$'",
        "'^Driver:[[:space:]]+docker$'",
        'docker login "$NEXUS_DOCKER_REGISTRY" -u admin --password-stdin',
        "run: pnpm check:production-images",
        "nice -n 10 ionice -c2 -n7 \\\n"
        "            node scripts/build-push-production-images.mjs",
        "NEXUS_ADMIN_PASSWORD: ${{ secrets.MYPC_NEXUS_ADMIN_PASSWORD }}",
    )
    for literal in required:
        assert literal in workflow, literal

    assert (
        'docker_config="$(mktemp -d '
        '"${RUNNER_TEMP}/vecta-docker-config.XXXXXX")"\n'
        '          trap \'rm -rf "$docker_config"\' EXIT'
    ) in workflow
    assert workflow.count("- name: Build and push production images") == 1
    assert "- name: Login to production Nexus Docker registry" not in workflow
    assert workflow.count("image_names:") == 2
    assert "docker buildx create" not in workflow
    assert "docker-container" not in workflow
    retired_environment = "".join(("v", "test"))
    assert f"check:{retired_environment}-images" not in workflow
    assert f"build-push-{retired_environment}-images.mjs" not in workflow
    assert f"{retired_environment.upper()}_IMAGE_NAMES" not in workflow
    assert "GITHUB_SHA: ${{ inputs.source_sha }}" not in workflow


def write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def run_fake_build(
    build_script: str,
    *,
    sha_failure: bool = False,
    version: str = BUILDX_VERSION,
    driver: str = "docker",
) -> tuple[subprocess.CompletedProcess[str], str, list[Path]]:
    with tempfile.TemporaryDirectory() as temporary:
        temporary_path = Path(temporary)
        fake_bin = temporary_path / "bin"
        runner_temp = temporary_path / "runner"
        fake_bin.mkdir()
        runner_temp.mkdir()
        log_path = temporary_path / "calls.log"

        write_executable(
            fake_bin / "curl",
            """#!/bin/sh
set -eu
printf 'curl' >> "$FAKE_LOG"
printf ' %s' "$@" >> "$FAKE_LOG"
printf '\n' >> "$FAKE_LOG"
output=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output) output="$2"; shift 2 ;;
    *) shift ;;
  esac
done
[ -n "$output" ]
printf 'fake buildx binary\n' > "$output"
""",
        )
        write_executable(
            fake_bin / "sha256sum",
            """#!/bin/sh
set -eu
input="$(cat)"
printf 'sha256sum %s\n' "$input" >> "$FAKE_LOG"
[ "${FAKE_SHA_FAILURE:-0}" != 1 ] || exit 42
actual_sha="${input%% *}"
actual_path="${input#*  }"
[ "$actual_sha" = "$EXPECTED_BUILDX_SHA" ]
case "$actual_path" in
  */docker-buildx.download) ;;
  *) exit 43 ;;
esac
""",
        )
        write_executable(
            fake_bin / "docker",
            """#!/bin/sh
set -eu
printf 'docker' >> "$FAKE_LOG"
printf ' %s' "$@" >> "$FAKE_LOG"
printf '\n' >> "$FAKE_LOG"
if [ "${1:-}" = buildx ] && [ "${2:-}" = version ]; then
  printf 'github.com/docker/buildx %s fake-revision\n' "$FAKE_BUILDX_VERSION"
elif [ "${1:-}" = buildx ] && [ "${2:-}" = use ]; then
  [ "${3:-}" = default ]
elif [ "${1:-}" = buildx ] && [ "${2:-}" = inspect ]; then
  [ "${3:-}" = default ]
  printf 'Name: default\nDriver: %s\n' "$FAKE_BUILDX_DRIVER"
elif [ "${1:-}" = login ]; then
  cat >/dev/null
else
  exit 44
fi
""",
        )
        write_executable(
            fake_bin / "nice",
            """#!/bin/sh
set -eu
[ "$#" -gt 0 ] || exit 45
printf 'nice %s\n' "$*" >> "$FAKE_LOG"
[ "$1" = -n ]
[ "$2" = 10 ]
shift 2
[ "$#" -gt 0 ] || exit 46
exec "$@"
""",
        )
        write_executable(
            fake_bin / "ionice",
            """#!/bin/sh
set -eu
[ "$#" -gt 0 ] || exit 47
printf 'ionice %s\n' "$*" >> "$FAKE_LOG"
[ "$1" = -c2 ]
[ "$2" = -n7 ]
shift 2
[ "$#" -gt 0 ] || exit 48
exec "$@"
""",
        )
        write_executable(
            fake_bin / "node",
            """#!/bin/sh
set -eu
[ "$1" = scripts/build-push-production-images.mjs ]
printf 'node %s\n' "$*" >> "$FAKE_LOG"
""",
        )

        environment = os.environ.copy()
        environment.update(
            {
                "PATH": f"{fake_bin}:{environment['PATH']}",
                "RUNNER_TEMP": str(runner_temp),
                "BUILDX_VERSION": BUILDX_VERSION,
                "BUILDX_LINUX_AMD64_SHA256": BUILDX_SHA256,
                "EXPECTED_BUILDX_SHA": BUILDX_SHA256,
                "FAKE_SHA_FAILURE": "1" if sha_failure else "0",
                "FAKE_BUILDX_VERSION": version,
                "FAKE_BUILDX_DRIVER": driver,
                "FAKE_LOG": str(log_path),
                "NEXUS_ADMIN_PASSWORD": "contract-secret",
                "NEXUS_DOCKER_REGISTRY": "127.0.0.1:8082",
            }
        )
        for stub_name in ("nice", "ionice"):
            empty_stub = subprocess.run(
                [str(fake_bin / stub_name)],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            assert empty_stub.returncode != 0, stub_name
        result = subprocess.run(
            ["bash", "-c", build_script],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        log = log_path.read_text() if log_path.exists() else ""
        leftovers = list(runner_temp.glob("vecta-docker-config.*"))
        return result, log, leftovers


def assert_fake_contract(workflow: str) -> None:
    build_script = extract_step_script(workflow, "Build and push production images")

    success, success_log, success_leftovers = run_fake_build(build_script)
    assert success.returncode == 0, success.stderr
    assert f"buildx-{BUILDX_VERSION}.linux-amd64" in success_log
    assert "docker buildx version" in success_log
    assert "docker buildx use default" in success_log
    assert "docker buildx inspect default" in success_log
    assert "docker login 127.0.0.1:8082 -u admin --password-stdin" in success_log
    assert (
        "nice -n 10 ionice -c2 -n7 "
        "node scripts/build-push-production-images.mjs"
    ) in success_log
    assert (
        "ionice -c2 -n7 node scripts/build-push-production-images.mjs"
    ) in success_log
    assert "node scripts/build-push-production-images.mjs" in success_log
    assert not success_leftovers

    failures = (
        ("checksum", run_fake_build(build_script, sha_failure=True)),
        ("version", run_fake_build(build_script, version="v0.34.0")),
        ("driver", run_fake_build(build_script, driver="docker-container")),
    )
    for label, (result, log, leftovers) in failures:
        assert result.returncode != 0
        assert "node scripts/build-push-production-images.mjs" not in log
        assert not leftovers, f"{label}: temporary DOCKER_CONFIG was not removed"


def main() -> None:
    workflow = WORKFLOW_PATH.read_text()
    assert_static_contract(workflow)
    assert_fake_contract(workflow)
    print("build-mypc images contract: ok")


if __name__ == "__main__":
    main()
