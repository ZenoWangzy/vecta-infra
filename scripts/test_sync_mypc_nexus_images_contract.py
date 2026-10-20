#!/usr/bin/env python3
"""Executable safety contract for the Nexus third-party image sync script."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync-mypc-nexus-images.sh"
HERMES_TARGET = "nousresearch/hermes-agent:v0.21.6-9774f4f3"
HERMES_SOURCE_TAG = "127.0.0.1:8083/nousresearch/hermes-agent:v0.21.6"
HERMES_DIGEST = "sha256:9774f4f39a9bb8c2f68ce728ed5e99ddbad282163be56764afacf88ed952b784"
HERMES_SOURCE_AUDIT_REF = f"{HERMES_SOURCE_TAG}@{HERMES_DIGEST}"
HERMES_SOURCE_DIGEST_REF = (
    f"127.0.0.1:8083/nousresearch/hermes-agent@{HERMES_DIGEST}"
)


def run_selected_dry_run() -> str:
    with tempfile.TemporaryDirectory() as temporary:
        fake_bin = Path(temporary) / "bin"
        fake_bin.mkdir()
        docker = fake_bin / "docker"
        docker.write_text("#!/bin/sh\nexit 1\n")
        docker.chmod(0o755)
        environment = {key: value for key, value in os.environ.items() if key not in {
            "NEXUS_SYNC_HERMES_REF", "NEXUS_SYNC_ONLY", "NEXUS_DOCKER_REGISTRY", "NEXUS_DOCKER_GROUP_REGISTRY",
        }} | {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "NEXUS_SYNC_ONLY": HERMES_TARGET,
            "NEXUS_DOCKER_REGISTRY": "127.0.0.1:8082",
            "NEXUS_DOCKER_GROUP_REGISTRY": "127.0.0.1:8083",
        }
        result = subprocess.run(
            ["bash", str(SCRIPT), "--dry-run"],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
    assert result.returncode == 0, result.stderr
    return result.stdout


def run_selected_execute(
    *,
    first_digest: str = HERMES_DIGEST,
    first_exit: int = 0,
    second_digest: str = HERMES_DIGEST,
    second_exit: int = 0,
    copy_exit: int = 0,
    candidate_ref: str | None = None,
    selection: str = HERMES_TARGET,
    registry: str = "127.0.0.1:8082",
    group_registry: str = "127.0.0.1:8083",
) -> tuple[subprocess.CompletedProcess[str], list[str], list[str]]:
    with tempfile.TemporaryDirectory() as temporary:
        fake_bin = Path(temporary) / "bin"
        fake_bin.mkdir()
        call_log = Path(temporary) / "skopeo-calls.txt"
        copy_args = Path(temporary) / "skopeo-copy-args.txt"
        inspect_count = Path(temporary) / "skopeo-inspect-count.txt"
        docker = fake_bin / "docker"
        docker.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "printf 'docker %s\\n' \"$*\" >> \"$FAKE_SKOPEO_CALLS\"\n"
            "case \"$1\" in\n"
            "  image) [ \"$2\" = inspect ]; exit 1 ;;\n"
            "  pull|tag|push) exit 0 ;;\n"
            "  *) exit 89 ;;\n"
            "esac\n"
        )
        docker.chmod(0o755)
        skopeo = fake_bin / "skopeo"
        skopeo.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "case \"${1:-}\" in\n"
            "  inspect)\n"
            "    count=\"$(cat \"$FAKE_INSPECT_COUNT\" 2>/dev/null || printf '0')\"\n"
            "    count=$((count + 1))\n"
            "    printf '%s\\n' \"$count\" > \"$FAKE_INSPECT_COUNT\"\n"
            "    printf 'inspect %s\\n' \"$*\" >> \"$FAKE_SKOPEO_CALLS\"\n"
            "    if [ \"$count\" -eq 1 ]; then\n"
            "      exit_code=\"$FAKE_FIRST_EXIT\"\n"
            "      digest=\"$FAKE_FIRST_DIGEST\"\n"
            "    else\n"
            "      exit_code=\"$FAKE_SECOND_EXIT\"\n"
            "      digest=\"$FAKE_SECOND_DIGEST\"\n"
            "    fi\n"
            "    if [ \"$exit_code\" -ne 0 ]; then exit \"$exit_code\"; fi\n"
            "    printf '%s\\n' \"$digest\"\n"
            "    ;;\n"
            "  copy)\n"
            "    printf 'copy\\n' >> \"$FAKE_SKOPEO_CALLS\"\n"
            "    printf '%s\\n' \"$@\" > \"$FAKE_SKOPEO_COPY_ARGS\"\n"
            "    exit \"$FAKE_COPY_EXIT\"\n"
            "    ;;\n"
            "  *) exit 2 ;;\n"
            "esac\n"
        )
        skopeo.chmod(0o755)
        environment = {key: value for key, value in os.environ.items() if key not in {
            "NEXUS_SYNC_HERMES_REF", "NEXUS_SYNC_ONLY", "NEXUS_DOCKER_REGISTRY", "NEXUS_DOCKER_GROUP_REGISTRY",
        }} | {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "NEXUS_SYNC_ONLY": selection,
            "NEXUS_DOCKER_REGISTRY": registry,
            "NEXUS_DOCKER_GROUP_REGISTRY": group_registry,
            "FAKE_SKOPEO_CALLS": str(call_log),
            "FAKE_SKOPEO_COPY_ARGS": str(copy_args),
            "FAKE_INSPECT_COUNT": str(inspect_count),
            "FAKE_FIRST_DIGEST": first_digest,
            "FAKE_FIRST_EXIT": str(first_exit),
            "FAKE_SECOND_DIGEST": second_digest,
            "FAKE_SECOND_EXIT": str(second_exit),
            "FAKE_COPY_EXIT": str(copy_exit),
        }
        if candidate_ref is not None:
            environment["NEXUS_SYNC_HERMES_REF"] = candidate_ref
        result = subprocess.run(
            ["bash", str(SCRIPT), "--execute"],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
        calls = call_log.read_text().splitlines() if call_log.exists() else []
        args = copy_args.read_text().splitlines() if copy_args.exists() else []
        return result, calls, args


def main() -> None:
    syntax = subprocess.run(
        ["bash", "-n", str(SCRIPT)], text=True, capture_output=True, check=False
    )
    assert syntax.returncode == 0, syntax.stderr

    output = run_selected_dry_run()
    assert f"target 127.0.0.1:8082/{HERMES_TARGET}" in output
    assert (
        f"manifest index {HERMES_SOURCE_AUDIT_REF} -> "
        f"127.0.0.1:8082/{HERMES_TARGET}"
    ) in output
    assert (
        "+ skopeo copy --all --preserve-digests --src-tls-verify=false "
        f"--dest-tls-verify=false docker://{HERMES_SOURCE_DIGEST_REF} "
        f"docker://127.0.0.1:8082/{HERMES_TARGET} "
        in output
    )
    assert f"docker://{HERMES_SOURCE_AUDIT_REF}" not in output
    assert f"+ docker pull {HERMES_SOURCE_AUDIT_REF} " not in output
    assert (
        f"+ docker tag {HERMES_SOURCE_AUDIT_REF} "
        f"127.0.0.1:8082/{HERMES_TARGET} "
    ) not in output
    assert f"+ docker push 127.0.0.1:8082/{HERMES_TARGET} " not in output
    assert "+ docker pull redis:7-alpine " not in output
    assert "+ docker pull vecta-hermes-withopenclaw:v2026.5.16 " not in output

    expected_copy_args = [
        "copy",
        "--all",
        "--preserve-digests",
        "--src-tls-verify=false",
        "--dest-tls-verify=false",
        f"docker://{HERMES_SOURCE_DIGEST_REF}",
        f"docker://127.0.0.1:8082/{HERMES_TARGET}",
    ]
    expected_inspect = (
        "inspect inspect --no-tags --tls-verify=false "
        "--format {{.Digest}} docker://127.0.0.1:8082/"
        f"{HERMES_TARGET}"
    )

    matched, matched_calls, matched_copy_args = run_selected_execute()
    assert matched.returncode == 0, matched.stderr
    assert matched_calls == [expected_inspect]
    assert matched_copy_args == []
    assert "already matches" in matched.stdout

    missing, missing_calls, missing_copy_args = run_selected_execute(first_exit=1)
    assert missing.returncode == 0, missing.stderr
    assert missing_calls == [expected_inspect, "copy", expected_inspect]
    assert missing_copy_args == expected_copy_args
    assert "target digest query failed" in missing.stdout

    different_digest = "sha256:" + ("a" * 64)
    different, different_calls, different_copy_args = run_selected_execute(
        first_digest=different_digest
    )
    assert different.returncode == 0, different.stderr
    assert different_calls == [expected_inspect, "copy", expected_inspect]
    assert different_copy_args == expected_copy_args
    assert "target digest mismatch" in different.stdout

    invalid_after_copy, invalid_calls, invalid_copy_args = run_selected_execute(
        first_exit=1, second_digest=different_digest
    )
    assert invalid_after_copy.returncode != 0
    assert "target digest mismatch after copy" in invalid_after_copy.stderr
    assert invalid_calls == [expected_inspect, "copy", expected_inspect]
    assert invalid_copy_args == expected_copy_args

    unreadable_after_copy, unreadable_calls, unreadable_copy_args = run_selected_execute(
        first_exit=1, second_exit=1
    )
    assert unreadable_after_copy.returncode != 0
    assert "could not inspect target digest after copy" in unreadable_after_copy.stderr
    assert unreadable_calls == [expected_inspect, "copy", expected_inspect]
    assert unreadable_copy_args == expected_copy_args

    failed_copy, failed_copy_calls, failed_copy_args = run_selected_execute(
        first_exit=1, copy_exit=47
    )
    assert failed_copy.returncode == 47
    assert failed_copy_calls == [expected_inspect, "copy"]
    assert failed_copy_args == expected_copy_args

    for tag, digest in (("fixture-v1", "a" * 64), ("fixture-v2", "b" * 64)):
        target = f"nousresearch/hermes-agent:{tag}"
        digest = f"sha256:{digest}"
        candidate, calls, copied = run_selected_execute(
            candidate_ref=f"127.0.0.1:8083/{target}@{digest}",
            selection=target,
            first_exit=1,
            second_digest=digest,
        )
        assert candidate.returncode == 0, candidate.stderr
        assert copied == [
            "copy", "--all", "--preserve-digests", "--src-tls-verify=false", "--dest-tls-verify=false",
            f"docker://127.0.0.1:8083/nousresearch/hermes-agent@{digest}",
            f"docker://127.0.0.1:8082/{target}",
        ]
        assert len(calls) == 3 and calls[1] == "copy", calls
        assert all(target in call for call in (calls[0], calls[2]))
        assert "verified" in candidate.stdout

    candidate_target = "nousresearch/hermes-agent:fixture-v1"
    candidate_ref = f"127.0.0.1:8083/{candidate_target}@sha256:{'a' * 64}"
    for overrides in (
        {"candidate_ref": ""},
        {"candidate_ref": f"127.0.0.1:8083/{candidate_target}"},
        {"candidate_ref": candidate_ref.replace("nousresearch/hermes-agent", "unknown/repository")},
        {"candidate_ref": candidate_ref.replace("127.0.0.1:8083", "unapproved.invalid")},
        {"candidate_ref": candidate_ref.replace("sha256:", "sha512:")},
        {"registry": "unapproved.invalid"},
        {"group_registry": "unapproved.invalid"},
        {"selection": "unknown-image:fixture"},
    ):
        arguments = {"candidate_ref": candidate_ref, "selection": candidate_target} | overrides
        rejected, calls, copied = run_selected_execute(**arguments)
        assert rejected.returncode == 2, (arguments, rejected.stderr)
        assert calls == [] and copied == [], arguments

    unknown, calls, copied = run_selected_execute(selection="nousresearch/hermes-agent:unregistered")
    assert unknown.returncode == 2
    assert "unknown Nexus sync target" in unknown.stderr
    assert calls == [] and copied == []

    # Existing non-Hermes entries retain their pull/tag/push behavior.
    redis, calls, copied = run_selected_execute(selection="library/redis:7-alpine")
    assert redis.returncode == 0, redis.stderr
    assert calls[-1] == "docker push 127.0.0.1:8082/library/redis:7-alpine"
    assert any(call.startswith("docker pull ") for call in calls)
    assert any(call.startswith("docker tag ") for call in calls)
    assert copied == []
    print("sync-mypc Nexus images contract: ok (default digest safety, two candidate copies, invalid inputs, existing image sync)")


if __name__ == "__main__":
    main()
