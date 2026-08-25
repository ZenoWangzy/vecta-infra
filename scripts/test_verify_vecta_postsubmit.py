#!/usr/bin/env python3
"""Regression tests for the exact-SHA VectA Postsubmit evidence gate."""

from __future__ import annotations

import importlib.util
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


if __name__ == "__main__":
    unittest.main()
