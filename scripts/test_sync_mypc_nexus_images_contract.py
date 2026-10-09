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
        environment = os.environ | {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "NEXUS_SYNC_ONLY": HERMES_TARGET,
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
    target_digest: str = HERMES_DIGEST,
    inspect_exit: int = 0,
    copy_exit: int = 0,
) -> tuple[subprocess.CompletedProcess[str], list[str], list[str], list[str]]:
    with tempfile.TemporaryDirectory() as temporary:
        fake_bin = Path(temporary) / "bin"
        fake_bin.mkdir()
        call_log = Path(temporary) / "skopeo-calls.txt"
        inspect_args = Path(temporary) / "skopeo-inspect-args.txt"
        copy_args = Path(temporary) / "skopeo-copy-args.txt"
        skopeo = fake_bin / "skopeo"
        skopeo.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "printf '%s\\n' \"$1\" >> \"$FAKE_SKOPEO_CALLS\"\n"
            "case \"$1\" in\n"
            "  inspect)\n"
            "    printf '%s\\n' \"$@\" > \"$FAKE_SKOPEO_INSPECT_ARGS\"\n"
            "    [ \"$FAKE_INSPECT_EXIT\" -eq 0 ] || exit \"$FAKE_INSPECT_EXIT\"\n"
            "    printf '%s\\n' \"$FAKE_TARGET_DIGEST\"\n"
            "    ;;\n"
            "  copy)\n"
            "    printf '%s\\n' \"$@\" > \"$FAKE_SKOPEO_COPY_ARGS\"\n"
            "    exit \"$FAKE_COPY_EXIT\"\n"
            "    ;;\n"
            "  *) exit 64 ;;\n"
            "esac\n"
        )
        skopeo.chmod(0o755)
        environment = os.environ | {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "NEXUS_SYNC_ONLY": HERMES_TARGET,
            "FAKE_SKOPEO_CALLS": str(call_log),
            "FAKE_SKOPEO_INSPECT_ARGS": str(inspect_args),
            "FAKE_SKOPEO_COPY_ARGS": str(copy_args),
            "FAKE_TARGET_DIGEST": target_digest,
            "FAKE_INSPECT_EXIT": str(inspect_exit),
            "FAKE_COPY_EXIT": str(copy_exit),
        }
        result = subprocess.run(
            ["bash", str(SCRIPT), "--execute"],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        calls = call_log.read_text().splitlines() if call_log.exists() else []
        inspected = inspect_args.read_text().splitlines() if inspect_args.exists() else []
        copied = copy_args.read_text().splitlines() if copy_args.exists() else []
        return result, calls, inspected, copied


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

    matched, matched_calls, matched_inspect_args, matched_copy_args = run_selected_execute()
    assert matched.returncode == 0, matched.stderr
    assert matched_calls == ["inspect"]
    assert "skip 127.0.0.1:8082/" + HERMES_TARGET in matched.stdout
    assert "already has " + HERMES_DIGEST in matched.stdout
    assert matched_inspect_args == [
        "inspect",
        "--no-tags",
        "--tls-verify=false",
        "--format",
        "{{.Digest}}",
        f"docker://127.0.0.1:8082/{HERMES_TARGET}",
    ]
    assert matched_copy_args == []

    copied_cases = (
        ("missing", run_selected_execute(inspect_exit=1)),
        ("mismatch", run_selected_execute(target_digest="sha256:" + "f" * 64)),
    )
    expected_copy_args = [
        "copy",
        "--all",
        "--preserve-digests",
        "--src-tls-verify=false",
        "--dest-tls-verify=false",
        f"docker://{HERMES_SOURCE_DIGEST_REF}",
        f"docker://127.0.0.1:8082/{HERMES_TARGET}",
    ]
    for label, (result, calls, inspect_args, copy_args) in copied_cases:
        assert result.returncode == 0, f"{label}: {result.stderr}"
        assert calls == ["inspect", "copy"], label
        assert inspect_args[:1] == ["inspect"], label
        assert copy_args == expected_copy_args, label

    failed_copy, failed_calls, _, failed_copy_args = run_selected_execute(
        target_digest="sha256:" + "f" * 64,
        copy_exit=1,
    )
    assert failed_copy.returncode != 0
    assert failed_calls == ["inspect", "copy"]
    assert failed_copy_args == expected_copy_args


if __name__ == "__main__":
    main()
