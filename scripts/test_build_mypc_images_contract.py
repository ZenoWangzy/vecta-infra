#!/usr/bin/env python3
"""Executable contract for the mypc production image workflow seam."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github/workflows/build-mypc-images.yml"
BUILDX_DEFAULTS_PATH = ROOT / "roles/infra-bootstrap/defaults/main.yml"
BUILDX_TASKS_PATH = ROOT / "roles/infra-bootstrap/tasks/buildx.yml"
INFRA_BOOTSTRAP_MAIN_PATH = ROOT / "roles/infra-bootstrap/tasks/main.yml"
BUILDX_VERSION = "v0.34.1"
BUILDX_SHA256 = (
    "f1332ddb9010bd0b72628266c3a906d9a6979848033df4c8d9bd2cd113bae12b"
)
BUILDX_PLUGIN_PATH = "/usr/local/lib/docker/cli-plugins/docker-buildx"


def job_allowed(event_name: str, repository: str, ref: str) -> bool:
    return (
        repository == "ZenoWangzy/vecta-infra"
        and event_name == "workflow_dispatch"
        and ref == "refs/heads/main"
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
        "github.repository == 'ZenoWangzy/vecta-infra' && "
        "github.event_name == 'workflow_dispatch' && "
        "github.ref == 'refs/heads/main'"
    )
    assert extract_job_condition(workflow) == expected_condition

    allowed = (
        (
            "workflow_dispatch",
            "ZenoWangzy/vecta-infra",
            "refs/heads/main",
        ),
    )
    rejected = (
        ("push", "ZenoWangzy/vecta", "refs/heads/main"),
        ("push", "ZenoWangzy/vecta", "refs/heads/develop"),
        ("pull_request", "ZenoWangzy/vecta", "refs/pull/42/merge"),
        ("workflow_call", "ZenoWangzy/vecta", "refs/heads/main"),
        ("workflow_dispatch", "ZenoWangzy/vecta", "refs/heads/main"),
        ("push", "ZenoWangzy/vecta-infra", "refs/heads/main"),
        (
            "workflow_dispatch",
            "ZenoWangzy/vecta-infra",
            "refs/heads/develop",
        ),
        ("push", "someone/fork", "refs/heads/main"),
    )
    assert all(job_allowed(*case) for case in allowed)
    assert not any(job_allowed(*case) for case in rejected)
    assert '"on":\n  workflow_dispatch:' in workflow
    trigger_block = workflow.split('"on":\n', 1)[1].split("\npermissions:", 1)[0]
    assert re.findall(r"^  ([a-z_]+):", trigger_block, re.MULTILINE) == [
        "workflow_dispatch"
    ]
    assert "  workflow_call:" not in workflow
    assert "\n    secrets:\n" not in workflow
    assert set(re.findall(r"\$\{\{ secrets\.([A-Z0-9_]+) \}\}", workflow)) == {
        "MYPC_NEXUS_ADMIN_PASSWORD",
        "PROXY_PASSWORD",
        "PROXY_USERNAME",
        "VECTA_READ_TOKEN",
    }

    required = (
        "runs-on: [self-hosted, mypc, prod-build]",
        "environment: production",
        "permissions:\n  contents: read",
        "SOURCE_SHA: ${{ inputs.source_sha }}",
        "SOURCE_BRANCH: ${{ inputs.source_branch }}",
        "grep -Eq '^[0-9a-f]{40}$'",
        "default: main",
        "only main is accepted.",
        'main) ;;',
        '*) echo "source_branch must be main: $SOURCE_BRANCH"',
        '"https://api.github.com/repos/ZenoWangzy/vecta/git/ref/heads/main"',
        'echo "source_sha must be the current VectA main HEAD"',
        'if [ "$branch_sha" != "$SOURCE_SHA" ]; then',
        'askpass="$(mktemp)"',
        "trap 'rm -f \"$curl_config\" \"$askpass\"' EXIT",
        'chmod 700 "$askpass"',
        "*Username*) printf '%s\\n' x-access-token ;;",
        "*Password*) printf '%s\\n' \"$VECTA_READ_TOKEN\" ;;",
        'GIT_ASKPASS="$askpass"',
        "GIT_ASKPASS_REQUIRE=force",
        "GIT_TERMINAL_PROMPT=0",
        "command -v timeout >/dev/null",
        "GIT_HTTP_LOW_SPEED_LIMIT=0",
        "LC_ALL=C",
        "git clone --depth=1 --branch main --single-branch",
        "https://github.com/ZenoWangzy/vecta.git vecta",
        "--depth=1 --branch main --single-branch",
        'git -C vecta switch --detach "$SOURCE_SHA"',
        'checkout_sha="$(git -C vecta rev-parse HEAD)"',
        'git -C vecta status --porcelain --untracked-files=all',
        "NEXUS_DOCKER_REGISTRY: 127.0.0.1:8082",
        "DOCKER_BASE_IMAGE_REGISTRY: 127.0.0.1:8082",
        "DOCKER_BASE_IMAGE_SOURCE_REGISTRY: 127.0.0.1:8083",
        "PRODUCTION_IMAGE_NAMES: ${{ inputs.image_names || '' }}",
        f"BUILDX_VERSION: {BUILDX_VERSION}",
        f"BUILDX_LINUX_AMD64_SHA256: {BUILDX_SHA256}",
        f"BUILDX_PLUGIN_PATH: {BUILDX_PLUGIN_PATH}",
        "command -v nice >/dev/null",
        "command -v ionice >/dev/null",
        "command -v sha256sum >/dev/null",
        'docker_config="$(mktemp -d "${RUNNER_TEMP}/vecta-docker-config.XXXXXX")"',
        'trap \'rm -rf "$docker_config"\' EXIT',
        'export DOCKER_CONFIG="$docker_config"',
        'if [ ! -x "$BUILDX_PLUGIN_PATH" ]; then',
        'echo "Buildx plugin is required at $BUILDX_PLUGIN_PATH"',
        '"$BUILDX_LINUX_AMD64_SHA256" "$BUILDX_PLUGIN_PATH"',
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
    checkout_step = "- name: Checkout infra contract"
    fruit_contract_step = "- name: Validate Fruit V4 isolated Compose contract"
    assert workflow.count(checkout_step) == 1
    assert workflow.count(fruit_contract_step) == 1
    assert workflow.index(checkout_step) < workflow.index(fruit_contract_step)
    assert "- name: Login to production Nexus Docker registry" not in workflow
    assert workflow.count("image_names:") == 1
    assert "main|develop" not in workflow
    assert "          - develop" not in workflow
    assert "github.com/docker/buildx" not in workflow
    assert "docker-buildx.download" not in workflow
    assert "curl " not in extract_step_script(
        workflow, "Build and push production images"
    )
    assert "docker buildx create" not in workflow
    assert "docker-container" not in workflow
    retired_environment = "".join(("v", "test"))
    assert f"check:{retired_environment}-images" not in workflow
    assert f"build-push-{retired_environment}-images.mjs" not in workflow
    assert f"{retired_environment.upper()}_IMAGE_NAMES" not in workflow
    assert "GITHUB_SHA: ${{ inputs.source_sha }}" not in workflow
    assert "/tarball/${SOURCE_SHA}" not in workflow
    download_script = extract_step_script(workflow, "Download selected VectA source")
    clone_prefix = (
        "if GIT_HTTP_LOW_SPEED_LIMIT=0 \\\n"
        "    LC_ALL=C \\\n"
        '    GIT_ASKPASS="$askpass" \\\n'
        "    GIT_ASKPASS_REQUIRE=force \\\n"
        "    GIT_TERMINAL_PROMPT=0 \\\n"
        "    timeout 30m git clone --depth=1 --branch main --single-branch"
    )
    assert clone_prefix in download_script
    assert "for attempt in 1 2 3; do" in download_script
    assert 'rm -rf vecta' in download_script
    assert 'if [ "$attempt" -eq 3 ]; then' in download_script
    assert 'echo "VectA clone failed after 3 attempts" >&2' in download_script
    assert workflow.count("GIT_HTTP_LOW_SPEED_LIMIT") == 1
    assert "GIT_HTTP_LOW_SPEED_TIME" not in workflow
    assert not re.search(r"\bgh\b", download_script)
    assert not re.search(r"\bgit\b[^\n]*\bconfig\b", download_script)
    assert "GIT_CONFIG_" not in download_script
    assert not re.search(r"\bset\s+-x\b|\bxtrace\b", download_script)
    assert not re.search(
        r"\becho\b[^\n]*\$(?:VECTA_READ_TOKEN|\{VECTA_READ_TOKEN[^}]*\})",
        download_script,
    )
    assert download_script.count("git clone ") == 1
    assert download_script.count("https://github.com/ZenoWangzy/vecta.git") == 1
    assert not re.search(r"https://[^\s/]*@github\.com", download_script)


def assert_provisioning_contract() -> None:
    defaults = BUILDX_DEFAULTS_PATH.read_text()
    tasks = BUILDX_TASKS_PATH.read_text()
    role_main = INFRA_BOOTSTRAP_MAIN_PATH.read_text()

    for literal in (
        f"infra_buildx_version: {BUILDX_VERSION}",
        BUILDX_SHA256,
        "https://github.com/docker/buildx/releases/download/"
        "{{ infra_buildx_version }}/buildx-"
        "{{ infra_buildx_version }}.linux-amd64",
        f"infra_buildx_plugin_path: {BUILDX_PLUGIN_PATH}",
    ):
        assert literal in defaults, literal

    assert "- import_tasks: buildx.yml\n  tags: [buildx]" in role_main
    download = tasks.split(
        "- name: Download the pinned Buildx asset on the controller\n", 1
    )[1].split("\n- name:", 1)[0]
    for literal in (
        "ansible.builtin.get_url:",
        'checksum: "sha256:{{ infra_buildx_linux_amd64_sha256 }}"',
        "timeout: 30",
        "delegate_to: localhost",
        "become: false",
        "run_once: true",
        "retries: 3",
        "delay: 5",
        "until: infra_buildx_download is succeeded",
        "when: not ansible_check_mode",
    ):
        assert literal in download, literal

    copy = tasks.split(
        "- name: Install the pinned Buildx plugin from the controller\n", 1
    )[1]
    for literal in (
        "ansible.builtin.copy:",
        'dest: "{{ infra_buildx_plugin_path }}"',
        'owner: root',
        'group: root',
        'mode: "0755"',
        "when: not ansible_check_mode",
    ):
        assert literal in copy, literal

    assert BUILDX_SHA256 in tasks
    assert (
        "https://github.com/docker/buildx/releases/download/"
        f"{BUILDX_VERSION}/buildx-{BUILDX_VERSION}.linux-amd64"
    ) in tasks
    assert BUILDX_PLUGIN_PATH in tasks
    for forbidden in (
        "community.docker",
        "ansible.builtin.command",
        "docker login",
        "registry",
    ):
        assert forbidden not in tasks, forbidden


def write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def run_fake_build(
    build_script: str,
    *,
    plugin_present: bool = True,
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
        plugin_path = temporary_path / "system" / "docker-buildx"
        if plugin_present:
            plugin_path.parent.mkdir()
            write_executable(plugin_path, "#!/bin/sh\nexit 0\n")
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
[ "$actual_path" = "$BUILDX_PLUGIN_PATH" ] || exit 43
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
                "BUILDX_PLUGIN_PATH": str(plugin_path),
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
    assert f"sha256sum {BUILDX_SHA256}" in success_log
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
        ("missing", run_fake_build(build_script, plugin_present=False)),
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
    assert_provisioning_contract()
    assert_fake_contract(workflow)
    print("build-mypc images contract: ok")


if __name__ == "__main__":
    main()
