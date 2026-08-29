#!/usr/bin/env python3
"""Require a successful exact-SHA VectA Postsubmit job before image builds."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
POSTSUBMIT_JOB_NAME = "Postsubmit validate"


class EvidenceError(RuntimeError):
    """The requested release evidence is missing or cannot be verified."""


def find_evidence(
    runs: list[dict[str, Any]],
    jobs_for_run: Callable[[int], list[dict[str, Any]]],
    *,
    sha: str,
    branch: str,
) -> tuple[int, int] | None:
    """Return the first exact successful run/job pair, newest run first."""

    candidates = sorted(
        (
            run
            for run in runs
            if run.get("head_sha") == sha
            and run.get("head_branch") == branch
            and run.get("event") == "push"
            and run.get("status") == "completed"
        ),
        key=lambda run: str(run.get("created_at", "")),
        reverse=True,
    )
    for run in candidates:
        run_id = run.get("id")
        if not isinstance(run_id, int):
            continue
        for job in jobs_for_run(run_id):
            if (
                job.get("name") == POSTSUBMIT_JOB_NAME
                and job.get("status") == "completed"
                and job.get("conclusion") == "success"
                and isinstance(job.get("id"), int)
            ):
                return run_id, job["id"]
    return None


class GitHubClient:
    def __init__(self, *, api_url: str, token: str) -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token

    def get_json(self, path: str, query: dict[str, str]) -> dict[str, Any]:
        url = f"{self.api_url}{path}?{urlencode(query)}"
        request = Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "vecta-infra-postsubmit-gate",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.load(response)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            raise EvidenceError("GitHub evidence API request failed") from error
        if not isinstance(payload, dict):
            raise EvidenceError("GitHub evidence API returned an invalid payload")
        return payload


def verify(*, repo: str, sha: str, branch: str, token: str, api_url: str) -> tuple[int, int]:
    if not REPO_RE.fullmatch(repo):
        raise EvidenceError("repo must be owner/name")
    if not SHA_RE.fullmatch(sha):
        raise EvidenceError("sha must be a full lowercase Git SHA")
    if branch != "main":
        raise EvidenceError("branch must be main")
    if not token:
        raise EvidenceError("VECTA_READ_TOKEN is required")

    client = GitHubClient(api_url=api_url, token=token)
    # head_sha filters server-side to the runs for this exact commit. Without
    # it GitHub returns the last 100 completed push runs in full — about 1.2 MB
    # — to find the one or two that matter, and on a slow link that response
    # arrives truncated (http.client.IncompleteRead), failing the gate for a
    # commit whose Postsubmit is green. find_evidence still re-checks head_sha
    # and head_branch, so this narrows the candidate set and never widens it.
    runs_payload = client.get_json(
        f"/repos/{repo}/actions/runs",
        {
            "branch": branch,
            "event": "push",
            "status": "completed",
            "head_sha": sha,
            "per_page": "100",
        },
    )
    runs = runs_payload.get("workflow_runs")
    if not isinstance(runs, list):
        raise EvidenceError("GitHub evidence API omitted workflow_runs")

    def jobs_for_run(run_id: int) -> list[dict[str, Any]]:
        payload = client.get_json(
            f"/repos/{repo}/actions/runs/{run_id}/jobs",
            {"filter": "latest", "per_page": "100"},
        )
        jobs = payload.get("jobs")
        if not isinstance(jobs, list):
            raise EvidenceError("GitHub evidence API omitted jobs")
        return jobs

    evidence = find_evidence(runs, jobs_for_run, sha=sha, branch=branch)
    if evidence is None:
        raise EvidenceError(
            f"no successful exact-SHA {POSTSUBMIT_JOB_NAME!r} job exists"
        )
    return evidence


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--branch", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        run_id, job_id = verify(
            repo=args.repo,
            sha=args.sha,
            branch=args.branch,
            token=os.environ.get("VECTA_READ_TOKEN", ""),
            api_url=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
        )
    except EvidenceError as error:
        print(f"postsubmit evidence rejected: {error}", file=sys.stderr)
        return 1
    print(f"postsubmit evidence verified: run_id={run_id} job_id={job_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
