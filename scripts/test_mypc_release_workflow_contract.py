#!/usr/bin/env python3
"""Static contract for the manually dispatched mypc release workflow."""

import json
import os
from pathlib import Path
import subprocess
import tempfile

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
GITHUB_VECTA = "https://github.com/ZenoWangzy/vecta.git"
CREDENTIAL_HELPER = '!f() { echo username=x-access-token; echo "password=$GH_TOKEN"; }; f'


def require(fragment: str, source: str = RELEASE) -> None:
    assert fragment in source, fragment


def git_env(rules: list[tuple[str, str]], token: str) -> dict[str, str]:
    env = {**os.environ, "GH_TOKEN": token, "GIT_CONFIG_COUNT": str(len(rules))}
    for index, (key, value) in enumerate(rules):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    return env


def git_run(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], env=env, text=True, capture_output=True)


def ref_exists(repo: Path, ref: str) -> bool:
    return git_run(["--git-dir", str(repo), "show-ref", "--verify", "-q", ref]).returncode == 0


def git_mechanism_proof() -> None:
    """Fetch reads the warm cache; push goes to the GitHub remote. file:// stands in for GitHub."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        seed, github, cache, work = root / "seed", root / "github.git", root / "cache.git", root / "work"
        identity = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        assert git_run(["init", "-q", "-b", "main", str(seed)]).returncode == 0
        assert git_run(["-C", str(seed), *identity, "commit", "-q", "--allow-empty", "-m", "seed"]).returncode == 0
        sha = git_run(["-C", str(seed), "rev-parse", "HEAD"]).stdout.strip()
        for bare in (github, cache):
            assert git_run(["init", "-q", "--bare", str(bare)]).returncode == 0
            assert git_run(["-C", str(seed), "push", "-q", str(bare), "HEAD:refs/heads/main"]).returncode == 0
        # Only the cache has this ref, so a successful fetch proves the read went to the cache.
        assert git_run(["-C", str(seed), "push", "-q", str(cache), f"{sha}:refs/heads/cache-only"]).returncode == 0
        assert git_run(["init", "-q", str(work)]).returncode == 0
        github_url = f"file://{github}"
        assert git_run(["-C", str(work), "remote", "add", "origin", github_url]).returncode == 0

        fixed = [(f"url.{cache}.insteadOf", github_url), (f"url.{github_url}.pushInsteadOf", github_url)]
        env = git_env(fixed, "fake-token")
        fetched = git_run(
            ["-C", str(work), "fetch", "-q", "origin", "refs/heads/cache-only:refs/remotes/origin/cache-only"], env
        )
        assert fetched.returncode == 0, fetched.stderr
        pushed = git_run(["-C", str(work), "push", "-q", "origin", f"{sha}:refs/heads/train/release-572"], env)
        assert pushed.returncode == 0, pushed.stderr
        assert ref_exists(github, "refs/heads/train/release-572"), "push must land on GitHub"
        assert not ref_exists(cache, "refs/heads/train/release-572"), "push must not land in the cache"

        # The insteadOf-only rules that #135 shipped: this is the bug the contract blocks.
        old = git_env([(f"url.{cache}.insteadOf", github_url)], "fake-token")
        assert git_run(["-C", str(work), "push", "-q", "origin", f"{sha}:refs/heads/train/release-old"], old).returncode == 0
        assert ref_exists(cache, "refs/heads/train/release-old"), "old rules send the push into the cache"
        assert not ref_exists(github, "refs/heads/train/release-old")


def credential_fill_proof() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "work"
        assert git_run(["init", "-q", str(work)]).returncode == 0
        env = git_env([("credential.https://github.com.helper", CREDENTIAL_HELPER)], "fake-token")
        result = subprocess.run(
            ["git", "-C", str(work), "credential", "fill"], env=env, text=True,
            input="protocol=https\nhost=github.com\n\n", capture_output=True,
        )
        assert result.returncode == 0, result.stderr
        assert "username=x-access-token" in result.stdout.splitlines(), result.stdout
        assert "password=fake-token" in result.stdout.splitlines(), result.stdout


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

    # vecta-infra checkout: actions/checkout has no slow-link retry (run 44391317843, 44417360328).
    # Each job's infra checkout must be an inline bounded fetch of the exact SHA with a low-speed abort.
    for job_name in ("validate", "deploy"):
        infra_checkouts = [
            step for step in steps_for(release, job_name)
            if str(step.get("name", "")).startswith("Checkout infra")
        ]
        assert len(infra_checkouts) == 1, job_name
        assert "uses" not in infra_checkouts[0], infra_checkouts[0]
        infra_run = str(infra_checkouts[0].get("run", ""))
        for fragment in (
            "https://github.com/ZenoWangzy/vecta-infra.git",
            "for attempt in 1 2 3 4 5; do",
            "GIT_HTTP_LOW_SPEED_LIMIT=1024",
            "GIT_HTTP_LOW_SPEED_TIME=60",
            'git -C "$target" fetch --depth=1 origin "$GITHUB_SHA"',
            'test "$(git -C "$target" rev-parse HEAD)" = "$GITHUB_SHA"',
        ):
            assert fragment in infra_run, (job_name, fragment)
        assert "extraheader" not in infra_run and "git config --global" not in infra_run

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
    # engine-versions persists across runs; picking a dir by name prefix can return an older pack of the same engine pin.
    assert "-print -quit" not in engine_build_run
    assert 'find "$AGENT_HOME/engine-versions"' not in engine_build_run
    assert 'version_dir="$AGENT_HOME/engine-versions/$version_id"' in engine_build_run
    assert "$(vecta_rev .zenoforge/build/pack)" in engine_build_run

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
    # run-release's git_fetch_bounded fetches origin main from GitHub; the runner has no git credential
    # for it, so the deploy step must rewrite that URL to the warm cache, env only, never a gitconfig.
    deploy_run = next(
        str(step.get("run", "")) for step in steps_for(release, "deploy")
        if step.get("name") == "Deploy or print dry-run plan"
    )
    require("cache=/home/github-runner/.cache/vecta-main.git", deploy_run)
    require('export GIT_CONFIG_COUNT=3 GIT_CONFIG_KEY_0="url.$cache.insteadOf"', deploy_run)
    require(f"GIT_CONFIG_VALUE_0={GITHUB_VECTA}", deploy_run)
    # insteadOf also rewrites pushes (#135 sent release pushes into the cache). Pushes must go back to GitHub,
    # and GitHub needs a credential that reads GH_TOKEN from the step env.
    require(f"GIT_CONFIG_KEY_1=url.{GITHUB_VECTA}.pushInsteadOf GIT_CONFIG_VALUE_1={GITHUB_VECTA}", deploy_run)
    require(f"GIT_CONFIG_KEY_2=credential.https://github.com.helper GIT_CONFIG_VALUE_2='{CREDENTIAL_HELPER}'", deploy_run)
    assert deploy_run.index("GIT_CONFIG_COUNT") < deploy_run.index("bash vecta/scripts/release/mypc-release.sh")
    git_mechanism_proof()
    credential_fill_proof()
    require('cache="/home/github-runner/.cache/vecta-main.git"', FETCH_SCRIPT_PATH.read_text())
    assert "git config --global" not in RELEASE and "extraheader" not in RELEASE
    print("mypc release workflow contract: PASS")


if __name__ == "__main__":
    main()
