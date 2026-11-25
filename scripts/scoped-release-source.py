"""Reconstruct the approved product change on its recorded production baseline."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def prepare(repo, spec, baseline):
    def git(*args, data=None, env=None):
        return subprocess.run(["git", "-C", str(repo), *args], input=data,
                              stdout=subprocess.PIPE, env=env, check=True).stdout

    base = spec["baseline"]
    change = spec["merged_revision"]
    tree = spec["expected_tree"]
    if not all(re.fullmatch("[0-9a-f]{40}", s) for s in (base, change, tree)):
        raise ValueError("expected full Git object IDs")
    if base != baseline:
        raise ValueError("production baseline has changed")
    git("merge-base", "--is-ancestor", base, change)
    git("merge-base", "--is-ancestor", change, "refs/remotes/origin/main")
    changed = git("diff", "--no-renames", "--name-only", "-z", change + "^", change)
    paths = {p.decode() for p in changed.split(b"\0") if p}
    excluded = set(spec["documentation_only"])
    if not excluded.issubset(paths) or any(not (p.startswith("docs/") and p.endswith(".md")) for p in excluded):
        raise ValueError("only named, changed documentation files can be excluded")
    included = sorted(paths - excluded)
    if not included or any(not p.startswith("packages/") for p in included):
        raise ValueError("unexpected product change outside packages")
    patch = git("diff", "--no-renames", "--binary", "--full-index", change + "^", change, "--", *included)
    with tempfile.TemporaryDirectory() as temp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(temp) / "index"))
        git("read-tree", base, env=env)
        git("apply", "--cached", "--3way", "--whitespace=nowarn", env=env, data=patch)
        actual = git("write-tree", env=env).decode().strip()
    if actual != tree:
        raise ValueError(f"reconstructed tree {actual} differs from manifest {tree}")
    stamp = git("show", "-s", "--format=%ct", change).decode().strip() + " +0000"
    identity = dict(os.environ)
    for role in ("AUTHOR", "COMMITTER"):
        identity.update({f"GIT_{role}_NAME": "VectA Release", f"GIT_{role}_EMAIL": "release@users.noreply.github.com",
                         f"GIT_{role}_DATE": stamp})
    msg = f"Backport VectA #{spec['pull_request']} to production {base}\n\nSource: {change}\n"
    return git("-c", "commit.gpgSign=false", "commit-tree", actual, "-p", base, env=identity, data=msg.encode()).decode().strip()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--production-baseline", required=True)
    args = parser.parse_args()
    print(prepare(args.repo, json.loads(args.manifest.read_text()), args.production_baseline))
