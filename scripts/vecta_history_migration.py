#!/usr/bin/env python3
"""Select and validate the journal-owned Fruit history migration contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path


MIGRATION_NAME_RE = re.compile(r"^(?P<number>[0-9]{4})_[a-z0-9_]+\.sql$")
JOURNAL_OWNER = "packages/fruit-industry-pack/migrations/meta/_journal.json"
SQL_LINE_COMMENT = re.compile(r"--[^\n]*")
SQL_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
STATEMENT_BREAKPOINT = re.compile(r"(?im)^\s*-->\s*statement-breakpoint\s*$")
CONSTRAINT_NAME = r'"?v4_documents_type_chk"?'


class HistoryMigrationError(ValueError):
    """A selected source cannot prove the production history contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise HistoryMigrationError(message)


def sql_without_comments(text: str) -> str:
    return SQL_LINE_COMMENT.sub("", SQL_BLOCK_COMMENT.sub("", text))


def sql_segments(text: str) -> list[str]:
    clean = sql_without_comments(text)
    return [segment.strip() for segment in STATEMENT_BREAKPOINT.split(clean) if segment.strip()]


def _constraint_update(
    text: str, *, require_tail: bool = True
) -> dict[str, object] | None:
    """Return one executable DROP/ADD update, or None when unrelated."""
    clean = sql_without_comments(text)
    drops = list(
        re.finditer(
            rf"\bDROP\s+CONSTRAINT(?:\s+IF\s+EXISTS)?\s+{CONSTRAINT_NAME}",
            clean,
            re.IGNORECASE,
        )
    )
    adds = list(
        re.finditer(
            rf"\bADD\s+CONSTRAINT\s+{CONSTRAINT_NAME}\s+CHECK\s*\(",
            clean,
            re.IGNORECASE,
        )
    )
    if not drops and not adds:
        return None
    require(len(drops) == 1 and len(adds) == 1, "v4_documents_type_chk must have one DROP and one ADD")
    drop, add = drops[0], adds[0]
    require(drop.start() < add.start(), "v4_documents_type_chk DROP must precede ADD")
    end = clean.find(";", add.start())
    require(end >= 0, "v4_documents_type_chk ADD must be terminated")
    if require_tail:
        require(not clean[end + 1 :].strip(), "v4_documents_type_chk update must be the final executable SQL")
        segments = sql_segments(text)
        require(segments and "ADD CONSTRAINT" in segments[-1].upper(), "v4_documents_type_chk ADD must be the final migration segment")
    add_statement = clean[add.start() : end]
    return {
        "clean": clean,
        "drop": drop,
        "add": add,
        "add_statement": add_statement,
        "has_loss": re.search(r"""['"]loss['"]""", add_statement, re.IGNORECASE) is not None,
    }


def has_loss_document_constraint(text: str) -> bool:
    try:
        update = _constraint_update(text)
    except HistoryMigrationError:
        return False
    return bool(update and update["has_loss"])


def has_tail_loss_constraint(text: str) -> bool:
    return has_loss_document_constraint(text)


def has_transactional_loss_rollback(text: str) -> bool:
    """Require the self-contained, ordered rollback transaction."""
    clean = sql_without_comments(text)
    begin = re.search(r"\bBEGIN\s*;", clean, re.IGNORECASE)
    set_local = re.search(
        r"\bSET\s+LOCAL\s+lock_timeout\s*=\s*['\"]5s['\"]\s*;",
        clean,
        re.IGNORECASE,
    )
    documents_lock = re.search(
        r'\bLOCK\s+TABLE\s+"?fruit"?\s*\.\s*"?v4_documents"?\s+IN\s+ACCESS\s+EXCLUSIVE\s+MODE\s*;',
        clean,
        re.IGNORECASE,
    )
    batches_lock = re.search(
        r'\bLOCK\s+TABLE\s+"?fruit"?\s*\.\s*"?v4_historical_import_batches"?\s+IN\s+ACCESS\s+EXCLUSIVE\s+MODE\s*;',
        clean,
        re.IGNORECASE,
    )
    commit = re.search(r"\bCOMMIT\s*;", clean, re.IGNORECASE)
    try:
        update = _constraint_update(clean, require_tail=False)
    except HistoryMigrationError:
        return False
    if None in (begin, set_local, documents_lock, batches_lock, commit, update):
        return False
    assert begin and set_local and documents_lock and batches_lock and commit and update
    if clean[: begin.start()].strip() or clean[commit.end() :].strip():
        return False
    guard_region = clean[batches_lock.end() : update["drop"].start()]
    if re.search(r"\bIF\b", guard_region, re.IGNORECASE) is None:
        return False
    if re.search(r"\bRAISE\s+EXCEPTION\b", guard_region, re.IGNORECASE) is None:
        return False
    if "v4_documents" not in guard_region or "v4_historical_import_batches" not in guard_region:
        return False
    ordered = (
        begin.start()
        < set_local.start()
        < documents_lock.start()
        < batches_lock.start()
        < update["drop"].start()
        < update["add"].start()
        < commit.start()
    )
    return ordered and not update["has_loss"]


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def select_history_provenance(root: Path) -> dict[str, object]:
    root = root.resolve()
    migration_root = root / "packages/fruit-industry-pack/migrations"
    journal_path = migration_root / "meta/_journal.json"
    require(migration_root.is_dir(), "Fruit migration directory is missing")
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HistoryMigrationError(f"migration journal is not readable: {error}") from error
    entries = journal.get("entries") if isinstance(journal, dict) else None
    require(isinstance(entries, list), "migration journal entries must be a list")
    tags: list[str] = []
    for entry in entries:
        require(isinstance(entry, dict), "migration journal entries must be objects")
        tag = entry.get("tag")
        require(
            isinstance(tag, str) and MIGRATION_NAME_RE.fullmatch(f"{tag}.sql") is not None,
            "migration journal tags must be non-empty canonical migration tags",
        )
        tags.append(tag)
    require(len(tags) == len(set(tags)), "migration journal tags must be unique")
    required_0029 = "0029_v4_container_lot_expand"
    required_0030 = "0030_v4_historical_batch_control"
    require(required_0029 in tags, "migration journal is missing 0029")
    require(required_0030 in tags, "migration journal is missing 0030")
    index_0029 = tags.index(required_0029)
    index_0030 = tags.index(required_0030)
    require(index_0029 < index_0030, "migration journal must apply 0029 before 0030")

    prerequisite = migration_root / f"{required_0029}.sql"
    schema = migration_root / f"{required_0030}.sql"
    require(prerequisite.is_file(), "0029 migration file is missing")
    require(schema.is_file(), "0030 migration file is missing")

    updates: list[tuple[int, str, Path, dict[str, object]]] = []
    for index, tag in enumerate(tags):
        if index < index_0030:
            continue
        require(MIGRATION_NAME_RE.fullmatch(f"{tag}.sql") is not None, f"invalid journal migration tag: {tag}")
        path = migration_root / f"{tag}.sql"
        require(path.name == f"{tag}.sql", f"journal tag does not match migration file: {tag}")
        require(path.is_file(), f"journal migration file is missing: {tag}")
        update = _constraint_update(path.read_text(encoding="utf-8"))
        if update is not None:
            updates.append((index, tag, path, update))

    require(len(updates) == 1, "journal-owned v4_documents_type_chk update must be unique")
    loss_index, loss_tag, loss_path, loss_update = updates[0]
    require(loss_update["has_loss"], "final v4_documents_type_chk vocabulary must include loss")
    rollback_path = migration_root / "rollback" / loss_path.name
    require(rollback_path.is_file(), f"loss rollback is missing: {_relative(root, rollback_path)}")
    require(
        has_transactional_loss_rollback(rollback_path.read_text(encoding="utf-8")),
        "loss rollback must be transactional, ordered, guarded, and loss-free",
    )
    return {
        "prerequisite_0029": {
            "path": _relative(root, prerequisite),
            "sha256": _sha256(prerequisite),
            "owner": JOURNAL_OWNER,
            "tag": required_0029,
            "journal_index": index_0029,
        },
        "schema_0030": {
            "path": _relative(root, schema),
            "sha256": _sha256(schema),
            "owner": JOURNAL_OWNER,
            "tag": required_0030,
            "journal_index": index_0030,
        },
        "loss_constraint": {
            "path": _relative(root, loss_path),
            "sha256": _sha256(loss_path),
            "owner": JOURNAL_OWNER,
            "tag": loss_tag,
            "journal_index": loss_index,
        },
        "loss_rollback": {
            "path": _relative(root, rollback_path),
            "sha256": _sha256(rollback_path),
            "owner": JOURNAL_OWNER,
            "tag": loss_tag,
            "journal_index": loss_index,
        },
    }


def self_check() -> int:
    forward = """
    /* DROP CONSTRAINT v4_documents_type_chk loss */
    ALTER TABLE "fruit"."v4_documents" DROP CONSTRAINT "v4_documents_type_chk";
    --> statement-breakpoint
    ALTER TABLE "fruit"."v4_documents" ADD CONSTRAINT "v4_documents_type_chk"
      CHECK ("fruit"."v4_documents"."doc_type" IN ('receipt', 'loss'));
    """
    require(has_tail_loss_constraint(forward), "positive loss migration fixture was rejected")
    require(
        not has_tail_loss_constraint("/* ALTER TABLE v4_documents ADD CONSTRAINT v4_documents_type_chk CHECK ('loss'); */"),
        "comment-only loss migration fixture was accepted",
    )
    rollback = """
    BEGIN;
    SET LOCAL lock_timeout = '5s';
    LOCK TABLE "fruit"."v4_documents" IN ACCESS EXCLUSIVE MODE;
    LOCK TABLE "fruit"."v4_historical_import_batches" IN ACCESS EXCLUSIVE MODE;
    DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM "fruit"."v4_documents" WHERE doc_type = 'loss')
         OR EXISTS (SELECT 1 FROM "fruit"."v4_historical_import_batches") THEN
        RAISE EXCEPTION 'rollback guard';
      END IF;
    END $$;
    ALTER TABLE "fruit"."v4_documents" DROP CONSTRAINT "v4_documents_type_chk";
    ALTER TABLE "fruit"."v4_documents" ADD CONSTRAINT "v4_documents_type_chk"
      CHECK ("fruit"."v4_documents"."doc_type" IN ('receipt', 'dispatch'));
    COMMIT;
    """
    require(has_transactional_loss_rollback(rollback), "positive rollback fixture was rejected")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        migration_root = root / "packages/fruit-industry-pack/migrations"
        (migration_root / "meta").mkdir(parents=True)
        (migration_root / "rollback").mkdir()
        (migration_root / "0029_v4_container_lot_expand.sql").write_text("SELECT 1;", encoding="utf-8")
        (migration_root / "0030_v4_historical_batch_control.sql").write_text(forward, encoding="utf-8")
        (migration_root / "0031_remove_loss.sql").write_text(
            forward.replace("'loss'", "'receipt'"), encoding="utf-8"
        )
        (migration_root / "rollback/0030_v4_historical_batch_control.sql").write_text(rollback, encoding="utf-8")
        (migration_root / "rollback/0031_remove_loss.sql").write_text(rollback, encoding="utf-8")
        (migration_root / "meta/_journal.json").write_text(
            json.dumps({"entries": [{"tag": tag} for tag in (
                "0029_v4_container_lot_expand",
                "0030_v4_historical_batch_control",
                "0031_remove_loss",
            )]}),
            encoding="utf-8",
        )
        try:
            select_history_provenance(root)
        except HistoryMigrationError:
            pass
        else:
            raise HistoryMigrationError("0030 add followed by 0031 removal was accepted")

        invalid_journals = (
            (
                "non-object",
                [{"tag": "0029_v4_container_lot_expand"}, "not-an-entry", {"tag": "0030_v4_historical_batch_control"}],
            ),
            (
                "empty-tag",
                [{"tag": "0029_v4_container_lot_expand"}, {"tag": ""}, {"tag": "0030_v4_historical_batch_control"}],
            ),
            (
                "duplicate-tag",
                [{"tag": "0029_v4_container_lot_expand"}, {"tag": "0029_v4_container_lot_expand"}, {"tag": "0030_v4_historical_batch_control"}],
            ),
            (
                "tag-file-mismatch",
                [
                    {"tag": "0029_v4_container_lot_expand"},
                    {"tag": "0030_v4_historical_batch_control"},
                    {"tag": "0031_missing_file"},
                ],
            ),
        )
        for label, invalid_entries in invalid_journals:
            (migration_root / "meta/_journal.json").write_text(
                json.dumps({"entries": invalid_entries}), encoding="utf-8"
            )
            try:
                select_history_provenance(root)
            except HistoryMigrationError:
                pass
            else:
                raise HistoryMigrationError(f"invalid {label} journal was accepted")

        base_0030 = 'CREATE TABLE "fruit"."v4_historical_import_batches" (id uuid);'
        loss_owner = forward
        (migration_root / "0030_v4_historical_batch_control.sql").write_text(base_0030, encoding="utf-8")
        (migration_root / "0031_loss_document_type.sql").write_text(loss_owner, encoding="utf-8")
        (migration_root / "rollback/0031_loss_document_type.sql").write_text(rollback, encoding="utf-8")
        (migration_root / "meta/_journal.json").write_text(
            json.dumps({"entries": [{"tag": tag} for tag in (
                "0029_v4_container_lot_expand",
                "0030_v4_historical_batch_control",
                "0031_loss_document_type",
            )]}),
            encoding="utf-8",
        )
        dynamic_provenance = select_history_provenance(root)
        require(
            dynamic_provenance["loss_constraint"]["tag"] == "0031_loss_document_type",
            "0030 base plus 0031 loss owner was not selected dynamically",
        )
    print("VectA history migration selector self-check passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path, nargs="?")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_check()
    if args.root is None:
        parser.error("root is required unless --self-test is used")
    try:
        result = select_history_provenance(args.root)
    except (HistoryMigrationError, OSError, json.JSONDecodeError) as error:
        print(f"VectA history migration selector rejected: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print("VectA history migration selector passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
