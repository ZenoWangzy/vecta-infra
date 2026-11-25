import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("scoped", HERE / "scoped-release-source.py")
scoped = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scoped)


class ScopedRelease(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "ci@example.invalid")
        self.git("config", "user.name", "CI")
        self.commit({"packages/fruit/card": "v1", "docs/a.md": "v1"})
        self.base = self.sha()
        self.commit({"packages/fruit/unrelated": "keep on main"})
        self.commit({"packages/fruit/card": "v2", "docs/a.md": "v2"})
        self.merge = self.sha()
        self.git("update-ref", "refs/remotes/origin/main", self.merge)
        self.git("checkout", "-q", "--detach", self.base)
        self.commit({"packages/fruit/card": "v2"})
        self.tree = self.git("rev-parse", "HEAD^{tree}")
        self.manifest = dict(pull_request=123, baseline=self.base, merged_revision=self.merge,
                             expected_tree=self.tree, documentation_only=["docs/a.md"])

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True).strip()

    def sha(self):
        return self.git("rev-parse", "HEAD")

    def commit(self, files):
        for name, data in files.items():
            file = self.repo / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(data + "\n")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture change")

    def test_reproduce_only_pr_on_baseline(self):
        source = scoped.prepare(self.repo, self.manifest, self.base)
        self.assertEqual(source, scoped.prepare(self.repo, self.manifest, self.base))
        self.assertEqual(self.git("rev-parse", source + "^"), self.base)
        self.assertEqual(self.git("rev-parse", source + "^{tree}"), self.tree)
        self.assertEqual(self.git("diff", "--name-only", self.base, source), "packages/fruit/card")
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_reject_wrong_base_or_tree(self):
        with self.assertRaisesRegex(ValueError, "baseline"):
            scoped.prepare(self.repo, self.manifest, self.merge)
        with self.assertRaisesRegex(ValueError, "tree"):
            scoped.prepare(self.repo, dict(self.manifest, expected_tree=self.base), self.base)

    def test_cannot_omit_a_product_file(self):
        with self.assertRaisesRegex(ValueError, "documentation"):
            scoped.prepare(self.repo, dict(self.manifest, documentation_only=["packages/fruit/card"]), self.base)

    def test_reject_unmerged_change(self):
        self.git("update-ref", "refs/remotes/origin/main", self.base)
        with self.assertRaises(subprocess.CalledProcessError):
            scoped.prepare(self.repo, self.manifest, self.base)

    def test_driver_guards(self):
        bash = os.environ.get("BASH_FOR_TEST", "bash")
        guards = HERE / "scoped-driver-guards.sh"
        prelude = f"""set -eu; source "{guards}"
SCOPED_ISSUE=8232; SCOPED_PR=8187; SCOPED_UPSTREAM=upstream; SCOPED_SOURCE=backport
N=8232; PR_NUM=8187; MERGE_SHA=upstream
SCOPED_SERVICES=fruit,web
declare -a RELEASE_TARGET_SERVICES=(fruit web) RELEASE_IMAGE_DRIFT_BLOCKED_SERVICES=() FLEET_ROLL_PENDING_SERVICES=()
image=sha256:{'a' * 64}
declare -A DEPLOYED_IMAGE_IDS=([fruit]=$image [web]=$image) DEPLOYED_IMAGE_REVISIONS=([fruit]=backport [web]=backport)
"""
        for command, expected in (
            ("scoped_require_anchor && scoped_require_plan && scoped_require_readbacks", 0),
            ("N=100; scoped_require_anchor", 1),
            ("scoped_ticket_deployed 8232 upstream backport", 0),
            ("scoped_ticket_deployed 100 upstream backport", 1),
            ("RELEASE_IMAGE_DRIFT_BLOCKED_SERVICES=(web); scoped_require_plan", 1),
            ("RELEASE_TARGET_SERVICES=(fruit); scoped_require_plan", 1),
            ("unset 'DEPLOYED_IMAGE_IDS[web]'; scoped_require_readbacks", 1),
            ("DEPLOYED_IMAGE_REVISIONS[web]=wrong; scoped_require_readbacks", 1),
        ):
            with self.subTest(command=command):
                result = subprocess.run([bash, "-c", prelude + command], capture_output=True)
                self.assertEqual(result.returncode, expected, result.stderr)

    def test_guarded_driver_boundaries(self):
        patch = (HERE.parent / "releases/scoped/require-exact-release.patch").read_text()
        self.assertIn('  scoped_require_plan || exit 75\n   if ((${#RELEASE_IMAGE_DRIFT_BLOCKED_SERVICES', patch)
        self.assertIn('scoped_require_readbacks || return 1', patch)
        self.assertIn('scoped_require_anchor || exit 65', patch)
        self.assertIn('release_preflight_args=(--mode release --merge-sha "$SCOPED_SOURCE")', patch)
        self.assertIn('if [[ "$PRODUCTION_DEPLOYMENT_SHA" != "${SCOPED_BASELINE:', patch)

    def test_publisher_preflight_uses_verified_source_before_release(self):
        wrapper = (HERE / "run-scoped-release.sh").read_text()
        preflight = wrapper.index('bash scripts/prepare-scoped-fruit-publisher.sh "$VECTA_REPO_DIR"')
        self.assertLess(wrapper.index("bash scripts/check-scoped-release.sh"), preflight)
        self.assertLess(preflight, wrapper.index('bash "$driver/scripts/release/mypc-release.sh"'))
        self.assertIn('if [[ ",$SCOPED_SERVICES," == *,fruit-industry-pack,* ]]', wrapper)

        fake = self.repo / "pnpm"
        fake.write_text("""#!/bin/sh
set -eu
test -z "${GH_TOKEN:-}${VECTA_READ_TOKEN:-}${FLEET_FRUIT_PLATFORM_TOKEN:-}"
echo "$*" >> invocations
case "$*" in
  *install*) test ! -f fail-install ;;
  *) echo '> pnpm run'; cat result.json ;;
esac
""")
        fake.chmod(0o755)
        env = dict(os.environ, PATH=str(self.repo) + os.pathsep + os.environ["PATH"],
                   GH_TOKEN="fixture", VECTA_READ_TOKEN="fixture", FLEET_FRUIT_PLATFORM_TOKEN="fixture")
        command = ["bash", str(HERE / "prepare-scoped-fruit-publisher.sh"), str(self.repo)]
        log = self.repo / "invocations"
        result = self.repo / "result.json"
        result.write_text('{"mode":"dry-run","manifest":{"packId":"fruit-v4-workbench","version":"v1"}}')
        subprocess.run(command, check=True, env=env, capture_output=True)
        install, dry = log.read_text().splitlines()
        for arg in ("--frozen-lockfile", "--prod=false", "--ignore-scripts"):
            self.assertIn(arg, install)
        self.assertIn("platform:publish-v4-workbench -- --dry-run", dry)
        result.write_text('{"mode":"dry-run","manifest":{"packId":"wrong","version":"v1"}}')
        self.assertNotEqual(subprocess.run(command, env=env, capture_output=True).returncode, 0)
        log.unlink()
        (self.repo / "fail-install").touch()
        self.assertNotEqual(subprocess.run(command, env=env, capture_output=True).returncode, 0)
        self.assertEqual(log.read_text().splitlines(), [install])


if __name__ == "__main__":
    unittest.main()
