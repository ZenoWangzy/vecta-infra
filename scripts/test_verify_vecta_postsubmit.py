#!/usr/bin/env python3
"""Regression tests for the exact-SHA VectA Postsubmit evidence gate."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("verify-vecta-postsubmit.py")
SPEC = importlib.util.spec_from_file_location("verify_vecta_postsubmit", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


SHA = "0123456789abcdef0123456789abcdef01234567"


def run(
    *,
    run_id: int = 10,
    sha: str = SHA,
    branch: str = "main",
    event: str = "push",
    status: str = "completed",
    created_at: str = "2026-08-25T00:00:00Z",
) -> dict[str, object]:
    return {
        "id": run_id,
        "head_sha": sha,
        "head_branch": branch,
        "event": event,
        "status": status,
        "created_at": created_at,
    }


def job(
    *,
    job_id: int = 20,
    name: str = "Postsubmit validate",
    status: str = "completed",
    conclusion: str = "success",
) -> dict[str, object]:
    return {
        "id": job_id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
    }


class PostsubmitEvidenceTests(unittest.TestCase):
    def find(self, runs: list[dict[str, object]], jobs: dict[int, list[dict[str, object]]]):
        return MODULE.find_evidence(
            runs,
            lambda run_id: jobs.get(run_id, []),
            sha=SHA,
            branch="main",
        )

    def test_accepts_only_completed_successful_exact_job(self) -> None:
        self.assertEqual(self.find([run()], {10: [job()]}), (10, 20))

    def test_rejects_wrong_sha_branch_event_or_status(self) -> None:
        cases = [
            run(sha="f" * 40),
            run(branch="develop"),
            run(event="pull_request"),
            run(status="in_progress"),
        ]
        for candidate in cases:
            with self.subTest(candidate=candidate):
                self.assertIsNone(self.find([candidate], {10: [job()]}))

    def test_rejects_skipped_cancelled_failed_or_incomplete_job(self) -> None:
        cases = [
            job(conclusion="skipped"),
            job(conclusion="cancelled"),
            job(conclusion="failure"),
            job(status="in_progress", conclusion="success"),
            job(name="Build declared packages"),
        ]
        for candidate in cases:
            with self.subTest(candidate=candidate):
                self.assertIsNone(self.find([run()], {10: [candidate]}))

    def test_uses_an_older_exact_run_when_newest_lacks_the_job(self) -> None:
        newest = run(run_id=11, created_at="2026-08-25T02:00:00Z")
        older = run(run_id=10, created_at="2026-08-25T01:00:00Z")
        self.assertEqual(
            self.find([older, newest], {11: [], 10: [job()]}),
            (10, 20),
        )


class EvidenceQueryTests(unittest.TestCase):
    """The runs query must filter server-side by head_sha.

    Fetching the last 100 completed push runs in full is about 1.2 MB, which a
    slow link truncates (http.client.IncompleteRead), failing the gate for a
    commit whose Postsubmit is green.
    """

    def test_runs_query_filters_by_head_sha(self) -> None:
        recorded: list[tuple[str, dict[str, str]]] = []

        class FakeClient:
            def __init__(self, *, api_url: str, token: str) -> None:
                pass

            def get_json(self, path: str, query: dict[str, str]) -> dict[str, object]:
                recorded.append((path, query))
                if path.endswith("/jobs"):
                    return {"jobs": [job()]}
                return {"workflow_runs": [run()]}

        original = MODULE.GitHubClient
        MODULE.GitHubClient = FakeClient
        try:
            evidence = MODULE.verify(
                repo="ZenoWangzy/vecta",
                sha=SHA,
                branch="main",
                token="t",
                api_url="https://api.github.com",
            )
        finally:
            MODULE.GitHubClient = original

        self.assertEqual(evidence, MODULE.EvidenceResult(10, 20, SHA))
        runs_query = next(q for path, q in recorded if path.endswith("/actions/runs"))
        self.assertEqual(runs_query.get("head_sha"), SHA)


class LastStageCopyPrefixTests(unittest.TestCase):
    def test_keeps_only_final_stage_and_derives_src_from_dist(self) -> None:
        dockerfile = """
FROM node:20 AS builder
COPY packages/foo packages/foo
RUN build

FROM node:20
COPY --from=builder /app/packages/foo/dist packages/foo/dist
COPY --from=builder /app/packages/foo/package.json packages/foo/
COPY --from=builder /usr/local/bin/node /usr/local/bin/node
COPY scripts/entrypoint.sh /app/entrypoint.sh
"""
        self.assertEqual(
            sorted(MODULE._last_stage_copy_prefixes(dockerfile)),
            sorted(
                [
                    "packages/foo/src",
                    "packages/foo/package.json",
                    "scripts/entrypoint.sh",
                ]
            ),
        )


class LoadPrefixTableTests(unittest.TestCase):
    def test_reads_contract_and_dockerfiles_from_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            (root / "scripts" / "production-image-contract.json").write_text(
                json.dumps(
                    {"images": [{"name": "demo", "dockerfile": "packages/demo/Dockerfile"}]}
                )
            )
            (root / "packages" / "demo").mkdir(parents=True)
            (root / "packages" / "demo" / "Dockerfile").write_text(
                "FROM node:20\nCOPY packages/demo packages/demo\n"
            )
            prefixes, recipe_paths = MODULE.load_prefix_table(root)
        self.assertIn(("demo", "packages/demo"), prefixes)
        self.assertEqual(
            recipe_paths,
            {"scripts/production-image-contract.json", "packages/demo/Dockerfile"},
        )


class DiffAffectsImageTests(unittest.TestCase):
    def test_matches_prefix_directory_or_recipe_file_only(self) -> None:
        prefixes = [("demo", "packages/demo/src")]
        recipe_paths = {"scripts/production-image-contract.json"}
        paths = [
            "packages/demo/src/index.ts",  # inside the prefix dir -> hit
            "packages/demo/src2/index.ts",  # look-alike prefix -> not a hit
            "docs/readme.md",  # unrelated -> not a hit
            "scripts/production-image-contract.json",  # the recipe itself -> hit
        ]
        self.assertEqual(
            MODULE.diff_affects_image(paths, prefixes, recipe_paths),
            ["packages/demo/src/index.ts", "scripts/production-image-contract.json"],
        )


class SelectEvidencedAncestorTests(unittest.TestCase):
    def test_returns_first_ancestor_with_evidence(self) -> None:
        self.assertEqual(
            MODULE.select_evidenced_ancestor(
                ["a", "b", "c"], lambda s: {"b": (1, 2)}.get(s)
            ),
            ("b", 1, 2),
        )

    def test_returns_none_when_no_ancestor_has_evidence(self) -> None:
        self.assertIsNone(MODULE.select_evidenced_ancestor(["a"], lambda s: None))


class AncestorFallbackVerifyTests(unittest.TestCase):
    """Ticket 141: a tip with no evidence falls back to an evidenced ancestor,
    but only when nothing between that ancestor and the tip is image-affecting.
    """

    SHA_TIP = "1" * 40
    SHA_ANCESTOR = "2" * 40

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        (root / "scripts").mkdir()
        (root / "scripts" / "production-image-contract.json").write_text(
            json.dumps(
                {"images": [{"name": "demo", "dockerfile": "packages/demo/Dockerfile"}]}
            )
        )
        (root / "packages" / "demo").mkdir(parents=True)
        (root / "packages" / "demo" / "Dockerfile").write_text(
            "FROM node:20 AS builder\n"
            "COPY packages/demo packages/demo\n"
            "FROM node:20\n"
            "COPY --from=builder /app/packages/demo/dist packages/demo/dist\n"
        )
        self.vecta_root = root

    def _fake_client(self, files: list[str]) -> type:
        tip, ancestor = self.SHA_TIP, self.SHA_ANCESTOR

        class FakeClient:
            def __init__(self, *, api_url: str, token: str) -> None:
                pass

            def get_json_list(self, path: str, query: dict[str, str]):
                assert path.endswith("/commits")
                assert query["sha"] == tip
                return [{"sha": tip}, {"sha": ancestor}]

            def get_json(self, path: str, query: dict[str, str]):
                if path.endswith("/actions/runs"):
                    if query["head_sha"] == ancestor:
                        return {"workflow_runs": [run(sha=ancestor)]}
                    return {"workflow_runs": []}
                if path.endswith("/jobs"):
                    return {"jobs": [job()]}
                if "/compare/" in path:
                    return {
                        "status": "ahead",
                        "files": [{"filename": f} for f in files],
                    }
                raise AssertionError(f"unexpected path {path}")

        return FakeClient

    def _verify(self, files: list[str]) -> MODULE.EvidenceResult:
        original = MODULE.GitHubClient
        MODULE.GitHubClient = self._fake_client(files)
        try:
            return MODULE.verify(
                repo="ZenoWangzy/vecta",
                sha=self.SHA_TIP,
                branch="main",
                token="t",
                api_url="https://api.github.com",
                vecta_root=self.vecta_root,
            )
        finally:
            MODULE.GitHubClient = original

    def test_accepts_ancestor_evidence_when_diff_is_docs_only(self) -> None:
        result = self._verify(["README.md", "docs/notes.md"])
        self.assertEqual(result, MODULE.EvidenceResult(10, 20, self.SHA_ANCESTOR))

    def test_rejects_ancestor_evidence_when_diff_touches_an_image_path(self) -> None:
        with self.assertRaises(MODULE.EvidenceError):
            self._verify(["packages/demo/src/index.ts"])


if __name__ == "__main__":
    unittest.main()
