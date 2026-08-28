#!/usr/bin/env python3
"""Check the VectA history-migration contract consumed by the release lane."""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path


from vecta_history_migration import (
    HistoryMigrationError,
    has_loss_document_constraint,
    has_tail_loss_constraint,
    has_transactional_loss_rollback,
    select_history_provenance,
    sql_without_comments,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def js_without_comments(text: str) -> str:
    """Strip JavaScript comments without treating comment text as source structure."""
    output: list[str] = []
    state = "normal"
    index = 0
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if state == "normal":
            if char == "/" and next_char == "/":
                state = "line-comment"
                output.extend((" ", " "))
                index += 2
                continue
            if char == "/" and next_char == "*":
                state = "block-comment"
                output.extend((" ", " "))
                index += 2
                continue
            if char in "'\"`":
                state = char
            output.append(char)
            index += 1
            continue
        if state == "line-comment":
            if char == "\n":
                state = "normal"
                output.append(char)
            else:
                output.append(" ")
            index += 1
            continue
        if state == "block-comment":
            if char == "*" and next_char == "/":
                output.extend((" ", " "))
                state = "normal"
                index += 2
            else:
                output.append("\n" if char == "\n" else " ")
                index += 1
            continue
        output.append(char)
        if char == "\\" and index + 1 < len(text):
            output.append(text[index + 1])
            index += 2
            continue
        if char == state:
            state = "normal"
        index += 1
    return "".join(output)


def has_drizzle_migration_transaction(text: str) -> bool:
    """Require executable source structure for one Drizzle migration transaction."""
    clean = js_without_comments(text)
    imports = (
        r"(?m)^\s*import\s*\{\s*drizzle\s*\}\s*from\s*['\"]drizzle-orm/node-postgres['\"]",
        r"(?m)^\s*import\s*\{\s*migrate\s*\}\s*from\s*['\"]drizzle-orm/node-postgres/migrator['\"]",
    )
    if not all(re.search(pattern, clean) for pattern in imports):
        return False
    required = (
        r"export\s+async\s+function\s+migrateFruitDatabase\s*\(\s*pool\b",
        r"const\s+database\s*=\s*drizzle\s*\(\s*pool\s*\)\s*;",
        r"await\s+migrate\s*\(\s*database\s*,\s*\{",
        r"migrationsFolder\s*:\s*fruitMigrationsFolder",
        r"migrationsSchema\s*:\s*['\"]fruit_meta['\"]",
        r"migrationsTable\s*:\s*['\"]__drizzle_migrations['\"]",
        r"try\s*\{[\s\S]*await\s+migrateFruitDatabase\s*\(\s*pool\s*\)\s*;[\s\S]*\}\s*finally\s*\{[\s\S]*await\s+pool\.end\s*\(\s*\)\s*;",
    )
    return all(re.search(pattern, clean) for pattern in required) and re.search(
        r"\b(?:pool|client)\s*\.\s*query\s*\(", clean
    ) is None


def find_production_runbook(root: Path) -> Path:
    """Resolve one canonical active/archive runbook without recursive globbing."""
    tasks_root = root / ".trellis/tasks"
    candidates = sorted(tasks_root.glob("*/production-batch-runbook.md"))
    candidates.extend(sorted(tasks_root.glob("archive/*/*/production-batch-runbook.md")))
    require(len(candidates) == 1, "history consumer must have one canonical production runbook")
    return candidates[0]


def self_check() -> int:
    forward = """
    CREATE TABLE \"fruit\".\"v4_historical_import_batches\" (id uuid);
    --> statement-breakpoint
    ALTER TABLE \"fruit\".\"v4_documents\" DROP CONSTRAINT \"v4_documents_type_chk\";
    --> statement-breakpoint
    ALTER TABLE \"fruit\".\"v4_documents\" ADD CONSTRAINT \"v4_documents_type_chk\"
      CHECK (\"fruit\".\"v4_documents\".\"doc_type\" IN ('receipt', 'loss'));
    """
    require(has_tail_loss_constraint(forward), "positive tail fixture was rejected")
    require(
        not has_tail_loss_constraint(f"{forward}\n--> statement-breakpoint\nSELECT 1;"),
        "non-tail fixture was accepted",
    )
    executable_migrate = """
    import { drizzle } from 'drizzle-orm/node-postgres';
    import { migrate } from 'drizzle-orm/node-postgres/migrator';
    export const fruitMigrationsFolder = '/migrations';
    export async function migrateFruitDatabase(pool: pg.Pool): Promise<void> {
      const database = drizzle(pool);
      await migrate(database, {
        migrationsFolder: fruitMigrationsFolder,
        migrationsSchema: 'fruit_meta',
        migrationsTable: '__drizzle_migrations',
      });
    }
    async function main() {
      const pool = new Pool();
      try {
        await migrateFruitDatabase(pool);
      } finally {
        await pool.end();
      }
    }
    """
    require(has_drizzle_migration_transaction(executable_migrate), "executable Drizzle fixture was rejected")
    require(
        not has_drizzle_migration_transaction(
            "/* export async function migrateFruitDatabase(pool) { await migrate(database, { migrationsFolder }); } */"
        ),
        "comment-only Drizzle fixture was accepted",
    )
    rollback = """
    BEGIN;
    SET LOCAL lock_timeout = '5s';
    LOCK TABLE \"fruit\".\"v4_documents\" IN ACCESS EXCLUSIVE MODE;
    LOCK TABLE \"fruit\".\"v4_historical_import_batches\" IN ACCESS EXCLUSIVE MODE;
    DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM \"fruit\".\"v4_documents\" WHERE doc_type = 'loss')
         OR EXISTS (SELECT 1 FROM \"fruit\".\"v4_historical_import_batches\") THEN
        RAISE EXCEPTION 'rollback guard';
      END IF;
    END $$;
    ALTER TABLE \"fruit\".\"v4_documents\" DROP CONSTRAINT \"v4_documents_type_chk\";
    ALTER TABLE \"fruit\".\"v4_documents\" ADD CONSTRAINT \"v4_documents_type_chk\"
      CHECK (\"fruit\".\"v4_documents\".\"doc_type\" IN ('receipt', 'dispatch'));
    COMMIT;
    """
    require(has_transactional_loss_rollback(rollback), "positive rollback fixture was rejected")
    require(
        not has_transactional_loss_rollback(f"{rollback}\nSELECT 1;"),
        "rollback fixture with statements after COMMIT was accepted",
    )
    wrong_order = rollback.replace(
        'LOCK TABLE "fruit"."v4_documents" IN ACCESS EXCLUSIVE MODE;\n    LOCK TABLE "fruit"."v4_historical_import_batches"',
        'LOCK TABLE "fruit"."v4_historical_import_batches" IN ACCESS EXCLUSIVE MODE;\n    LOCK TABLE "fruit"."v4_documents" IN ACCESS EXCLUSIVE MODE;',
    )
    require(not has_transactional_loss_rollback(wrong_order), "wrong lock order fixture was accepted")

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        migration_root = root / "packages/fruit-industry-pack/migrations"
        (migration_root / "meta").mkdir(parents=True)
        (migration_root / "rollback").mkdir()
        (migration_root / "0029_v4_container_lot_expand.sql").write_text("SELECT 1;", encoding="utf-8")
        (migration_root / "0030_v4_historical_batch_control.sql").write_text(
            'CREATE TABLE "fruit"."v4_historical_import_batches" (id uuid);', encoding="utf-8"
        )
        (migration_root / "0031_loss_document_type.sql").write_text(forward, encoding="utf-8")
        (migration_root / "rollback/0031_loss_document_type.sql").write_text(rollback, encoding="utf-8")
        (migration_root / "meta/_journal.json").write_text(
            json.dumps({"entries": [{"tag": tag} for tag in (
                "0029_v4_container_lot_expand",
                "0030_v4_historical_batch_control",
                "0031_loss_document_type",
            )]}),
            encoding="utf-8",
        )
        selected = select_history_provenance(root)
        require(
            selected["loss_constraint"]["tag"] == "0031_loss_document_type",
            "0030 base plus 0031 loss owner was not selected dynamically",
        )
    print("history transaction/lock self-check passed")
    return 0


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "--self-test":
        return self_check()
    if len(sys.argv) != 2:
        print("usage: check-vecta-history-consumer.py VECTA_CHECKOUT|--self-test", file=sys.stderr)
        return 2
    root = Path(sys.argv[1]).resolve()
    migration_root = root / "packages/fruit-industry-pack/migrations"
    migration_0029 = migration_root / "0029_v4_container_lot_expand.sql"
    migration_0030 = migration_root / "0030_v4_historical_batch_control.sql"
    rollback_0030 = migration_root / "rollback/0030_v4_historical_batch_control.sql"
    journal_path = migration_root / "meta/_journal.json"
    migrate_source = root / "packages/fruit-industry-pack/src/db/migrate.ts"
    batch_source = root / "packages/fruit-industry-pack/src/v4/historical-batch.ts"
    batch_plan = root / "packages/fruit-industry-pack/src/v4/historical-batch-plan.ts"
    app_source = root / "packages/fruit-industry-pack/src/app.ts"
    dry_run = root / "scripts/jiechen-history-dry-run.mjs"

    for path in (
        migration_0029,
        migration_0030,
        rollback_0030,
        journal_path,
        migrate_source,
        batch_source,
        batch_plan,
        app_source,
        dry_run,
    ):
        require(path.is_file(), f"missing history consumer file: {path.relative_to(root)}")

    migration_0029_text = migration_0029.read_text(encoding="utf-8")
    migration_0030_text = migration_0030.read_text(encoding="utf-8")
    rollback_text = rollback_0030.read_text(encoding="utf-8")
    migrate_text = migrate_source.read_text(encoding="utf-8")
    batch_text = batch_source.read_text(encoding="utf-8")
    batch_plan_text = batch_plan.read_text(encoding="utf-8")
    app_text = app_source.read_text(encoding="utf-8")
    dry_run_text = dry_run.read_text(encoding="utf-8")

    require("v4_containers" in migration_0029_text, "0029 must create the container prerequisite")
    require("v4_container_aliases" in migration_0029_text, "0029 must create the container alias prerequisite")
    table_prefix = 'CREATE TABLE "fruit"."v4_historical_import_'
    require(migration_0030_text.count(table_prefix) == 5, "0030 must define all five history audit tables")
    require(migration_0030_text.count("FORCE ROW LEVEL SECURITY") >= 5, "0030 must force RLS on all audit tables")
    require(migration_0030_text.count("_append_only") >= 5, "0030 must preserve append-only audit triggers")
    require(has_drizzle_migration_transaction(migrate_text), "Fruit migrations must run through the Drizzle transaction migrator")
    require("Refusing to drop" in rollback_text, "0030 rollback must refuse non-empty audit history")

    try:
        history_provenance = select_history_provenance(root)
    except HistoryMigrationError as error:
        raise AssertionError(str(error)) from error
    loss_migration = root / history_provenance["loss_constraint"]["path"]
    loss_rollback = root / history_provenance["loss_rollback"]["path"]
    journal_owner = "packages/fruit-industry-pack/migrations/meta/_journal.json"
    for key in ("prerequisite_0029", "schema_0030", "loss_constraint", "loss_rollback"):
        entry = history_provenance[key]
        require(entry["owner"] == journal_owner, f"{key} provenance must name the migration journal owner")
        require(Path(entry["path"]).name == f"{entry['tag']}.sql", f"{key} tag/path binding is inconsistent")
        require(type(entry["journal_index"]) is int and entry["journal_index"] >= 0, f"{key} journal index is invalid")
    require(
        history_provenance["loss_constraint"]["tag"] == history_provenance["loss_rollback"]["tag"]
        and history_provenance["loss_constraint"]["journal_index"] == history_provenance["loss_rollback"]["journal_index"],
        "loss migration and rollback must share the journal tag/index",
    )
    require(
        has_loss_document_constraint(loss_migration.read_text(encoding="utf-8")),
        "journal-owned migration must include the ordered v4_documents_type_chk update that allows loss",
    )
    require(
        loss_rollback.is_file(),
        f"loss document constraint migration is missing rollback: {loss_rollback.relative_to(root)}",
    )
    loss_rollback_text = loss_rollback.read_text(encoding="utf-8")
    rollback_clean = sql_without_comments(loss_rollback_text)
    require(
        has_tail_loss_constraint(loss_migration.read_text(encoding="utf-8")),
        "loss document CHECK migration must end with the CHECK update",
    )
    require("v4_documents_type_chk" in rollback_clean, "loss document constraint rollback must restore the prior CHECK definition")
    require(
        not has_loss_document_constraint(loss_rollback_text)
        and has_transactional_loss_rollback(loss_rollback_text),
        "loss document constraint rollback must remove loss from the prior vocabulary",
    )

    require(
        "sourceDocumentRef and evidence are derived from the frozen source row" in batch_plan_text,
        "historical Action bridge must derive row evidence",
    )
    require("sourceDocumentRef: sourceRef(row)" in batch_plan_text, "loss Actions must carry source row reference")
    require("contentSha256: row.rawRowHash" in batch_plan_text, "loss Actions must carry row evidence hash")
    require("case 'loss':" in batch_plan_text, "loss Action validation must remain first-class")

    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    entries = journal.get("entries", [])
    tags = [entry.get("tag") for entry in entries]
    require("0029_v4_container_lot_expand" in tags, "migration journal is missing 0029")
    require("0030_v4_historical_batch_control" in tags, "migration journal is missing 0030")
    require(
        tags.index("0029_v4_container_lot_expand") < tags.index("0030_v4_historical_batch_control"),
        "migration journal must apply 0029 before 0030",
    )
    loss_tag = history_provenance["loss_constraint"]["tag"]
    require(loss_tag in tags, f"migration journal is missing {loss_tag} loss CHECK update")
    require(
        tags.index("0030_v4_historical_batch_control") <= tags.index(loss_tag),
        "loss document CHECK update must apply with 0030 or a later journal entry",
    )

    require("/internal/controlled-entry/historical-batches/:batchId/execute" in app_text, "batch execute route missing")
    for flag in ("--batch-out", "--dry-run-code-sha", "--resolution-map"):
        require(flag in dry_run_text, f"history preparation flag missing: {flag}")
    order = ["WHEN 'receipt'", "WHEN 'count_adjustment'", "WHEN 'physical_transfer'", "WHEN 'dispatch'", "WHEN 'loss'"]
    positions = [batch_text.find(item) for item in order]
    require(all(position >= 0 for position in positions), "controlled action order is incomplete")
    require(positions == sorted(positions), "controlled action journal order is not receipt/count/transfer/dispatch/loss")

    runbook_path = find_production_runbook(root)
    runbook = runbook_path.read_text(encoding="utf-8")
    for term in ("0030", "writer", "backup", "restore", "confirmation", "rollback", "receipt", "dispatch", "loss"):
        require(term.lower() in runbook.lower(), f"history runbook is missing {term}")
    require(re.search(r"physical[_ ]transfer", runbook, re.IGNORECASE), "history runbook is missing transfer ordering")
    migration_heading = re.search(r"(?im)^##\s+[^\n]*(?:fixed[- ]sha|migration|迁移)", runbook)
    require(migration_heading is not None, "history runbook migration gate section is missing")
    assert migration_heading is not None
    next_heading = re.search(r"(?im)^##\s+", runbook[migration_heading.end():])
    migration_section = runbook[migration_heading.start():]
    if next_heading is not None:
        migration_section = runbook[migration_heading.start():migration_heading.end() + next_heading.start()]
    for term in (
        "drizzle",
        "access exclusive",
        "lock_timeout",
        "commit",
        "v4_documents",
    ):
        require(term in migration_section.lower(), f"history runbook migration section is missing {term} lock contract")
    require(
        "transaction" in migration_section.lower() or "事务" in migration_section,
        "history runbook migration section is missing transaction ownership",
    )
    require(
        re.search(r"\b(?:until|through)\b|直到|一直持有到", migration_section, re.IGNORECASE) is not None,
        "history runbook must state that the ACCESS EXCLUSIVE lock lasts through commit",
    )
    rollback_heading = re.search(r"(?im)^##\s+[^\n]*(?:rollback|回滚)", runbook)
    require(rollback_heading is not None, "history runbook rollback section is missing")
    assert rollback_heading is not None
    rollback_section = runbook[rollback_heading.start():].lower()
    require(
        "v4_historical_import_batches" in rollback_section,
        "history rollback section must name the second lock target",
    )
    require("playbooks/mypc-release-rollback.yml" in rollback_section, "history rollback must use the canonical rollback playbook")
    require("--require-history-provenance" in rollback_section, "history rollback must validate the prior manifest")
    require("rollback_manifest_selector_sha" in rollback_section, "history rollback must bind the full prior source SHA")
    require("history_rollback_enabled=true" in rollback_section, "history rollback must use the explicit history mode")
    require(
        re.search(r"\bpsql\s+-X\s+-v\s+on_error_stop=1", rollback_section, re.IGNORECASE) is None,
        "history runbook must not expose a standalone SQL-only rollback entry",
    )
    require(
        "writer" in rollback_section
        and "begin" in rollback_section
        and "commit" in rollback_section
        and ("quies" in rollback_section or "stop" in rollback_section or "停止" in rollback_section)
        and ("failure" in rollback_section or "失败" in rollback_section)
        and ("stopped" in rollback_section or "停止" in rollback_section),
        "history rollback must require quiesced writers and keep them stopped on failure",
    )
    require(
        not re.search(r"另一个[^。\n]{0,80}单主干|不自行恢复旧\s*develop/main", runbook),
        "history runbook must not claim an unreleased future trunk authority",
    )

    print("VectA history consumer contract passed: 0029 -> 0030, Drizzle transaction, lock order, backup/restore, and rollback")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, json.JSONDecodeError) as error:
        print(f"VectA history consumer contract rejected: {error}", file=sys.stderr)
        raise SystemExit(1)
