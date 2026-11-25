import importlib.util
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

    def test_scoped_driver_patch_only_adds_guard_statements(self):
        patch = (HERE.parent / "releases/scoped/require-exact-release.patch").read_text()
        self.assertFalse(any(line.startswith("-") and not line.startswith("--- ") for line in patch.splitlines()))
        self.assertEqual(patch.count("+++ b/scripts/release/run-release.sh"), 1)
        self.assertIn('if [[ "$PRODUCTION_DEPLOYMENT_SHA" != "${SCOPED_BASELINE:', patch)
        self.assertIn('if [[ "$DEPLOYED_SHA" != "${SCOPED_SOURCE:', patch)


if __name__ == "__main__":
    unittest.main()
