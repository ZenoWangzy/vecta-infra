#!/usr/bin/env python3
"""Validate the only image-reference format accepted by mypc release playbooks."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
MIGRATION_PATH_RE = re.compile(
    r"^packages/fruit-industry-pack/migrations/[0-9]{4}_[a-z0-9_]+\.sql$"
)
ROLLBACK_PATH_RE = re.compile(
    r"^packages/fruit-industry-pack/migrations/rollback/[0-9]{4}_[a-z0-9_]+\.sql$"
)
APPROVED_NEXUS_REGISTRY = "127.0.0.1:8082"
JOURNAL_OWNER = "packages/fruit-industry-pack/migrations/meta/_journal.json"
APPROVED_IMAGE_NAMES = frozenset(
    {
        "fleet-gateway",
        "channel-gateway",
        "a2a-router",
        "rag-service",
        "directory-service",
        "baidu-search-service",
        "admin-console",
        "wechat-contact-sync",
        "fruit-industry-pack",
        "vecta-migrator",
        "employee-runtime",
    }
)
REQUIRED_DEPLOY_SERVICE_NAMES = frozenset(
    {
        "a2a-router",
        "directory-service",
        "admin-console",
        "fleet-gateway",
        "rag-service",
        "channel-gateway",
        "wechat-contact-sync",
    }
)


class ManifestError(ValueError):
    pass


def fail(message: str) -> None:
    raise ManifestError(message)


def validate_history_provenance(
    document: dict[str, object], *, required: bool = False
) -> None:
    provenance = document.get("history_provenance")
    if not isinstance(provenance, dict):
        if required:
            fail("history_provenance is required for a history release")
        return

    expected_paths = {
        "prerequisite_0029": "packages/fruit-industry-pack/migrations/0029_v4_container_lot_expand.sql",
        "schema_0030": "packages/fruit-industry-pack/migrations/0030_v4_historical_batch_control.sql",
    }
    expected_tags = {
        "prerequisite_0029": "0029_v4_container_lot_expand",
        "schema_0030": "0030_v4_historical_batch_control",
    }
    journal_indices: dict[str, int] = {}
    for key, expected_path in expected_paths.items():
        entry = provenance.get(key)
        if not isinstance(entry, dict) or entry.get("path") != expected_path:
            fail(f"history_provenance.{key} must bind the canonical migration path")
        if not isinstance(entry.get("sha256"), str) or not re.fullmatch(
            r"[0-9a-f]{64}", entry["sha256"]
        ):
            fail(f"history_provenance.{key}.sha256 must be a lowercase SHA-256")
        if entry.get("owner") != JOURNAL_OWNER:
            fail(f"history_provenance.{key}.owner must identify the migration journal")
        if entry.get("tag") != expected_tags[key]:
            fail(f"history_provenance.{key}.tag must match its canonical migration")
        journal_index = entry.get("journal_index")
        if type(journal_index) is not int or journal_index < 0:
            fail(f"history_provenance.{key}.journal_index must be a non-negative integer")
        if Path(expected_path).name != f"{entry['tag']}.sql":
            fail(f"history_provenance.{key}.tag does not match its path")
        journal_indices[key] = journal_index

    loss_entry = provenance.get("loss_constraint")
    rollback_entry = provenance.get("loss_rollback")
    if not isinstance(loss_entry, dict) or not isinstance(rollback_entry, dict):
        fail("history_provenance must bind the loss migration and paired rollback")
    loss_path = loss_entry.get("path")
    rollback_path = rollback_entry.get("path")
    if not isinstance(loss_path, str) or MIGRATION_PATH_RE.fullmatch(loss_path) is None:
        fail("history_provenance.loss_constraint.path must be a canonical migration path")
    if not isinstance(rollback_path, str) or ROLLBACK_PATH_RE.fullmatch(rollback_path) is None:
        fail("history_provenance.loss_rollback.path must be a canonical rollback path")
    for key, entry, path in (
        ("loss_constraint", loss_entry, loss_path),
        ("loss_rollback", rollback_entry, rollback_path),
    ):
        if entry.get("owner") != JOURNAL_OWNER:
            fail(f"history_provenance.{key}.owner must identify the migration journal")
        tag = entry.get("tag")
        if not isinstance(tag, str) or not re.fullmatch(r"[0-9]{4}_[a-z0-9_]+", tag):
            fail(f"history_provenance.{key}.tag must be a migration journal tag")
        if Path(path).name != f"{tag}.sql":
            fail(f"history_provenance.{key}.tag does not match its path")
        journal_index = entry.get("journal_index")
        if type(journal_index) is not int or journal_index < 0:
            fail(f"history_provenance.{key}.journal_index must be a non-negative integer")
        journal_indices[key] = journal_index
    if loss_entry["tag"] != rollback_entry["tag"] or loss_entry["journal_index"] != rollback_entry["journal_index"]:
        fail("history_provenance.loss migration and rollback must share tag and journal index")
    loss_number = int(Path(loss_path).name.split("_", 1)[0])
    if loss_number < 30:
        fail("loss constraint migration must be 0030 or a later migration")
    if not journal_indices["prerequisite_0029"] < journal_indices["schema_0030"] <= journal_indices["loss_constraint"]:
        fail("history provenance journal indices must preserve 0029 -> 0030 -> loss order")
    if rollback_path != loss_path.replace("/migrations/", "/migrations/rollback/"):
        fail("loss rollback must be paired with the selected loss migration")
    for key, entry in (("loss_constraint", loss_entry), ("loss_rollback", rollback_entry)):
        if not isinstance(entry.get("sha256"), str) or not re.fullmatch(
            r"[0-9a-f]{64}", entry["sha256"]
        ):
            fail(f"history_provenance.{key}.sha256 must be a lowercase SHA-256")


def validate(
    path: Path,
    *,
    require_full_deploy_set: bool = False,
    require_history_provenance: bool = False,
) -> dict[str, object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"digest manifest is not readable JSON: {error}")
    if not isinstance(document, dict):
        fail("digest manifest root must be an object")
    if document.get("schema_version") != "mypc-release-digest-v1":
        fail("unsupported digest manifest schema")
    source_sha = document.get("source_sha")
    if not isinstance(source_sha, str) or not SHA_RE.fullmatch(source_sha):
        fail("source_sha must be a full lowercase 40-character SHA")
    if document.get("source_branch") != "main":
        fail("source_branch must be main")
    registry = document.get("registry")
    if registry != APPROVED_NEXUS_REGISTRY:
        fail(f"registry must be the approved Nexus registry {APPROVED_NEXUS_REGISTRY}")
    validate_history_provenance(document, required=require_history_provenance)

    images = document.get("images")
    refs = document.get("deploy_image_refs")
    if not isinstance(images, list) or not images:
        fail("images must be a non-empty list")
    if not isinstance(refs, dict) or not refs:
        fail("deploy_image_refs must be a non-empty object")

    names: set[str] = set()
    for image in images:
        if not isinstance(image, dict):
            fail("each image entry must be an object")
        name = image.get("name")
        source_ref = image.get("source_ref")
        digest = image.get("digest")
        deploy_ref = image.get("deploy_ref")
        oci_revision = image.get("oci_revision")
        if not isinstance(name, str) or not name or name in names:
            fail("image names must be non-empty and unique")
        if name not in APPROVED_IMAGE_NAMES:
            fail(f"{name} is not an approved production image")
        names.add(name)
        expected_source_ref = f"{APPROVED_NEXUS_REGISTRY}/{name}:{source_sha}"
        if source_ref != expected_source_ref:
            fail(f"{name} source_ref must use Nexus and bind to source_sha")
        if oci_revision != source_sha:
            fail(f"{name} OCI revision must bind the digest to source_sha")
        if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
            fail(f"{name} digest must be a sha256 digest")
        expected_deploy_ref = f"{APPROVED_NEXUS_REGISTRY}/{name}@{digest}"
        if deploy_ref != expected_deploy_ref:
            fail(f"{name} deploy_ref does not bind source_ref to its digest")
        if refs.get(name) != deploy_ref:
            fail(f"deploy_image_refs does not match {name}")

    if set(refs) != names:
        fail("deploy_image_refs and images must contain the same names")
    if any(not isinstance(value, str) or "@sha256:" not in value for value in refs.values()):
        fail("every deployment reference must be immutable")
    if require_full_deploy_set and not REQUIRED_DEPLOY_SERVICE_NAMES <= names:
        missing = sorted(REQUIRED_DEPLOY_SERVICE_NAMES - names)
        fail(f"full deploy service set is incomplete: {', '.join(missing)}")
    return document


def self_check() -> int:
    source_sha = "a" * 40
    digest = "sha256:" + "b" * 64
    images = [
        {
            "name": "a2a-router",
            "source_ref": f"{APPROVED_NEXUS_REGISTRY}/a2a-router:{source_sha}",
            "digest": digest,
            "oci_revision": source_sha,
            "deploy_ref": f"{APPROVED_NEXUS_REGISTRY}/a2a-router@{digest}",
        }
    ]
    document = {
        "schema_version": "mypc-release-digest-v1",
        "source_sha": source_sha,
        "source_branch": "main",
        "registry": APPROVED_NEXUS_REGISTRY,
        "history_provenance": {
            "prerequisite_0029": {
                "path": "packages/fruit-industry-pack/migrations/0029_v4_container_lot_expand.sql",
                "sha256": "c" * 64,
                "owner": JOURNAL_OWNER,
                "tag": "0029_v4_container_lot_expand",
                "journal_index": 1,
            },
            "schema_0030": {
                "path": "packages/fruit-industry-pack/migrations/0030_v4_historical_batch_control.sql",
                "sha256": "d" * 64,
                "owner": JOURNAL_OWNER,
                "tag": "0030_v4_historical_batch_control",
                "journal_index": 2,
            },
            "loss_constraint": {
                "path": "packages/fruit-industry-pack/migrations/0030_v4_historical_batch_control.sql",
                "sha256": "d" * 64,
                "owner": JOURNAL_OWNER,
                "tag": "0030_v4_historical_batch_control",
                "journal_index": 2,
            },
            "loss_rollback": {
                "path": "packages/fruit-industry-pack/migrations/rollback/0030_v4_historical_batch_control.sql",
                "sha256": "e" * 64,
                "owner": JOURNAL_OWNER,
                "tag": "0030_v4_historical_batch_control",
                "journal_index": 2,
            },
        },
        "images": images,
        "deploy_image_refs": {"a2a-router": images[0]["deploy_ref"]},
    }
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "manifest.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        validate(path)
        document_without_history = dict(document)
        document_without_history.pop("history_provenance")
        path.write_text(json.dumps(document_without_history), encoding="utf-8")
        validate(path)
        try:
            validate(path, require_history_provenance=True)
        except ManifestError:
            pass
        else:
            fail("history release accepted a manifest without SQL provenance")
        path.write_text(json.dumps(document), encoding="utf-8")
        try:
            validate(path, require_full_deploy_set=True)
        except ManifestError:
            pass
        else:
            fail("incomplete full-deploy fixture was accepted")
        tampered = json.loads(json.dumps(document))
        tampered["history_provenance"]["loss_rollback"]["path"] = (
            "packages/fruit-industry-pack/migrations/rollback/0999_unowned.sql"
        )
        path.write_text(json.dumps(tampered), encoding="utf-8")
        try:
            validate(path, require_history_provenance=True)
        except ManifestError:
            pass
        else:
            fail("unowned rollback SQL fixture was accepted")
        document["registry"] = "registry.example.invalid"
        path.write_text(json.dumps(document), encoding="utf-8")
        try:
            validate(path)
        except ManifestError:
            pass
        else:
            fail("non-Nexus registry fixture was accepted")
    print("mypc digest manifest self-check passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path, nargs="?")
    parser.add_argument("--require-full-deploy-set", action="store_true")
    parser.add_argument("--require-history-provenance", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_check()
    if args.manifest is None:
        parser.error("manifest is required unless --self-test is used")
    try:
        document = validate(
            args.manifest,
            require_full_deploy_set=args.require_full_deploy_set,
            require_history_provenance=args.require_history_provenance,
        )
    except ManifestError as error:
        print(f"digest manifest rejected: {error}", file=sys.stderr)
        return 1
    print(
        "validated immutable manifest: "
        f"source_sha={document['source_sha']} images={len(document['images'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
