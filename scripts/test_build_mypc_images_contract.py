#!/usr/bin/env python3
"""Executable contract for the mypc production image workflow seam."""

from __future__ import annotations

import os
import json
from pathlib import Path
import re
import shutil
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
        '"https://api.github.com/repos/ZenoWangzy/vecta/compare/$SOURCE_SHA...$SOURCE_BRANCH?per_page=1"',
        'if [ "$merge_base_sha" != "$SOURCE_SHA" ]; then',
        'echo "source_sha must be an ancestor of the current VectA main HEAD"',
        'merge_base_commit?.sha ?? ""',
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
        'git -C vecta fetch --depth=1 "$1" "$SOURCE_SHA"',
        'remote="https://github.com/ZenoWangzy/vecta.git"',
        'git -C vecta switch --detach "$SOURCE_SHA"',
        'checkout_sha="$(git -C vecta rev-parse HEAD)"',
        'git -C vecta status --porcelain --untracked-files=all',
        "NEXUS_DOCKER_REGISTRY: 127.0.0.1:8082",
        "DOCKER_BASE_IMAGE_REGISTRY: 127.0.0.1:8082",
        "DOCKER_BASE_IMAGE_SOURCE_REGISTRY: 127.0.0.1:8083",
        "PRODUCTION_IMAGE_NAMES: ${{ inputs.image_names || '' }}",
        "scripts/verify-vecta-postsubmit.py",
        "--repo ZenoWangzy/vecta",
        '--sha "$SOURCE_SHA"',
        '--branch "$SOURCE_BRANCH"',
        f"BUILDX_VERSION: {BUILDX_VERSION}",
        f"BUILDX_PLUGIN_PATH: {BUILDX_PLUGIN_PATH}",
        'docker_config="$(mktemp -d "${RUNNER_TEMP}/vecta-docker-config.XXXXXX")"',
        'trap \'rm -rf "$docker_config"\' EXIT',
        'export DOCKER_CONFIG="$docker_config"',
        'if [ ! -x "$BUILDX_PLUGIN_PATH" ]; then',
        'echo "Buildx plugin is required at $BUILDX_PLUGIN_PATH"',
        'actual_buildx_version="$(docker buildx version | awk',
        "docker buildx use default",
        'builder_info="$(docker buildx inspect default)"',
        "'^Name:[[:space:]]+default$'",
        "'^Driver:[[:space:]]+docker$'",
        'docker login "$NEXUS_DOCKER_REGISTRY" -u admin --password-stdin',
        "run: pnpm check:production-images",
        "          node scripts/build-push-production-images.mjs",
        "NEXUS_ADMIN_PASSWORD: ${{ secrets.MYPC_NEXUS_ADMIN_PASSWORD }}",
    )
    for literal in required:
        assert literal in workflow, literal
    assert 'AFFECTED_SINCE_SHA: ${{ inputs.affected_since_sha }}' in workflow
    assert 'default: never' not in workflow
    assert 'command -v nice' not in workflow
    assert 'command -v ionice' not in workflow
    assert workflow.index('- name: Validate requested production images') < workflow.index('- name: Ensure production Nexus is running')

    nexus_script = extract_step_script(workflow, "Ensure production Nexus is running")
    secret_check = (
        'if curl -fsS \\\n'
        '    -u "admin:${NEXUS_ADMIN_PASSWORD}" \\\n'
        '    http://127.0.0.1:8081/service/rest/v1/status >/dev/null 2>&1; then'
    )
    password_read = 'initial_password="$(docker exec vecta-nexus \\\n'
    stale_path = (
        'stale_password_path="/nexus-data/admin.password.stale-'
        '${GITHUB_RUN_ID:-manual}-${GITHUB_RUN_ATTEMPT:-1}"'
    )
    file_password_check = '-u "admin:${initial_password}"'
    stale_rename = (
        'docker exec vecta-nexus mv /nexus-data/admin.password '
        '"$stale_password_path"'
    )
    assert secret_check in nexus_script
    secret_check_at = nexus_script.index(secret_check)
    password_read_at = nexus_script.index(password_read)
    assert secret_check_at < password_read_at
    assert (
        "else"
        in nexus_script[secret_check_at + len(secret_check) : password_read_at]
    )
    assert password_read_at < nexus_script.index(file_password_check)
    assert stale_path in nexus_script
    assert nexus_script.count(stale_rename) == 2
    assert "admin secret status check failed" in nexus_script
    assert "admin.password change-password check failed" in nexus_script
    assert "rm -f /nexus-data/admin.password" not in nexus_script

    assert (
        'docker_config="$(mktemp -d '
        '"${RUNNER_TEMP}/vecta-docker-config.XXXXXX")"\n'
        '          trap \'rm -rf "$docker_config"\' EXIT'
    ) in workflow
    assert workflow.count("- name: Build and push production images") == 1
    assert workflow.count("- name: Seed verified Hermes base image") == 1
    checkout_step = "- name: Checkout infra contract"
    fruit_contract_step = "- name: Validate Fruit V4 isolated Compose contract"
    hermes_seed_step = "- name: Seed verified Hermes base image"
    node_runtime_seed_step = "- name: Seed verified Node runtime base image"
    image_build_step = "- name: Build and push production images"
    postsubmit_step = "- name: Require green VectA Postsubmit evidence"
    nexus_step = "- name: Ensure production Nexus is running"
    assert workflow.count(checkout_step) == 1
    assert workflow.count(postsubmit_step) == 1
    assert workflow.count(fruit_contract_step) == 1
    assert workflow.count(node_runtime_seed_step) == 1
    assert workflow.index(checkout_step) < workflow.index(fruit_contract_step)
    assert workflow.index(postsubmit_step) < workflow.index(image_build_step)
    assert workflow.index(postsubmit_step) < workflow.index(nexus_step)
    assert workflow.index(hermes_seed_step) < workflow.index(image_build_step)
    assert workflow.index(node_runtime_seed_step) < workflow.index(image_build_step)
    assert "uses: actions/checkout@" not in workflow
    proxy_step = "- name: Configure git proxy"
    assert workflow.count(proxy_step) == 1
    assert workflow.index(proxy_step) < workflow.index(checkout_step)
    for literal in (
        'LOCAL_PROXY="http://127.0.0.1:3129"',
        'curl -sS -o /dev/null -m 15 -x "$LOCAL_PROXY" --noproxy \'\' "$DOOR"',
        'GIT_CONFIG_GLOBAL="$RUNNER_TEMP/vecta-infra-git-proxy-gitconfig"',
        'echo "GIT_CONFIG_GLOBAL=$GIT_CONFIG_GLOBAL" >> "$GITHUB_ENV"',
        'git config --global http.proxy "$LOCAL_PROXY"',
        'git config --global https.proxy "$LOCAL_PROXY"',
        'printf \'https_proxy=%s\\n\' "$LOCAL_PROXY"',
        "printf 'no_proxy=%s\\n' 'localhost,127.0.0.1,::1'",
        "printf 'NO_PROXY=%s\\n' 'localhost,127.0.0.1,::1'",
        "keeping existing GitHub transport",
    ):
        assert literal in workflow, literal
    assert "GIT_CONFIG_GLOBAL: /dev/null" in workflow
    assert "GIT_CONFIG_SYSTEM: /dev/null" in workflow
    assert 'GIT_NO_REPLACE_OBJECTS: "1"' in workflow
    assert "working-directory: infra" in workflow
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
    assert "sha256sum" not in workflow
    assert "BUILDX_LINUX_AMD64_SHA256" not in workflow
    assert "ddns.net" not in workflow
    assert "PROXY_USERNAME" not in workflow
    assert "PROXY_PASSWORD" not in workflow
    prune_script = extract_step_script(
        workflow, "Keep mypc Docker build cache under 60 GB"
    )
    assert "if: always()" in workflow
    assert (
        "docker buildx prune --builder default --force --max-used-space 60gb"
        in prune_script
    )
    assert "docker builder prune" not in prune_script
    assert "DOCKER_CACHE_PRUNE_UNTIL" not in workflow
    image_build_script = extract_step_script(workflow, "Build and push production images")
    docker_build_lock = "/var/lib/vecta-docker-lock/build.lock"
    # The source builder holds this lock around its worker pool. An outer
    # workflow flock would deadlock its second open of the same lock file.
    assert "flock 9" not in image_build_script
    assert f"exec 9>{docker_build_lock}" in prune_script
    assert "if ! flock -n 9; then" in prune_script
    assert "production image build holds Docker lock; skipping cache prune" in prune_script
    assert (
        'docker login "$DOCKER_BASE_IMAGE_SOURCE_REGISTRY" -u admin --password-stdin'
        in image_build_script
    )
    retired_environment = "".join(("v", "test"))
    assert f"check:{retired_environment}-images" not in workflow
    assert f"build-push-{retired_environment}-images.mjs" not in workflow
    assert f"{retired_environment.upper()}_IMAGE_NAMES" not in workflow
    assert "GITHUB_SHA: ${{ inputs.source_sha }}" not in workflow
    assert "/tarball/${SOURCE_SHA}" not in workflow
    infra_script = extract_step_script(workflow, "Checkout infra contract")
    for literal in (
        'target="$GITHUB_WORKSPACE/infra"',
        'old_objects="$GITHUB_WORKSPACE/.git/objects"',
        'rm -rf "$target"',
        'git init "$target"',
        'GIT_ALTERNATE_OBJECT_DIRECTORIES="$old_objects"',
        'printf \'%s\\n\' "$GITHUB_SHA" > "$target/.git/shallow"',
        'git -C "$target" repack -a -d',
        "for attempt in 1 2 3; do",
        "timeout 10m git -C \"$target\" fetch --depth=1 origin",
        'git -C "$target" fsck --connectivity-only "$GITHUB_SHA"',
        'git -C "$target" checkout --detach --force "$GITHUB_SHA"',
        'test "$(git -C "$target" rev-parse HEAD)" = "$GITHUB_SHA"',
        'git -C "$target" status --porcelain --untracked-files=all',
        "GIT_ASKPASS_REQUIRE=force",
        "GIT_TERMINAL_PROMPT=0",
    ):
        assert literal in infra_script, literal
    assert not re.search(r"https://[^\s/]*@github\.com", infra_script)
    assert not re.search(r"\bset\s+-x\b|\bxtrace\b", infra_script)
    download_script = extract_step_script(workflow, "Download selected VectA source")
    clone_prefix = (
        "  GIT_HTTP_LOW_SPEED_LIMIT=0 \\\n"
        "    LC_ALL=C \\\n"
        '    GIT_ASKPASS="$askpass" \\\n'
        "    GIT_ASKPASS_REQUIRE=force \\\n"
        "    GIT_TERMINAL_PROMPT=0 \\\n"
        '    timeout 30m git -C vecta fetch --depth=1 "$1" "$SOURCE_SHA" "${baseline_refs[@]}"'
    )
    assert clone_prefix in download_script
    assert "for attempt in 1 2 3; do" in download_script
    assert 'rm -rf vecta' in download_script
    assert 'if [ "$attempt" -eq 3 ]; then' in download_script
    assert 'echo "VectA clone failed after 3 attempts" >&2' in download_script
    assert 'cache="/home/github-runner/.cache/vecta-main.git"' in download_script
    assert "proxy =" not in download_script
    assert "HTTPS_PROXY" not in download_script
    assert "https_proxy" not in download_script
    assert 'git --git-dir="$cache" cat-file -e "$SOURCE_SHA^{commit}"' in download_script
    assert 'git --git-dir="$cache" fsck --connectivity-only "$SOURCE_SHA"' in download_script
    assert 'if clone_source "$cache"; then' in download_script
    assert 'clone_source "$remote"' in download_script
    assert "Cached VectA source unusable; falling back to GitHub" in download_script
    assert download_script.count("GIT_HTTP_LOW_SPEED_LIMIT") == 1
    assert "GIT_HTTP_LOW_SPEED_TIME" not in workflow
    assert not re.search(r"\bgh\b", download_script)
    assert not re.search(r"\bgit\b[^\n]*\bconfig\b", download_script)
    assert "GIT_CONFIG_" not in download_script
    assert not re.search(r"\bset\s+-x\b|\bxtrace\b", download_script)
    assert not re.search(
        r"\becho\b[^\n]*\$(?:VECTA_READ_TOKEN|\{VECTA_READ_TOKEN[^}]*\})",
        download_script,
    )
    assert download_script.count('git -C vecta fetch --depth=1') == 1
    assert download_script.count("https://github.com/ZenoWangzy/vecta.git") == 1
    assert not re.search(r"https://[^\s/]*@github\.com", download_script)

    hermes_seed_script = extract_step_script(workflow, "Seed verified Hermes base image")
    for literal in (
        'Hermes base must use a digest-pinned production proxy reference',
        'read -r expected_group_tag_ref expected_manifest_digest expected_target <<< "$hermes_pin"',
        'if [ -z "${PRODUCTION_IMAGE_NAMES:-}" ]; then',
        'if [ "$requested_image" = \'employee-runtime\' ]; then',
        "Hermes base sync skipped; employee-runtime is not selected",
        "vecta/scripts/production-image-contract.json",
        'NEXUS_SYNC_ONLY="$expected_target"',
        'NEXUS_SYNC_HERMES_REF="${expected_group_tag_ref}@${expected_manifest_digest}"',
        'NEXUS_DOCKER_GROUP_REGISTRY="$DOCKER_BASE_IMAGE_SOURCE_REGISTRY"',
        "scripts/sync-mypc-nexus-images.sh --execute",
        'docker login "$NEXUS_DOCKER_REGISTRY" -u admin --password-stdin',
        'docker login "$DOCKER_BASE_IMAGE_SOURCE_REGISTRY" -u admin --password-stdin',
        "command -v skopeo >/dev/null",
        "scripts/verify-nexus-image-digest.sh",
        '"$expected_group_tag_ref" "$expected_manifest_digest"',
        'docker_config="$(mktemp -d "${RUNNER_TEMP}/vecta-hermes-sync.XXXXXX")"',
        "trap 'rm -rf \"$docker_config\"' EXIT",
    ):
        assert literal in hermes_seed_script, literal
    assert hermes_seed_script.index(
        "scripts/sync-mypc-nexus-images.sh --execute"
    ) < hermes_seed_script.index("scripts/verify-nexus-image-digest.sh")
    assert "set -x" not in hermes_seed_script


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
    version: str = BUILDX_VERSION,
    driver: str = "docker",
) -> tuple[subprocess.CompletedProcess[str], str, list[Path]]:
    with tempfile.TemporaryDirectory() as temporary:
        temporary_path = Path(temporary)
        fake_bin = temporary_path / "bin"
        runner_temp = temporary_path / "runner"
        docker_lock = temporary_path / "vecta-docker-lock" / "build.lock"
        fake_bin.mkdir()
        runner_temp.mkdir()
        docker_lock.parent.mkdir()
        docker_lock.touch()
        build_script = build_script.replace(
            "/var/lib/vecta-docker-lock/build.lock", str(docker_lock)
        )
        log_path = temporary_path / "calls.log"
        plugin_path = temporary_path / "system" / "docker-buildx"
        if plugin_present:
            plugin_path.parent.mkdir()
            write_executable(plugin_path, "#!/bin/sh\nexit 0\n")
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
                "BUILDX_PLUGIN_PATH": str(plugin_path),
                "FAKE_BUILDX_VERSION": version,
                "FAKE_BUILDX_DRIVER": driver,
                "FAKE_LOG": str(log_path),
                "NEXUS_ADMIN_PASSWORD": "contract-secret",
                "NEXUS_DOCKER_REGISTRY": "127.0.0.1:8082",
                "DOCKER_BASE_IMAGE_SOURCE_REGISTRY": "127.0.0.1:8083",
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
    assert "docker buildx version" in success_log
    assert "docker buildx use default" in success_log
    assert "docker buildx inspect default" in success_log
    assert "docker login 127.0.0.1:8082 -u admin --password-stdin" in success_log
    assert "docker login 127.0.0.1:8083 -u admin --password-stdin" in success_log
    assert 'nice ' not in success_log
    assert 'ionice ' not in success_log
    assert "node scripts/build-push-production-images.mjs" in success_log
    assert not success_leftovers

    failures = (
        ("missing", run_fake_build(build_script, plugin_present=False)),
        ("version", run_fake_build(build_script, version="v0.34.0")),
        ("driver", run_fake_build(build_script, driver="docker-container")),
    )
    for label, (result, log, leftovers) in failures:
        assert result.returncode != 0
        assert "node scripts/build-push-production-images.mjs" not in log
        assert not leftovers, f"{label}: temporary DOCKER_CONFIG was not removed"


def assert_local_checkout_isolation(workflow: str) -> None:
    checkout_script = extract_step_script(workflow, "Checkout infra contract")
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        source = root / "source"
        workspace = root / "workspace"
        outside = root / "outside"
        runner_temp = root / "runner"
        hook_marker = root / "hook-ran"
        source.mkdir()
        outside.mkdir()
        runner_temp.mkdir()

        def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["git", "-C", str(repo), *args],
                text=True,
                capture_output=True,
                check=True,
            )

        git(source, "init")
        git(source, "config", "user.name", "Contract Test")
        git(source, "config", "user.email", "contract@example.invalid")
        (source / "contract.txt").write_text("base\n")
        git(source, "add", "contract.txt")
        git(source, "commit", "-m", "base")
        (source / "contract.txt").write_text("good\n")
        git(source, "commit", "-am", "good")
        good_sha = git(source, "rev-parse", "HEAD").stdout.strip()
        subprocess.run(
            ["git", "clone", "--depth=1", f"file://{source}", str(workspace)],
            text=True,
            capture_output=True,
            check=True,
        )
        git(workspace, "config", "user.name", "Contract Test")
        git(workspace, "config", "user.email", "contract@example.invalid")
        (workspace / "contract.txt").write_text("evil\n")
        git(workspace, "commit", "-am", "evil")
        evil_sha = git(workspace, "rev-parse", "HEAD").stdout.strip()
        git(workspace, "replace", good_sha, evil_sha)
        hook = workspace / ".git" / "hooks" / "post-checkout"
        hook.write_text(f"#!/bin/sh\ntouch '{hook_marker}'\n")
        hook.chmod(0o700)
        (outside / "keep").write_text("keep\n")
        git(workspace, "config", "core.worktree", str(outside))

        environment = os.environ.copy()
        environment.update(
            {
                "GITHUB_SHA": good_sha,
                "GITHUB_WORKSPACE": str(workspace),
                "PWD": str(workspace),
                "INFRA_READ_TOKEN": "unused-local-object",
                "RUNNER_TEMP": str(runner_temp),
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_SYSTEM": "/dev/null",
                "GIT_NO_REPLACE_OBJECTS": "1",
            }
        )
        result = subprocess.run(
            ["bash", "-c", checkout_script],
            cwd=workspace,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        checkout = workspace / "infra"
        assert (checkout / "contract.txt").read_text() == "good\n"
        assert not hook_marker.exists()
        assert (outside / "keep").read_text() == "keep\n"
        assert git(checkout, "rev-parse", "HEAD").stdout.strip() == good_sha
        assert git(checkout, "status", "--porcelain").stdout == ""


def assert_incomplete_cache_rejected() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        source = root / "source"
        cache = root / "cache.git"
        source.mkdir()
        subprocess.run(["git", "-C", str(source), "init"], check=True)
        subprocess.run(
            ["git", "-C", str(source), "config", "user.name", "Contract Test"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "config",
                "user.email",
                "contract@example.invalid",
            ],
            check=True,
        )
        (source / "contract.txt").write_text("complete graph required\n")
        subprocess.run(["git", "-C", str(source), "add", "contract.txt"], check=True)
        subprocess.run(
            ["git", "-C", str(source), "commit", "-m", "source"], check=True
        )
        sha = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        subprocess.run(["git", "init", "--bare", str(cache)], check=True)
        source_object = source / ".git" / "objects" / sha[:2] / sha[2:]
        cache_object = cache / "objects" / sha[:2] / sha[2:]
        cache_object.parent.mkdir(parents=True)
        shutil.copyfile(source_object, cache_object)
        subprocess.run(
            ["git", f"--git-dir={cache}", "update-ref", "refs/heads/main", sha],
            check=True,
        )
        result = subprocess.run(
            ["git", f"--git-dir={cache}", "fsck", "--connectivity-only", sha],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode != 0


def assert_image_selection(workflow: str) -> None:
    script = extract_step_script(workflow, 'Validate requested production images')
    for raw, baseline, expected in (
        ('', '', 'app,employee-runtime'),
        (' employee-runtime,app,employee-runtime ', '', 'app,employee-runtime'),
        ('employee-runtime', '', 'employee-runtime'),
        ('', 'candidate-base', ''),
        ('employee-runtime', 'candidate-base', 'employee-runtime'),
        ('container-alias', '', None),
        (',,', '', None),
        (',,', 'candidate-base', None),
    ):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'scripts').mkdir()
            (root / 'scripts/production-image-contract.json').write_text(json.dumps({
                'images': [{'name': 'app'}, {'name': 'employee-runtime'}],
            }))
            environment_file = root / 'environment'
            result = subprocess.run(['bash', '-c', script], cwd=root, env={
                **os.environ, 'PRODUCTION_IMAGE_NAMES': raw, 'AFFECTED_SINCE_SHA': baseline,
                'GITHUB_ENV': str(environment_file),
            }, text=True, capture_output=True, check=False, timeout=15)
            if expected is None:
                assert result.returncode != 0, raw
                assert not environment_file.exists(), raw
            else:
                assert result.returncode == 0, result.stderr
                assert environment_file.read_text() == f'PRODUCTION_IMAGE_NAMES={expected}\n'


def assert_fixed_source_checkout(workflow: str) -> None:
    script = extract_step_script(workflow, 'Download selected VectA source')
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / 'source'
        source.mkdir()
        environment = {**os.environ, 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_SYSTEM': '/dev/null', 'GIT_NO_REPLACE_OBJECTS': '1'}

        def git(*args: str) -> str:
            return subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(source), *args], env=environment,
                                  text=True, capture_output=True, check=True).stdout.strip()

        git('init', '-b', 'main')
        git('config', 'user.name', 'Contract Test')
        git('config', 'user.email', 'contract@example.invalid')
        (source / 'candidate').write_text('selected\n')
        changed_path = 'packages/web/src/app.ts'
        (source / changed_path).parent.mkdir(parents=True)
        (source / changed_path).write_text('baseline\n')
        git('add', '.')
        git('commit', '-m', 'baseline')
        baseline = git('rev-parse', 'HEAD')
        (source / changed_path).write_text('selected\n')
        git('add', '.')
        git('commit', '-m', 'candidate')
        selected = git('rev-parse', 'HEAD')
        (source / 'candidate').write_text('newer main\n')
        git('commit', '-am', 'later main')
        tip = git('rev-parse', 'HEAD')
        fake_bin = root / 'bin'
        fake_bin.mkdir()
        # The enclosing subprocess has a 15-second deadline. Keep this fixture
        # portable to the Mac supervisor without requiring GNU timeout there.
        write_executable(fake_bin / 'timeout', '#!/bin/sh\nshift\nexec "$@"\n')
        write_executable(fake_bin / 'curl', '''#!/bin/sh
set -eu
case "$*" in
  */git/ref/heads/main*) printf '%s\n' "$FAKE_BRANCH_JSON" ;;
  */compare/*) printf '%s\n' "$FAKE_COMPARE_JSON" ;;
  *) exit 89 ;;
esac
''')
        cache = root / 'cache.git'
        subprocess.run(['git', 'clone', '--bare', str(source), str(cache)], env=environment, check=True, capture_output=True)
        candidate_cache = root / 'candidate-cache.git'
        subprocess.run(['git', 'init', '--bare', str(candidate_cache)], env=environment, check=True, capture_output=True)
        subprocess.run([
            'git', '--git-dir', str(candidate_cache), 'fetch', '--depth=1',
            f'file://{source}', f'{selected}:refs/heads/main',
        ], env=environment, check=True, capture_output=True)
        assert subprocess.run([
            'git', '--git-dir', str(candidate_cache), 'cat-file', '-e', f'{baseline}^{{commit}}',
        ], env=environment, capture_output=True).returncode != 0
        for label, requested, status, base, selected_cache, affected, names, error in (
            ('head', tip, 'identical', tip, None, '', '', None),
            ('retained-cold', selected, 'ahead', selected, None, '', '', None),
            ('retained-warm', selected, 'ahead', selected, cache, '', '', None),
            ('divergent', selected, 'diverged', selected, None, '', '', 'main ancestry'),
            ('wrong-merge-base', selected, 'ahead', tip, None, '', '', 'ancestor of the current VectA main'),
            ('unknown-proof', selected, 'unknown', selected, None, '', '', 'main ancestry'),
            ('affected-cold', selected, 'ahead', selected, None, baseline, '', None),
            ('affected-fetch-head-baseline', selected, 'ahead', selected, None, baseline, '', None),
            ('affected-warm', selected, 'ahead', selected, cache, baseline, '', None),
            ('affected-cache-missing-baseline', selected, 'ahead', selected, candidate_cache, baseline, '', None),
            ('affected-whitespace', selected, 'ahead', selected, None, f' {baseline} ', ' \t ', None),
            ('affected-empty-diff', selected, 'ahead', selected, None, selected, '', None),
            ('explicit-subset', selected, 'ahead', selected, None, 'f' * 40, ' vecta-web , vecta-web ', None),
            ('explicit-subset-invalid-baseline', selected, 'ahead', selected, None, 'main', ' vecta-web ', None),
            ('unused-whitespace-baseline', selected, 'ahead', selected, None, ' \t ', '', None),
            ('invalid-baseline', selected, 'ahead', selected, None, 'main', '', 'affected_since_sha must be a full'),
            ('missing-baseline', selected, 'ahead', selected, None, 'f' * 40, '', 'VectA clone failed after 3 attempts'),
        ):
            workspace = root / label
            workspace.mkdir()
            adapted = script.replace('https://github.com/ZenoWangzy/vecta.git', f'file://{source}')
            adapted = adapted.replace('/home/github-runner/.cache/vecta-main.git', str(selected_cache or root / 'absent-cache'))
            if label == 'affected-fetch-head-baseline':
                # A baseline fetch may become the last FETCH_HEAD entry. Inject
                # that real Git state before the workflow chooses its checkout.
                checkout = 'git -C vecta switch --detach "$SOURCE_SHA"'
                adapted = adapted.replace(checkout, f'git -C vecta fetch --depth=1 "file://{source}" "$AFFECTED_SINCE_SHA"\n' + checkout)
            # Capture the SHA exposed to the following build step, independently
            # of the additional baseline entry written by Git to FETCH_HEAD.
            adapted += '\nprintf "%s\\n" "$SOURCE_SHA" > downstream-source-sha\n'
            result = subprocess.run(['bash', '-c', adapted], cwd=workspace, env={
                **environment, 'PATH': f'{fake_bin}:{environment["PATH"]}',
                'VECTA_READ_TOKEN': 'fixture', 'SOURCE_BRANCH': 'main', 'SOURCE_SHA': requested,
                'AFFECTED_SINCE_SHA': affected, 'PRODUCTION_IMAGE_NAMES': names,
                'FAKE_BRANCH_JSON': json.dumps({'object': {'sha': tip}}),
                'FAKE_COMPARE_JSON': json.dumps({'status': status, 'base_commit': {'sha': requested}, 'merge_base_commit': {'sha': base}}),
            }, text=True, capture_output=True, check=False, timeout=15)
            if error is None:
                assert result.returncode == 0, (label, result.stderr)
                expected = 'newer main\n' if requested == tip else 'selected\n'
                assert (workspace / 'vecta/candidate').read_text() == expected, label
                assert (workspace / 'downstream-source-sha').read_text().strip() == requested, label
                assert subprocess.run([
                    'git', '-C', str(workspace / 'vecta'), 'rev-parse', 'HEAD',
                ], env=environment, text=True, capture_output=True, check=True).stdout.strip() == requested, label
                if selected_cache == cache:
                    assert 'Using cached exact VectA source' in result.stdout
                if selected_cache == candidate_cache:
                    assert 'Cached VectA source unusable; falling back to GitHub' in result.stdout
                    assert 'Using cached exact VectA source' not in result.stdout
                if affected.strip() and not names.strip():
                    # This is the real diff command used by the VectA builder;
                    # a shallow candidate-only checkout used to fail here (128).
                    diff = subprocess.run([
                        'git', '-C', str(workspace / 'vecta'), 'diff', '--name-only', f'{affected.strip()}..HEAD',
                    ], env=environment, text=True, capture_output=True, check=True)
                    assert diff.stdout.splitlines() == ([] if affected.strip() == requested else [changed_path]), label
                    fetched = (workspace / 'vecta/.git/FETCH_HEAD').read_text().splitlines()
                    expected_fetch = {affected.strip()} if label == 'affected-fetch-head-baseline' else {requested, affected.strip()}
                    assert {line.split()[0] for line in fetched} == expected_fetch, label
                else:
                    fetched = (workspace / 'vecta/.git/FETCH_HEAD').read_text().splitlines()
                    assert {line.split()[0] for line in fetched} == {requested}, label
            else:
                assert result.returncode != 0, label
                assert error in result.stderr, (label, result.stderr)
                assert not (workspace / 'vecta').exists(), label
                assert not (workspace / 'downstream-source-sha').exists(), label


def assert_candidate_hermes_pin(workflow: str) -> None:
    script = extract_step_script(workflow, 'Seed verified Hermes base image')
    valid_ref = f'127.0.0.1:8083/nousresearch/hermes-agent:fixture-v1@sha256:{"a" * 64}'
    for group_ref, selected, allowed in (
        (valid_ref, 'employee-runtime', True),
        (valid_ref.replace('fixture-v1', 'fixture-v2').replace('a' * 64, 'b' * 64), 'employee-runtime', True),
        (valid_ref.split('@')[0], 'employee-runtime', False),
        (valid_ref.replace('nousresearch/hermes-agent', 'unknown/repository'), 'employee-runtime', False),
        (valid_ref.replace('127.0.0.1:8083', 'unapproved.invalid'), 'employee-runtime', False),
        (valid_ref.split('@')[0], 'app', True),
    ):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'vecta/scripts').mkdir(parents=True)
            (root / 'scripts').mkdir()
            (root / 'runner').mkdir()
            (root / 'bin').mkdir()
            (root / 'vecta/scripts/production-image-contract.json').write_text(json.dumps({
                'images': [{'name': 'employee-runtime', 'baseImages': [{'arg': 'HERMES_BASE', 'groupRef': group_ref}]}],
            }))
            log = root / 'calls'
            write_executable(root / 'bin/docker', '''#!/usr/bin/env python3
import json, os, sys
if sys.argv[1] != 'login': raise SystemExit(89)
sys.stdin.read()
with open(os.environ['FAKE_LOG'], 'a') as output:
    output.write(json.dumps(['docker', *sys.argv[1:]]) + '\\n')
''')
            write_executable(root / 'bin/skopeo', '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['FAKE_LOG'], 'a') as output:
    output.write(json.dumps(['skopeo', *args]) + '\\n')
state_file = Path(os.environ['FAKE_REGISTRY'])
state = json.loads(state_file.read_text()) if state_file.exists() else {}
if args[0] == 'copy':
    source, target = args[-2:]
    if '--all' not in args or '--preserve-digests' not in args: raise SystemExit(88)
    if '@sha256:' not in source: raise SystemExit(87)
    state[target] = source.split('@', 1)[1]
    state_file.write_text(json.dumps(state))
elif args[0] == 'inspect':
    # The Nexus group resolves the same hosted image after the real helper copy.
    target = args[-1].replace('127.0.0.1:8083/', '127.0.0.1:8082/')
    if target not in state: raise SystemExit(1)
    print(state[target])
else:
    raise SystemExit(89)
''')
            # Execute both real helpers. The only fakes are external registry
            # tools; a skipped sync cannot manufacture a successful digest.
            for helper in ('sync-mypc-nexus-images.sh', 'verify-nexus-image-digest.sh'):
                target = root / 'scripts' / helper
                shutil.copyfile(ROOT / 'scripts' / helper, target)
                target.chmod(0o755)
            result = subprocess.run(['bash', '-c', script], cwd=root, env={
                **os.environ, 'PATH': f'{root / "bin"}:{os.environ["PATH"]}',
                'PRODUCTION_IMAGE_NAMES': selected, 'RUNNER_TEMP': str(root / 'runner'),
                'NEXUS_ADMIN_PASSWORD': 'fixture', 'FAKE_LOG': str(log),
                'FAKE_REGISTRY': str(root / 'registry'),
                'NEXUS_DOCKER_REGISTRY': '127.0.0.1:8082', 'DOCKER_BASE_IMAGE_SOURCE_REGISTRY': '127.0.0.1:8083',
            }, text=True, capture_output=True, check=False, timeout=15)
            assert (result.returncode == 0) == allowed, result.stderr
            if allowed and selected == 'employee-runtime':
                calls = [json.loads(line) for line in log.read_text().splitlines()]
                tag_ref, digest = group_ref.split('@', 1)
                target = tag_ref.split('/', 1)[1]
                expected_copy = [
                    'skopeo', 'copy', '--all', '--preserve-digests',
                    '--src-tls-verify=false', '--dest-tls-verify=false',
                    f'docker://127.0.0.1:8083/nousresearch/hermes-agent@{digest}',
                    f'docker://127.0.0.1:8082/{target}',
                ]
                assert [call for call in calls if call[:2] == ['skopeo', 'copy']] == [expected_copy]
                assert len([call for call in calls if call[:2] == ['docker', 'login']]) == 2
                assert len([call for call in calls if call[:2] == ['skopeo', 'inspect']]) == 3
                assert calls[-1][-1] == f'docker://{tag_ref}'
                assert json.loads((root / 'registry').read_text()) == {f'docker://127.0.0.1:8082/{target}': digest}
            else:
                assert not log.exists()
            assert not list((root / 'runner').iterdir())


def assert_candidate_node_runtime_pin(workflow: str) -> None:
    script = extract_step_script(workflow, 'Seed verified Node runtime base image')
    target = 'node:22.16.0-bookworm-slim'
    digest = 'sha256:048ed02c5fd52e86fda6fbd2f6a76cf0d4492fd6c6fee9e2c463ed5108da0e34'
    group_ref = f'127.0.0.1:8083/{target}@{digest}'
    for selected, selected_ref, allowed in (
        ('employee-runtime', group_ref, True),
        ('employee-runtime', group_ref.replace(target, 'node:22.16.0-alpine'), False),
        ('employee-runtime', group_ref.replace('@sha256:', '@sha512:'), False),
        ('employee-runtime', group_ref.replace('127.0.0.1:8083', 'unapproved.invalid'), False),
        ('app', 'not-a-pinned-node-reference', True),
    ):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'vecta/scripts').mkdir(parents=True)
            (root / 'scripts').mkdir()
            (root / 'runner').mkdir()
            (root / 'bin').mkdir()
            (root / 'vecta/scripts/production-image-contract.json').write_text(json.dumps({
                'images': [{'name': 'employee-runtime', 'baseImages': [{'arg': 'NODE_RUNTIME_BASE', 'groupRef': selected_ref}]}],
            }))
            log = root / 'calls'
            write_executable(root / 'bin/docker', '''#!/usr/bin/env python3
import json, os, sys
if sys.argv[1] != 'login': raise SystemExit(89)
sys.stdin.read()
with open(os.environ['FAKE_LOG'], 'a') as output:
    output.write(json.dumps(['docker', *sys.argv[1:]]) + '\\n')
''')
            write_executable(root / 'bin/skopeo', '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['FAKE_LOG'], 'a') as output:
    output.write(json.dumps(['skopeo', *args]) + '\\n')
state_file = Path(os.environ['FAKE_REGISTRY'])
state = json.loads(state_file.read_text()) if state_file.exists() else {}
if args[0] == 'copy':
    source, target = args[-2:]
    if '--all' not in args or '--preserve-digests' not in args: raise SystemExit(88)
    if '@sha256:' not in source: raise SystemExit(87)
    state[target] = source.split('@', 1)[1]
    state_file.write_text(json.dumps(state))
elif args[0] == 'inspect':
    target = args[-1].replace('127.0.0.1:8083/', '127.0.0.1:8082/')
    if target not in state: raise SystemExit(1)
    print(state[target])
else:
    raise SystemExit(89)
''')
            for helper in ('sync-mypc-nexus-images.sh', 'verify-nexus-image-digest.sh'):
                helper_path = root / 'scripts' / helper
                shutil.copyfile(ROOT / 'scripts' / helper, helper_path)
                helper_path.chmod(0o755)
            result = subprocess.run(['bash', '-c', script], cwd=root, env={
                **os.environ, 'PATH': f'{root / "bin"}:{os.environ["PATH"]}',
                'PRODUCTION_IMAGE_NAMES': selected, 'RUNNER_TEMP': str(root / 'runner'),
                'NEXUS_ADMIN_PASSWORD': 'fixture', 'FAKE_LOG': str(log),
                'FAKE_REGISTRY': str(root / 'registry'),
                'NEXUS_DOCKER_REGISTRY': '127.0.0.1:8082', 'DOCKER_BASE_IMAGE_SOURCE_REGISTRY': '127.0.0.1:8083',
            }, text=True, capture_output=True, check=False, timeout=15)
            assert (result.returncode == 0) == allowed, result.stderr
            if allowed and selected == 'employee-runtime':
                calls = [json.loads(line) for line in log.read_text().splitlines()]
                expected_copy = [
                    'skopeo', 'copy', '--all', '--preserve-digests',
                    '--src-tls-verify=false', '--dest-tls-verify=false',
                    f'docker://127.0.0.1:8083/node@{digest}',
                    f'docker://127.0.0.1:8082/{target}',
                ]
                copy_calls = [call for call in calls if call[:2] == ['skopeo', 'copy']]
                assert copy_calls == [expected_copy]
                login_calls = [call for call in calls if call[:2] == ['docker', 'login']]
                assert login_calls == [
                    ['docker', 'login', '127.0.0.1:8082', '-u', 'admin', '--password-stdin'],
                    ['docker', 'login', '127.0.0.1:8083', '-u', 'admin', '--password-stdin'],
                ]
                assert len([call for call in calls if call[:2] == ['skopeo', 'inspect']]) == 3
                assert calls[-1][-1] == f'docker://127.0.0.1:8083/{target}'
                assert json.loads((root / 'registry').read_text()) == {
                    f'docker://127.0.0.1:8082/{target}': digest,
                }
            else:
                assert not log.exists()
            assert not list((root / 'runner').iterdir())


def main() -> None:
    workflow = WORKFLOW_PATH.read_text()
    assert_static_contract(workflow)
    assert_provisioning_contract()
    assert_fake_contract(workflow)
    assert_image_selection(workflow)
    assert_fixed_source_checkout(workflow)
    assert_candidate_hermes_pin(workflow)
    assert_candidate_node_runtime_pin(workflow)
    assert_local_checkout_isolation(workflow)
    assert_incomplete_cache_rejected()
    print("build-mypc images contract: ok")


if __name__ == "__main__":
    main()
