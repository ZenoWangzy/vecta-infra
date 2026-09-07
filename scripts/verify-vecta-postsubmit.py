#!/usr/bin/env python3
"""Require a successful exact-SHA VectA Postsubmit job before image builds.

Ticket 141: a non-code commit (typically `[skip ci]`) at main's tip never
gets its own Postsubmit run, which used to deadlock every promotion once it
landed at HEAD -- the tip has no evidence and never will, and the commit
that does have evidence isn't the tip. `verify()` now falls back to the
newest ancestor commit that does carry exact-SHA evidence, but only when
the diff between that ancestor and the requested sha touches no path a
production Dockerfile's *final* build stage would copy into an image. That
path->image mapping (`load_prefix_table`) is re-derived from
scripts/production-image-contract.json and its Dockerfiles on every call,
never a hand-copied table that can silently go stale. It's a lean port of
the same idea in vecta's
.scratch/fruit-v41-daily-reconciliation/merge-artefacts.sh prefix_table()
-- that script also does far more (production revision probing over SSH,
etc.) which is irrelevant here, so this keeps only the COPY-parsing core.
Unlike that script, it does not exempt test files inside a /src prefix:
this gate only needs a strict yes/no on "could the image differ", and
erring toward "yes" costs nothing worse than an avoidable exact-SHA wait.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, NamedTuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
POSTSUBMIT_JOB_NAME = "Postsubmit validate"
IMAGE_CONTRACT_PATH = "scripts/production-image-contract.json"
# ponytail: one page of ancestor history covers every deadlock actually
# seen (the tip is one or two non-code commits past real evidence).
# Paginate further only if a real gap is ever observed deeper than this.
MAX_ANCESTOR_LOOKBACK = 100
# GitHub's compare API caps the files list; past that we can't prove the
# diff is image-safe, so treat it as image-affecting rather than guess.
MAX_COMPARABLE_FILES = 300


class EvidenceError(RuntimeError):
    """The requested release evidence is missing or cannot be verified."""


class EvidenceResult(NamedTuple):
    run_id: int
    job_id: int
    sha: str  # the commit that actually carries the Postsubmit evidence


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


def _last_stage_copy_prefixes(dockerfile_text: str) -> list[str]:
    """Path prefixes the Dockerfile's *last* build stage COPYs into the image.

    `FROM` resets the accumulator, so only the final stage's COPY lines
    survive. `--from=<stage> /app/X ...` strips the `/app/` prefix and, for
    a `/dist` destination, swaps it for `/src` (the builder compiles
    src -> dist, so a src change is what actually varies the image). A
    bare COPY (no `--from`) keeps its source path as-is. `--from=<stage>`
    paths outside `/app/` come from a base image, not this repo, and are
    skipped.
    """
    prefixes: list[str] = []
    for line in dockerfile_text.splitlines():
        if re.match(r"^\s*FROM\s+", line, re.IGNORECASE):
            prefixes = []
            continue
        match = re.match(r"^\s*COPY\s+(.*)$", line)
        if not match:
            continue
        from_stage: str | None = None
        args: list[str] = []
        for token in match.group(1).split():
            if token.startswith("--from="):
                from_stage = token[len("--from=") :]
            elif token.startswith("--"):
                continue
            else:
                args.append(token)
        if len(args) < 2:
            continue
        for source in args[:-1]:  # the last token is the destination
            prefix = source
            if from_stage is not None:
                if not prefix.startswith("/app/"):
                    continue
                prefix = prefix[len("/app/") :]
                if prefix.endswith("/dist"):
                    prefix = prefix[: -len("/dist")] + "/src"
            prefix = prefix.rstrip("/")
            if prefix:
                prefixes.append(prefix)
    return prefixes


def load_prefix_table(vecta_root: Path) -> tuple[list[tuple[str, str]], set[str]]:
    """Derive (image_name, path_prefix) pairs and the build recipe's own files.

    Re-parsed from the contract and its Dockerfiles on every call -- never
    a hand-copied table that can go stale.
    """
    contract_file = vecta_root / IMAGE_CONTRACT_PATH
    contract = json.loads(contract_file.read_text())
    recipe_paths = {IMAGE_CONTRACT_PATH}
    prefixes: list[tuple[str, str]] = []
    for image in contract["images"]:
        dockerfile_rel = image["dockerfile"]
        recipe_paths.add(dockerfile_rel)
        text = (vecta_root / dockerfile_rel).read_text()
        prefixes.extend(
            (image["name"], prefix) for prefix in _last_stage_copy_prefixes(text)
        )
    return prefixes, recipe_paths


def diff_affects_image(
    paths: Iterable[str],
    prefixes: list[tuple[str, str]],
    recipe_paths: set[str],
) -> list[str]:
    """Paths (in input order) that would change a built image's content.

    A path affects an image if some Dockerfile's final stage COPYs it in
    (exact prefix match or inside a prefix directory), or if it's the
    build recipe itself (the contract or one of its Dockerfiles) -- a
    changed recipe invalidates any classification derived from it.
    """
    hits = []
    for path in paths:
        if path in recipe_paths or any(
            path == prefix or path.startswith(prefix + "/") for _, prefix in prefixes
        ):
            hits.append(path)
    return hits


def select_evidenced_ancestor(
    ancestor_shas: list[str],
    evidence_for_sha: Callable[[str], tuple[int, int] | None],
) -> tuple[str, int, int] | None:
    """First (newest-first) ancestor with exact-SHA evidence, or None."""
    for candidate in ancestor_shas:
        evidence = evidence_for_sha(candidate)
        if evidence is not None:
            return candidate, evidence[0], evidence[1]
    return None


class GitHubClient:
    def __init__(self, *, api_url: str, token: str) -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token

    def _request(self, path: str, query: dict[str, str]) -> Any:
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
                return json.load(response)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            raise EvidenceError("GitHub evidence API request failed") from error

    def get_json(self, path: str, query: dict[str, str]) -> dict[str, Any]:
        payload = self._request(path, query)
        if not isinstance(payload, dict):
            raise EvidenceError("GitHub evidence API returned an invalid payload")
        return payload

    def get_json_list(self, path: str, query: dict[str, str]) -> list[Any]:
        payload = self._request(path, query)
        if not isinstance(payload, list):
            raise EvidenceError("GitHub evidence API returned an invalid payload")
        return payload


def verify(
    *,
    repo: str,
    sha: str,
    branch: str,
    token: str,
    api_url: str,
    vecta_root: Path | None = None,
) -> EvidenceResult:
    if not REPO_RE.fullmatch(repo):
        raise EvidenceError("repo must be owner/name")
    if not SHA_RE.fullmatch(sha):
        raise EvidenceError("sha must be a full lowercase Git SHA")
    if branch != "main":
        raise EvidenceError("branch must be main")
    if not token:
        raise EvidenceError("VECTA_READ_TOKEN is required")

    client = GitHubClient(api_url=api_url, token=token)

    def runs_for(candidate_sha: str) -> list[dict[str, Any]]:
        # head_sha filters server-side to the runs for this exact commit. Without
        # it GitHub returns the last 100 completed push runs in full — about 1.2 MB
        # — to find the one or two that matter, and on a slow link that response
        # arrives truncated (http.client.IncompleteRead), failing the gate for a
        # commit whose Postsubmit is green. find_evidence still re-checks head_sha
        # and head_branch, so this narrows the candidate set and never widens it.
        payload = client.get_json(
            f"/repos/{repo}/actions/runs",
            {
                "branch": branch,
                "event": "push",
                "status": "completed",
                "head_sha": candidate_sha,
                "per_page": "100",
            },
        )
        runs = payload.get("workflow_runs")
        if not isinstance(runs, list):
            raise EvidenceError("GitHub evidence API omitted workflow_runs")
        return runs

    def jobs_for_run(run_id: int) -> list[dict[str, Any]]:
        payload = client.get_json(
            f"/repos/{repo}/actions/runs/{run_id}/jobs",
            {"filter": "latest", "per_page": "100"},
        )
        jobs = payload.get("jobs")
        if not isinstance(jobs, list):
            raise EvidenceError("GitHub evidence API omitted jobs")
        return jobs

    exact = find_evidence(runs_for(sha), jobs_for_run, sha=sha, branch=branch)
    if exact is not None:
        return EvidenceResult(exact[0], exact[1], sha)

    if vecta_root is None:
        raise EvidenceError(
            f"no successful exact-SHA {POSTSUBMIT_JOB_NAME!r} job exists"
        )

    ancestors_payload = client.get_json_list(
        f"/repos/{repo}/commits",
        {"sha": sha, "per_page": str(MAX_ANCESTOR_LOOKBACK)},
    )
    ancestor_shas = [
        commit["sha"]
        for commit in ancestors_payload
        if isinstance(commit, dict) and isinstance(commit.get("sha"), str)
    ]
    if ancestor_shas and ancestor_shas[0] == sha:
        ancestor_shas = ancestor_shas[1:]  # sha itself, already checked above

    selected = select_evidenced_ancestor(
        ancestor_shas,
        lambda candidate: find_evidence(
            runs_for(candidate), jobs_for_run, sha=candidate, branch=branch
        ),
    )
    if selected is None:
        raise EvidenceError(
            f"no successful exact-SHA {POSTSUBMIT_JOB_NAME!r} job exists "
            f"(checked {sha} and {len(ancestor_shas)} ancestor commits)"
        )
    ancestor_sha, run_id, job_id = selected

    compare = client.get_json(f"/repos/{repo}/compare/{ancestor_sha}...{sha}", {})
    if compare.get("status") != "ahead":
        raise EvidenceError(
            f"ancestor {ancestor_sha} is not a strict ancestor of {sha} "
            f"(compare status={compare.get('status')!r})"
        )
    files = compare.get("files")
    if not isinstance(files, list):
        raise EvidenceError(
            f"GitHub compare API omitted files for {ancestor_sha}...{sha}"
        )
    if len(files) >= MAX_COMPARABLE_FILES:
        raise EvidenceError(
            f"{ancestor_sha}...{sha} touches {len(files)} files, too many to "
            "prove none are image-affecting"
        )
    paths = [
        entry["filename"]
        for entry in files
        if isinstance(entry, dict) and isinstance(entry.get("filename"), str)
    ]
    if len(paths) != len(files):
        raise EvidenceError(
            f"GitHub compare API returned a malformed file entry for "
            f"{ancestor_sha}...{sha}"
        )

    prefixes, recipe_paths = load_prefix_table(vecta_root)
    offending = diff_affects_image(paths, prefixes, recipe_paths)
    if offending:
        raise EvidenceError(
            f"ancestor {ancestor_sha} has postsubmit evidence but "
            f"{sha} changes image-affecting path(s) not covered by it: "
            + ", ".join(offending[:10])
            + (" ..." if len(offending) > 10 else "")
        )

    return EvidenceResult(run_id, job_id, ancestor_sha)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument(
        "--vecta-checkout",
        default=None,
        help=(
            "Local VectA checkout at --sha, used to fall back to the newest "
            "evidenced ancestor when --sha itself (e.g. a [skip ci] commit) "
            "has none. Omit to require exact-SHA evidence only."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = verify(
            repo=args.repo,
            sha=args.sha,
            branch=args.branch,
            token=os.environ.get("VECTA_READ_TOKEN", ""),
            api_url=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
            vecta_root=Path(args.vecta_checkout) if args.vecta_checkout else None,
        )
    except EvidenceError as error:
        print(f"postsubmit evidence rejected: {error}", file=sys.stderr)
        return 1
    if result.sha != args.sha:
        print(
            f"postsubmit evidence verified via ancestor {result.sha} "
            f"(no image-affecting changes between it and {args.sha}): "
            f"run_id={result.run_id} job_id={result.job_id}"
        )
    else:
        print(
            f"postsubmit evidence verified: run_id={result.run_id} "
            f"job_id={result.job_id}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
