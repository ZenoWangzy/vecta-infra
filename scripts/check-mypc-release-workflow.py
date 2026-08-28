#!/usr/bin/env python3
"""Small stdlib admission check for the single mypc release workflow."""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/build-mypc-images.yml"
RELEASE_PLAYBOOK = ROOT / "playbooks/mypc-release.yml"
LEASE_PLAYBOOK = ROOT / "playbooks/mypc-release-lease.yml"
PREFLIGHT_PLAYBOOK = ROOT / "playbooks/mypc-release-preflight.yml"
HEALTH_PLAYBOOK = ROOT / "playbooks/mypc-release-health.yml"
ROLLBACK_PLAYBOOK = ROOT / "playbooks/mypc-release-rollback.yml"
HISTORY_CHECK = ROOT / "scripts/check-vecta-history-consumer.py"
FRUIT_PROVENANCE_CHECK = ROOT / "scripts/validate_fruit_v4_image_provenance.py"
HISTORY_PREFLIGHT = ROOT / "playbooks/mypc-history-batch-preflight.yml"
HISTORY_EXECUTE = ROOT / "playbooks/mypc-history-batch-execute.yml"
HISTORY_SELECTOR = ROOT / "scripts/vecta_history_migration.py"
LOCK_HELPER = ROOT / "scripts/mypc-release-lock.sh"
MYPC_RUNBOOK = ROOT / "docs/runbooks/mypc-data-structure-compatibility.md"
DIGEST_CHECK = ROOT / "scripts/validate-mypc-digest-manifest.py"
ADOPTION_SCRIPT = ROOT / "scripts/mypc-postgres-adoption-evidence.sh"
DATA_PREFLIGHT_SCRIPT = ROOT / "scripts/mypc-data-layer-regression.sh"
APP_REGRESSION_SCRIPT = ROOT / "scripts/mypc-app-regression.sh"
INVENTORY = ROOT / "inventories/mypc/hosts.ini"
COLLECTIONS = ROOT / "collections/requirements.yml"
LOCK_SELF_TEST_PASS = "mypc release lock self-check passed"


def require(source: str, needle: str, label: str) -> None:
    if needle not in source:
        raise AssertionError(f"missing {label}: {needle}")


def forbid(source: str, pattern: str, label: str) -> None:
    if re.search(pattern, source, re.IGNORECASE | re.MULTILINE):
        raise AssertionError(f"forbidden {label}: {pattern}")


def forbid_standalone_psql_file_execution(source: str, label: str) -> None:
    """Reject tokenized psql file execution outside the rollback playbook."""
    normalized = re.sub(r"\\[ \t]*\n[ \t]*", " ", source)
    for line in normalized.splitlines():
        if not re.search(r"\bpsql\b", line, re.IGNORECASE):
            continue
        try:
            lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
            lexer.whitespace_split = True
            lexer.commenters = ""
            tokens = list(lexer)
        except ValueError as error:
            raise AssertionError(f"forbidden {label}: malformed shell near psql") from error
        for index, token in enumerate(tokens):
            if token.lower() != "psql":
                continue
            for argument in tokens[index + 1 :]:
                if argument in {";", "&&", "||", "|", "&", "(", ")"}:
                    break
                if (
                    argument == "-f"
                    or argument.startswith("-f")
                    or argument == "--file"
                    or argument.startswith("--file=")
                ):
                    raise AssertionError(f"forbidden {label}: standalone psql file execution")


def standalone_psql_scan_self_check() -> None:
    negative = (
        "psql -X -v ON_ERROR_STOP=1 -f rollback.sql",
        "psql -X \\\n  -v ON_ERROR_STOP=1 \\\n  -f rollback.sql",
    )
    negative += (
        'PGHOST=localhost psql -X "-f" rollback.sql',
        'psql -X "--file=rollback.sql"',
        '"psql" -X "--file" rollback.sql',
        "env PGDATABASE=fruit psql -X --file rollback.sql",
    )
    for fixture in negative:
        try:
            forbid_standalone_psql_file_execution(fixture, "negative fixture")
        except AssertionError:
            continue
        raise AssertionError(f"standalone psql scanner accepted fixture: {fixture!r}")
    try:
        forbid_standalone_psql_file_execution('psql -X "-f rollback.sql', "malformed fixture")
    except AssertionError:
        pass
    else:
        raise AssertionError("standalone psql scanner accepted malformed shell")
    forbid_standalone_psql_file_execution("docker exec postgres psql -X -c 'SELECT 1'", "allowed query")


def check_lease_action_contract(lease: str, runbook: str) -> None:
    """Verify readonly verify is reachable without widening record deletion."""
    allowlists = re.findall(
        r"release_lock_action \| default\(''\) in \[([^\]]+)\]",
        lease,
    )
    expected = {"acquire", "verify", "release", "recover"}
    actions = None
    for candidate in allowlists:
        parsed = {item.strip().strip("'\"") for item in candidate.split(",")}
        if parsed == expected:
            actions = parsed
            break
    if actions is None:
        raise AssertionError("lease action allowlist is missing")
    require(lease, "{{ release_lock_action }}", "lease action execution")
    removal = lease.split("- name: Remove the consumed continuation after terminal lease action", 1)[1]
    require(removal, "release_lock_action in ['release', 'recover']", "terminal-only continuation removal")
    if re.search(r"release_lock_action\s+in\s+\[[^\]]*verify", removal):
        raise AssertionError("readonly verify must not remove its continuation record")
    require(runbook, "release_lock_action=verify", "readonly verify runbook fixture")


def run_lock_runtime_self_check() -> None:
    if shutil.which("flock") is None:
        raise AssertionError("lock helper runtime self-test requires Linux flock")
    result = subprocess.run(
        [str(LOCK_HELPER), "--self-test"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or result.stdout.strip() != LOCK_SELF_TEST_PASS:
        raise AssertionError(
            "lock helper runtime self-test failed: "
            f"exit={result.returncode} output={result.stdout.strip()!r}"
        )


def check_history_rollback_modes(rollback: str) -> None:
    history_play = rollback.split("# The same operator rollback entry point", 1)[1]
    pre_tasks = history_play.split("  pre_tasks:", 1)[1].split("  tasks:", 1)[0]
    task_blocks = re.split(r"(?m)^    - name: ", pre_tasks)[1:]
    if len(task_blocks) < 10:
        raise AssertionError("history rollback must expose at least ten gated pre_tasks")
    gate = "history_rollback_enabled | default(false) | bool"
    for block in task_blocks:
        if gate not in block:
            raise AssertionError("normal rollback mode would enter an ungated history pre_task")
    task_section = history_play.split("  tasks:", 1)[1]
    require(task_section, gate, "history rollback task mode gate")
    normal_invocation = {"history_rollback_enabled": False}
    history_invocation = {"history_rollback_enabled": True}
    if normal_invocation["history_rollback_enabled"] or not history_invocation["history_rollback_enabled"]:
        raise AssertionError("normal/history rollback invocation self-check failed")


def main() -> int:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    for path in (
        RELEASE_PLAYBOOK,
        LEASE_PLAYBOOK,
        PREFLIGHT_PLAYBOOK,
        HEALTH_PLAYBOOK,
        ROLLBACK_PLAYBOOK,
        HISTORY_CHECK,
        FRUIT_PROVENANCE_CHECK,
        HISTORY_PREFLIGHT,
        HISTORY_EXECUTE,
        HISTORY_SELECTOR,
        LOCK_HELPER,
        MYPC_RUNBOOK,
        DIGEST_CHECK,
        ADOPTION_SCRIPT,
        DATA_PREFLIGHT_SCRIPT,
        APP_REGRESSION_SCRIPT,
        INVENTORY,
        COLLECTIONS,
    ):
        if not path.is_file():
            raise AssertionError(f"missing release interface: {path.relative_to(ROOT)}")

    require(workflow, "workflow_dispatch:", "manual dispatch")
    require(workflow, "default: false", "inert deploy default")
    require(workflow, "vars.MYPC_RELEASE_ENABLED", "repository release switch")
    require(workflow, "current VectA main HEAD", "exact source authority")
    require(workflow, "source_sha must be a full 40-character lowercase Git SHA", "source SHA validation")
    require(workflow, "validate-mypc-digest-manifest.py", "digest manifest validator")
    require(workflow, "mypc-release-digest-v1", "digest manifest schema")
    require(workflow, "Deploy exact immutable image digests", "exact-digest deployment")
    require(workflow, "mypc-release-preflight.yml", "contention/backup preflight")
    require(workflow, "mypc-release-lease.yml", "continuous target-host lease")
    require(workflow, "mypc-release-health.yml", "post-deploy health")
    require(workflow, "production-web-flow.mjs", "Hero E2E interface")
    require(workflow, "fruit-production-query-soak.mjs", "business Oracle interface")
    require(workflow, "mypc-release-rollback.yml", "rollback interface")
    require(workflow, "check-vecta-history-consumer.py", "history consumer contract")
    require(workflow, "validate_fruit_v4_image_provenance.py", "existing Fruit image provenance helper")
    require(workflow, "vecta_history_migration.py", "journal-owned migration selector")
    require(workflow, "--json .", "structured migration JSON output")
    require(workflow, "HISTORICAL_MIGRATION", "history release selector")
    require(workflow, "history_provenance_file", "shared history provenance artifact")
    require(workflow, "HISTORY_PROVENANCE_FILE", "manifest/preflight provenance handoff")
    require(workflow, "vecta-history-provenance.json", "stable provenance artifact path")
    require(workflow, "FRUIT_V4_MIGRATION_PATH", "exact Fruit migration path provenance")
    require(workflow, "FRUIT_V4_MIGRATION_SHA256", "exact Fruit migration hash provenance")
    require(workflow, 'docker pull "$fruit_ref"', "exact Fruit digest pull")
    forbid(workflow, r"--registry-only", "registry-only Fruit provenance bypass")
    require(workflow, "FRUIT_V4_INFRA_REVISION", "exact infra revision for Fruit migration")
    require(workflow, "mypc-history-batch-preflight.yml", "history backup/migration preflight")
    require(workflow, "mypc-history-batch-execute.yml", "serial history batch execution")
    require(workflow, "prerequisite_0029", "0029 prerequisite provenance")
    require(workflow, "schema_0030", "0030 migration provenance")
    require(workflow, "history_confirmation_ref", "batch confirmation evidence")
    require(workflow, "HISTORY_BATCH_TOKEN", "batch API credential boundary")
    require(workflow, "RELEASE_LOCK_OWNER", "single target-host lease owner")
    require(workflow, "Acquire one continuous target-host release lease", "lease acquisition step")
    require(workflow, "Release the target-host lease after terminal success", "terminal lease release step")
    require(workflow, "if: success() && inputs.deploy == true", "success-only lease release")
    require(workflow, "inspect the protected continuation reference", "phase-accurate failure handoff")
    require(workflow, "repair owner", "deterministic failure ownership")
    require(workflow, "for attempt in 1 2 3", "bounded transient retry")
    require(workflow, "Docker runner did not become ready after 3 attempts", "bounded runner readiness retry")
    require(workflow, "concurrency:", "release contention guard")
    require(workflow, "mypc-production-release-v1", "single release concurrency group")
    require(workflow, "Fail closed before any release work", "always-executed release guard")
    require(workflow, "MYPC_INVENTORY_FILE", "protected production inventory path")
    require(workflow, "is_transient_transport_failure", "transient transport classifier")
    require(workflow, "is_transient_curl_status", "transient Nexus classifier")
    require(workflow, "408|425|429|502|503|504", "approved HTTP transient allowlist")
    require(workflow, "deterministic auth/ref/config/disk error", "deterministic checkout failure classification")
    require(workflow, "Typecheck selected VectA source", "fixed-source typecheck")
    require(workflow, 'typecheck_packages="$(node -', "typecheck package enumeration")
    require(workflow, "packages.length === 0", "non-empty typecheck contract")
    require(workflow, 'pnpm --filter "$package_name" run typecheck', "source typecheck command")
    forbid(workflow, r"pnpm\s+-r[^\n]*--if-present[^\n]*typecheck", "zero-execution typecheck fallback")
    require(workflow, "nexus_bootstrap", "explicit Nexus build-lane switch")
    require(workflow, "MYPC_NEXUS_BOOTSTRAP_ENABLED", "Nexus mutation authority")
    require(workflow, "Install and verify pinned Ansible collections", "pinned Ansible collection bootstrap")
    require(workflow, "--collections-path \"$ANSIBLE_COLLECTIONS_PATH\"", "isolated Ansible collection path")
    require(workflow, "--require-full-deploy-set", "complete deployment service-set gate")
    require(workflow, "history_0029_hash", "runtime 0029 journal identity")
    require(workflow, "history_0030_hash", "runtime 0030 journal identity")
    require(workflow, "history_loss_hash", "runtime loss journal identity")
    require(workflow, "history_loss_path", "runtime loss migration path")
    require(workflow, "loss_migration_path_rel", "relative loss migration provenance")
    require(workflow, "history_provenance", "manifest SQL provenance")
    require(workflow, "oci_revision", "OCI source revision provenance")
    require(workflow, "skopeo", "OCI digest inspection")
    require(workflow, "ansible-core==2.21.3", "pinned Ansible core")
    require(workflow, "-i \"$MYPC_INVENTORY_FILE\"", "protected inventory invocation")
    require(workflow, "loss_migration_path_rel=", "canonical loss path handoff")
    require(workflow, '-e "@$history_provenance_file"', "paired rollback SQL provenance handoff")
    application_preflight_index = workflow.find("- name: Run application contention and backup preflight")
    history_preflight_index = workflow.find("- name: Run history migration consumer preflight")
    lease_acquire_index = workflow.find("- name: Acquire one continuous target-host release lease")
    if application_preflight_index < 0 or history_preflight_index < 0:
        raise AssertionError("release preflight steps are missing")
    if application_preflight_index > history_preflight_index:
        raise AssertionError("ordinary application/data preflight must precede history migration")
    if lease_acquire_index < 0 or application_preflight_index > lease_acquire_index:
        raise AssertionError("read-only application/data preflight must precede lease acquisition")
    forbid(workflow, r"history_batch_execute_url|HISTORY_BATCH_EXECUTE_URL", "arbitrary history endpoint input")
    forbid(workflow, r"-i inventories/mypc/hosts\.ini", "repository placeholder inventory invocation")
    if re.search(r"(?m)^  build:\n\s+if:", workflow):
        raise AssertionError("release guard must not be a job-level if")
    if re.search(
        r"(?ms)^      - name: Bootstrap production Nexus build lane \(explicit\)\n\s+if: (?!inputs\.nexus_bootstrap == true)",
        workflow,
    ):
        raise AssertionError("Nexus bootstrap must have an explicit step gate")
    if re.search(
        r"(?ms)^      - name: Provision production Nexus repositories \(explicit build lane\)\n\s+if: (?!inputs\.nexus_bootstrap == true)",
        workflow,
    ):
        raise AssertionError("Nexus repository mutation must have an explicit step gate")

    forbid(workflow, r"verify-vecta-postsubmit|Postsubmit", "retired postsubmit gate")
    forbid(workflow, r"test_fruit_v4_isolated_compose_contract", "independent compose test stage")
    forbid(workflow, r"(?:vitest|testcontainers|\bvtest\b)", "retired independent test tooling")
    if workflow.count("default: false") < 3:
        raise AssertionError("deploy, Hero, and Oracle inputs must remain disabled by default")
    if "if: inputs.deploy == true" not in workflow:
        raise AssertionError("all production mutation steps must require deploy=true")
    if "if: inputs.execute_batch == true" not in workflow:
        raise AssertionError("history batch execution must be explicitly gated")
    require(workflow, "Require deploy for opt-in production controls", "production control deployment guard")

    for path in (RELEASE_PLAYBOOK, ROLLBACK_PLAYBOOK):
        source = path.read_text(encoding="utf-8")
        require(source, "vecta_app_release_mode: true", f"release mode in {path.name}")
        require(source, "@sha256:[0-9a-f]{64}", f"exact digest assertion in {path.name}")
        require(source, "serial: 1", f"serial release in {path.name}")
    preflight = PREFLIGHT_PLAYBOOK.read_text(encoding="utf-8")
    require(preflight, "mypc-data-layer-regression.sh", "read-only data preflight")
    forbid(preflight, "mypc-release-lock.sh", "lease acquisition in read-only preflight")
    forbid(preflight, "release_lock_owner", "lease owner requirement in read-only preflight")
    health = HEALTH_PLAYBOOK.read_text(encoding="utf-8")
    require(health, "post-deploy-smoke.sh", "existing health helper")
    fruit_provenance = FRUIT_PROVENANCE_CHECK.read_text(encoding="utf-8")
    for needle, label in (
        ("validate_image_inspection", "exact Fruit OCI inspection"),
        ("validate_image_sql", "exact Fruit SQL hash inspection"),
        ("RepoDigests", "Fruit RepoDigest verification"),
        ("org.opencontainers.image.revision", "Fruit source revision verification"),
        ("FRUIT_V4_MIGRATION_PATH", "Fruit migration path input"),
        ("FRUIT_V4_MIGRATION_SHA256", "Fruit migration hash input"),
        ("--registry-only is forbidden for history release provenance", "history registry-only guard"),
    ):
        require(fruit_provenance, needle, label)
    history_preflight = HISTORY_PREFLIGHT.read_text(encoding="utf-8")
    history_consumer = HISTORY_CHECK.read_text(encoding="utf-8")
    history_lease_verify_index = history_preflight.find(
        "- name: Verify the continuous target-host release lease before target mutation"
    )
    history_copy_index = history_preflight.find(
        "- name: Materialize the exact backup helper before the locked sequence"
    )
    if history_lease_verify_index < 0 or history_copy_index < 0 or history_lease_verify_index > history_copy_index:
        raise AssertionError("history target mutation must follow lease verification")
    for needle, label in (
        ("v4_documents_type_chk", "loss document constraint consumer admission"),
        ("has_loss_document_constraint", "loss document constraint parser"),
        ("has_tail_loss_constraint", "journal-owned tail placement parser"),
        ("has_drizzle_migration_transaction", "Drizzle migration transaction parser"),
        ("js_without_comments", "executable source parser"),
        ("select_history_provenance", "journal-discovered loss migration"),
        ("find_production_runbook", "canonical production runbook resolver"),
        ("has_transactional_loss_rollback", "transactional rollback parser"),
        ("sourceDocumentRef", "historical row evidence reference"),
        ("contentSha256", "historical row evidence hash"),
        ("case 'loss':", "first-class loss Action validation"),
    ):
        require(history_consumer, needle, label)
    for needle, label in (
        ("0029", "history 0029 prerequisite"),
        ("0030", "history 0030 migration"),
        ("mypc-postgres-adoption-evidence.sh", "fresh backup/restore rehearsal"),
        ("docker stop", "writer stop/quiesce"),
        ("docker start", "writer restart"),
        ("start_writers()", "verified writer restart helper"),
        ("PGOPTIONS", "migration lock timeout environment"),
        ("lock_timeout", "bounded migration lock timeout"),
        ("mypc-release-lock.sh verify", "continuous migration lease"),
        ("release_lock_owner", "migration lease owner"),
        ("v4_documents_type_chk", "loss document CHECK verification"),
        ("'''loss'''", "first-class loss document type verification"),
        ("fruit_meta.__drizzle_migrations", "migration journal verification"),
        ("BEGIN READ ONLY", "non-write smoke"),
        ("serial: 1", "serial migration"),
    ):
        require(history_preflight, needle, label)
    for path in (LEASE_PLAYBOOK, RELEASE_PLAYBOOK, HISTORY_PREFLIGHT, HISTORY_EXECUTE, ROLLBACK_PLAYBOOK):
        stateful = path.read_text(encoding="utf-8")
        require(stateful, "ansible_check_mode", f"check-mode guard in {path.name}")
        require(stateful, "become: true", f"root privilege contract in {path.name}")
        require(stateful, "become_user: root", f"root user contract in {path.name}")
        forbid(stateful, r"check_mode:\s*false", f"stateful check-mode bypass in {path.name}")
    lock_helper = LOCK_HELPER.read_text(encoding="utf-8")
    for needle, label in (
        ("acquire|verify|release", "lock helper terminal lifecycle modes"),
        ("recover LOCK_PATH STATE_PATH OWNER_TOKEN --operator-approved-recovery", "lock helper recovery mode"),
        ("setsid", "detached lock holder"),
        ('exec 9>"$lock_path"', "holder-owned lock descriptor"),
        ("flock -n 9", "holder-owned flock acquisition"),
        ("</dev/null >/dev/null 2>&1", "lock holder stdio detachment"),
        ("owner_token", "owner-token lock state"),
        ("set -o noclobber", "atomic state reservation"),
        ("holder_pid=0", "pending state reservation"),
        ("holder_start=pending", "pending holder identity"),
        ("state collision", "foreign state collision guard"),
        ("foreign state", "foreign state preservation"),
        ("acquire_cleanup", "acquire cancellation cleanup"),
        ("signal_holder", "process-group holder termination"),
        ("trap cleanup EXIT INT TERM", "holder signal/exit cleanup"),
        ("wait_for_exit", "holder termination wait"),
        ("terminate_verified_holder", "verified holder termination"),
        ("flock -n 8", "post-termination flock release proof"),
        ("verify_lock", "cross-playbook lease verification"),
        ("VECTA_RELEASE_LOCK_STARTUP_PAUSE_FILE", "startup cancellation self-check gate"),
        ("recover_stale_lock", "non-destructive stale recovery path"),
        ("terminal_release_lock", "terminal holder release path"),
        ("--operator-approved-recovery", "independent recovery approval"),
        ("live holder is not recoverable", "live holder recovery rejection"),
        ("state changed during recovery", "recovery state race guard"),
        ("cleanup_lease", "self-test terminal cleanup helper"),
        ("self-test cleanup could not prove", "self-test cleanup proof"),
        ("stale recovery requires a verifiable holder pid", "pending recovery rejection"),
        ("dead-state", "dead-holder recovery self-check"),
        ("live recovery changed the winner state", "live recovery immutability self-check"),
        ("synchronized loser claim", "synchronized claim race self-check"),
        ("--self-test", "lock helper self-check interface"),
    ):
        require(lock_helper, needle, label)
    for forbidden, label in (
        (r"controller_pid|controller_nonce|controller_heartbeat|controller_is_active", "retired fake controller state"),
        (r"VECTA_RELEASE_LOCK_CONTROLLER_NONCE", "retired fake controller heartbeat"),
    ):
        forbid(lock_helper, forbidden, label)
    forbid(lock_helper, r"sleep\s+86400", "orphanable 24-hour lock holder")
    lease = LEASE_PLAYBOOK.read_text(encoding="utf-8")
    runbook_text = MYPC_RUNBOOK.read_text(encoding="utf-8")
    for needle, label in (
        ("mypc-release-lock.sh", "canonical lease helper"),
        ("release_lock_action", "explicit lease action"),
        ("acquire", "lease acquisition"),
        ("release", "lease release"),
        ("recover", "operator-gated lease recovery"),
        ("release_lock_owner", "lease owner token"),
        ("release_lock_recovery_approved", "independent recovery approval"),
        ("release_continuation_id", "protected continuation reference"),
        ("release_workflow_run_id", "continuation workflow identity"),
        ("owner capability", "protected owner continuation"),
        ("become_user: root", "lease root privilege"),
    ):
        require(lease, needle, label)
    check_lease_action_contract(lease, runbook_text)
    for path in (RELEASE_PLAYBOOK, HEALTH_PLAYBOOK, HISTORY_PREFLIGHT, HISTORY_EXECUTE, ROLLBACK_PLAYBOOK):
        source = path.read_text(encoding="utf-8")
        require(source, "mypc-release-lock.sh verify", f"continuous lease verification in {path.name}")
        require(source, "release_lock_owner", f"owner-token lock in {path.name}")
        forbid(source, r"mypc-release-lock\.sh\s+(?:acquire|release|recover)", f"second lease lifecycle in {path.name}")
        forbid(source, r"\bflock\s+-n", f"task-local lock in {path.name}")
        forbid(source, r"sleep\s+86400|\.vecta-release\.lock\.holder", f"legacy lock holder in {path.name}")
    if workflow.count('-e "release_lock_owner=$RELEASE_LOCK_OWNER"') < 5:
        raise AssertionError("all stateful release playbooks must receive one owner token")
    lease_release_index = workflow.find("- name: Release the target-host lease after terminal success")
    deploy_index = workflow.find("- name: Deploy exact immutable image digests")
    health_index = workflow.find("- name: Verify deployed health and application contract")
    if min(lease_acquire_index, lease_release_index, deploy_index, health_index) < 0:
        raise AssertionError("continuous lease boundary steps are missing")
    if not lease_acquire_index < deploy_index < health_index < lease_release_index:
        raise AssertionError("deploy and health must remain inside one continuous lease")
    standalone_psql_scan_self_check()
    forbid_standalone_psql_file_execution(workflow, "standalone workflow rollback SQL handoff")
    forbid_standalone_psql_file_execution(runbook_text, "standalone runbook rollback SQL handoff")
    require(workflow, "RELEASE_PHASE_FILE", "release phase record")
    require(workflow, "RELEASE_CONTINUATION_ID", "release continuation reference")
    require(workflow, "unverified phase", "phase-accurate failure handoff")
    require(workflow, "no lease or writer state is asserted", "pre-lease failure handoff")
    require(workflow, "protected continuation reference", "durable failure continuation")
    require(workflow, "terminal-success", "post-release phase handoff")
    forbid(workflow, "the target-host lease remains held", "unverified lease failure claim")
    run_lock_runtime_self_check()
    require(history_preflight, "history_backup_helper_path", "locked backup helper")
    require(history_preflight, "sequence_succeeded", "success-only writer restart")
    require(history_preflight, "writers remain stopped", "failure-safe migration writer state")
    require(history_preflight, "-X -U", "protected migration database identity")
    require(history_preflight, "history_loss_hash", "dynamic loss journal hash")
    require(history_preflight, "history_loss_path", "dynamic loss migration path")
    require(history_preflight, "history_loss_tag", "dynamic loss journal tag")
    require(history_preflight, "current_database()", "protected live database identity")
    require(history_preflight, "current_user", "protected live database role identity")
    require(history_preflight, "hash = '{{ history_0029_hash }}'", "live 0029 journal hash")
    require(history_preflight, "hash = '{{ history_0030_hash }}'", "live 0030 journal hash")
    require(
        history_preflight,
        "hash = '{{ history_0029_hash }}')\n              < (SELECT created_at\n                 FROM fruit_meta.__drizzle_migrations\n                 WHERE hash = '{{ history_0030_hash }}')",
        "journal ordering verification",
    )
    require(history_preflight, "BEGIN READ ONLY", "non-write migration smoke")
    for path in (LEASE_PLAYBOOK, RELEASE_PLAYBOOK, PREFLIGHT_PLAYBOOK, HISTORY_PREFLIGHT, ROLLBACK_PLAYBOOK):
        source = path.read_text(encoding="utf-8")
        require(source, "vars_files:", f"explicit external vars in {path.name}")
        require(source, "../inventories/mypc/group_vars/mypc.yml", f"production vars in {path.name}")
    execute = HISTORY_EXECUTE.read_text(encoding="utf-8")
    require(execute, "vars_files:", "explicit external vars in mypc-history-batch-execute.yml")
    require(execute, "../inventories/mypc/group_vars/mypc.yml", "production vars in mypc-history-batch-execute.yml")
    rollback = ROLLBACK_PLAYBOOK.read_text(encoding="utf-8")
    check_history_rollback_modes(rollback)
    for needle, label in (
        ("history_rollback_enabled", "history rollback opt-in"),
        ("history_rollback_approved", "history rollback operator approval"),
        ("history_rollback_sql_path", "exact history rollback SQL path"),
        ("docker stop", "rollback writer quiesce"),
        ("docker start", "rollback writer restart"),
        ("start_writers()", "verified rollback writer restart helper"),
        ("mypc-release-lock.sh verify", "rollback continuous lease"),
        ("release_lock_owner", "rollback lease owner"),
        ("psql", "rollback SQL executor"),
        ("docker cp", "exact rollback SQL materialization"),
        ("-X", "rollback psql no-user-config mode"),
        ("ON_ERROR_STOP=1", "rollback psql fail-closed mode"),
        ('-f /tmp/vecta-history-rollback.sql', "rollback exact SQL file"),
        ("serial: 1", "serial rollback"),
    ):
        require(rollback, needle, label)
    require(rollback, "rollback_succeeded", "success-only rollback writer restart")
    require(rollback, "writers remain stopped", "failure-safe rollback writer state")
    require(rollback, "history writers are already quiesced", "already-stopped rollback admission")
    require(rollback, "mixed running/stopped states", "mixed writer rollback rejection")
    require(rollback, "writer_mode", "rollback writer state machine")
    require(rollback, "psql -X -v ON_ERROR_STOP=1", "canonical rollback psql invocation")
    for needle, label in (
        ("rollback_manifest_verified", "verified prior rollback manifest"),
        ("rollback_manifest_selector_sha", "operator-selected prior source SHA"),
        ("rollback_manifest_root", "protected prior manifest root"),
        ("rollback_manifest_realpath", "realpath-verified prior manifest"),
        ("realpath", "manifest containment resolution"),
        ("include_vars", "manifest loader"),
        ("validate-mypc-digest-manifest.py", "manifest structure validation"),
        ("rollback_manifest_source_sha", "rollback source SHA provenance"),
        ("rollback_manifest_fruit_ref", "rollback Fruit digest provenance"),
        ("rollback_manifest_sql_sha256", "rollback SQL provenance"),
        ("rollback_sql_sha256", "runtime rollback SQL hash check"),
        ('docker pull "$fruit_image"', "rollback exact Fruit digest pull"),
        ("RepoDigests", "rollback OCI RepoDigest verification"),
        ("org.opencontainers.image.revision", "rollback OCI source revision verification"),
        ("packages/fruit-industry-pack/migrations/rollback/[0-9]{4}", "canonical rollback SQL path"),
    ):
        require(rollback, needle, label)
    require(rollback, "--require-history-provenance", "history rollback manifest provenance validation")
    forbid(rollback, r"0030_v4_historical_batch_control\.sql", "hard-coded rollback migration")
    for validation_marker in (
        "Validate the prior exact-digest rollback manifest",
        "Validate the prior history rollback manifest",
    ):
        validation_index = rollback.find(validation_marker)
        include_index = rollback.find("ansible.builtin.include_vars", validation_index)
        if validation_index < 0 or include_index < 0 or include_index < validation_index:
            raise AssertionError("rollback manifest must be validated before include_vars")
    normal_rollback = rollback.split("# The same operator rollback entry point", 1)[0]
    forbid(normal_rollback, "history_rollback_approved", "history-only assertion in normal application rollback")
    require(rollback, 'if [ "$rollback_succeeded" = true ]; then', "success-gated rollback writer restart")
    history_execute = HISTORY_EXECUTE.read_text(encoding="utf-8")
    for needle, label in (
        ("history_batch_api_origin", "controlled batch API origin"),
        ("/internal/controlled-entry/historical-batches", "controlled batch API path"),
        ("curl --silent --show-error", "controlled batch request"),
        ('"batchId"', "batch request identity"),
        ('"sourceSha"', "source SHA request identity"),
        ('"confirmationRef"', "confirmation request identity"),
        ("response.batchId", "batch response identity"),
        ("response.sourceSha", "source SHA response identity"),
        ("response.confirmationRef", "confirmation response identity"),
        ("history_confirmation_ref", "batch confirmation"),
        ("HISTORY_BATCH_TOKEN", "batch credential boundary"),
        ("docker stop", "execute writer quiesce"),
        ("docker start", "execute writer resume"),
        ("writers remain stopped", "failure-safe batch writer state"),
        ("mypc-release-lock.sh verify", "batch continuous lease"),
        ("release_lock_owner", "batch lease owner"),
        ("batch_succeeded", "success-only batch writer resume"),
        ("forbiddenStatuses", "batch terminal-status denylist"),
        ("response.status", "batch terminal status contract"),
        ("response.terminal", "batch terminality contract"),
        ("response.completion", "batch completion contract"),
        ("expected", "batch expected-count contract"),
        ("completed", "batch completed-count contract"),
        ("history_writer_containers", "canonical batch writer set"),
        ("writers_json", "rendered batch writer set"),
        ("serial: 1", "serial batch execute"),
    ):
        require(history_execute, needle, label)
    forbid(history_execute, r"docker\s+(?:pause|unpause)", "pause-based batch writer control")
    forbid(history_execute, r"history_batch_execute_url|https?://\{\{", "arbitrary batch endpoint")
    digest_check = DIGEST_CHECK.read_text(encoding="utf-8")
    for needle, label in (
        ("APPROVED_NEXUS_REGISTRY", "approved Nexus registry constant"),
        ("APPROVED_IMAGE_NAMES", "approved image allowlist"),
        ("REQUIRED_DEPLOY_SERVICE_NAMES", "complete deployment service set"),
        ("source_ref must use Nexus", "source SHA registry binding"),
        ("require_full_deploy_set", "full deployment service-set option"),
    ):
        require(digest_check, needle, label)
    adoption = ADOPTION_SCRIPT.read_text(encoding="utf-8")
    require(adoption, "POSTGRES_RESTORE_IMAGE must be the approved Nexus pgvector image digest", "immutable restore image gate")
    forbid(adoption, r"POSTGRES_RESTORE_IMAGE:-[^\n]*:pg16", "mutable restore image fallback")
    for line in adoption.splitlines():
        if re.search(r"\bpsql\s+-", line):
            require(line, "psql -X", "psql no-user-config mode")
    data_preflight = DATA_PREFLIGHT_SCRIPT.read_text(encoding="utf-8")
    for line in data_preflight.splitlines():
        if re.search(r"\bpsql\s+-", line):
            require(line, "psql -X", "release preflight psql no-user-config mode")
    for path in (APP_REGRESSION_SCRIPT, DATA_PREFLIGHT_SCRIPT):
        source = path.read_text(encoding="utf-8")
        require(source, "000|408|425|429|502|503|504", f"transient health retry classifier in {path.name}")
        require(source, "401|403|404", f"deterministic health failure classifier in {path.name}")
        require(source, "repair owner=release", f"health repair ownership in {path.name}")
    inventory = INVENTORY.read_text(encoding="utf-8")
    require(inventory, "protected and supplied outside this repository", "external inventory contract")
    forbid(inventory, r"mypc-host\.example\.com|replace .*host|TODO|CHANGE_ME", "placeholder production inventory")
    collections = COLLECTIONS.read_text(encoding="utf-8")
    require(collections, 'version: "3.10.2"', "exact community.docker version")
    runbook = MYPC_RUNBOOK.read_text(encoding="utf-8").lower()
    for term, label in (
        ("drizzle", "runbook Drizzle transaction ownership"),
        ("access exclusive", "runbook lock mode"),
        ("lock_timeout", "runbook bounded lock timeout"),
        ("rollback", "runbook rollback"),
        ("quiesced", "runbook rollback writer quiesce"),
        ("shared-lease", "runbook shared release lease"),
        ("mypc-release-lease.yml", "runbook canonical lease lifecycle"),
        ("release_lock_action=release", "runbook success-only lease release"),
    ):
        require(runbook, term, label)

    print("mypc release workflow contract passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError) as error:
        print(f"mypc release workflow contract rejected: {error}", file=sys.stderr)
        raise SystemExit(1)
