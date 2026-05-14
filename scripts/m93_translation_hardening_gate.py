"""Run the M9.3 translation hardening gate against temporary vaults."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from indbase_core.db import connect
from indbase_core.documents import archive_document
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.translations import (
    resolve_translation_output_path,
    translate_full_document,
    translate_selected_chunks,
)
from indbase_core.vault import init_vault


ROOT = Path.cwd()


class FailingTranslationAdapter:
    model = "local/failing-translation-gate"

    def translate(self, text: str, *, source_language: str | None, target_language: str) -> str:
        raise RuntimeError("simulated translation adapter failure")


def main() -> None:
    root = ROOT / ".tmp" / f"m93-translation-hardening-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    root.mkdir(parents=True)
    summary = _run_gate(root)

    hard_zero = {
        "translations_rebound_after_reingest": summary["translations_rebound_after_reingest"],
        "missing_output_after_reingest": summary["missing_output_after_reingest"],
        "missing_output_after_archive": summary["missing_output_after_archive"],
        "archived_new_translation_records": summary["archived_new_translation_records"],
        "failed_translation_records": summary["failed_translation_records"],
        "failed_output_files": summary["failed_output_files"],
        "failed_executions_not_failed": summary["failed_executions_not_failed"],
        "failed_tasks_not_failed": summary["failed_tasks_not_failed"],
        "failed_errors_missing": summary["failed_errors_missing"],
        "running_translation_tasks": summary["running_translation_tasks"],
        "running_translation_executions": summary["running_translation_executions"],
        "output_paths_missing": summary["output_paths_missing"],
    }
    if any(value != 0 for value in hard_zero.values()):
        raise RuntimeError(f"M9.3 hard zero metrics failed: {hard_zero}")
    if summary["stale_translation_records_after_reingest"] < 2:
        raise RuntimeError(f"M9.3 did not preserve old-revision translation records after re-ingest: {summary}")
    if summary["archived_existing_translations_listed"] < 1:
        raise RuntimeError(f"M9.3 did not keep archived-document translations listable: {summary}")
    if summary["failed_execution_records"] < 1:
        raise RuntimeError(f"M9.3 did not persist failed execution state: {summary}")

    print(json.dumps(summary, sort_keys=True))
    print("M93_TRANSLATION_HARDENING_GATE=passed")
    print(f"DOGFOOD_ROOT={root}")


def _run_gate(root: Path) -> dict[str, int]:
    reingest = _reingest_binding_gate(root / "reingest")
    archived = _archived_output_gate(root / "archived")
    failure = _adapter_failure_gate(root / "failure")
    return {**reingest, **archived, **failure}


def _reingest_binding_gate(root: Path) -> dict[str, int]:
    vault, source, doc_id, old_revision_id, chunks = _vault_with_chunks(root)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        selected = translate_selected_chunks(
            connection,
            vault,
            doc_id=doc_id,
            revision_id=old_revision_id,
            chunk_ids=(chunks[0],),
            target_language="zh-CN",
        )
        full = translate_full_document(
            connection,
            vault,
            doc_id=doc_id,
            revision_id=old_revision_id,
            target_language="ja",
        )
        selected_output = resolve_translation_output_path(connection, vault, selected.translation_id)
        full_output = resolve_translation_output_path(connection, vault, full.translation_id)

    source.write_text("# Handbook\nChanged content after existing translation outputs.\n", encoding="utf-8")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        new_revision_id = connection.execute(
            "SELECT current_revision_id FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()["current_revision_id"]
        rebound = _count(
            connection,
            "SELECT COUNT(*) AS count FROM translations WHERE source_doc_id = ? AND source_revision_id = ?",
            (doc_id, new_revision_id),
        )
        stale_records = _count(
            connection,
            "SELECT COUNT(*) AS count FROM translations WHERE source_doc_id = ? AND source_revision_id = ?",
            (doc_id, old_revision_id),
        )
        missing_output = 0
        for translation_id in (selected.translation_id, full.translation_id):
            try:
                resolve_translation_output_path(connection, vault, translation_id)
            except ValueError:
                missing_output += 1
        output_paths_missing = _output_paths_missing(connection, vault)

    return {
        "translations_rebound_after_reingest": rebound,
        "stale_translation_records_after_reingest": stale_records,
        "missing_output_after_reingest": missing_output,
        "output_paths_missing": output_paths_missing,
        "pre_reingest_outputs_existed": int(selected_output.is_file() and full_output.is_file()),
    }


def _archived_output_gate(root: Path) -> dict[str, int]:
    vault, _source, doc_id, revision_id, chunks = _vault_with_chunks(root)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        result = translate_selected_chunks(
            connection,
            vault,
            doc_id=doc_id,
            revision_id=revision_id,
            chunk_ids=(chunks[0],),
            target_language="zh-CN",
        )
        before = _count(connection, "SELECT COUNT(*) AS count FROM translations")
        archive_document(connection, doc_id)
        listed = _count(connection, "SELECT COUNT(*) AS count FROM translations WHERE source_doc_id = ?", (doc_id,))
        try:
            resolve_translation_output_path(connection, vault, result.translation_id)
            missing_output = 0
        except ValueError:
            missing_output = 1
        try:
            translate_selected_chunks(
                connection,
                vault,
                doc_id=doc_id,
                revision_id=revision_id,
                chunk_ids=(chunks[0],),
                target_language="ja",
            )
        except ValueError:
            pass
        else:
            raise RuntimeError("archived document translation unexpectedly succeeded")
        after = _count(connection, "SELECT COUNT(*) AS count FROM translations")

    return {
        "archived_existing_translations_listed": listed,
        "missing_output_after_archive": missing_output,
        "archived_new_translation_records": after - before,
    }


def _adapter_failure_gate(root: Path) -> dict[str, int]:
    vault, _source, doc_id, revision_id, _chunks = _vault_with_chunks(root)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        try:
            translate_full_document(
                connection,
                vault,
                doc_id=doc_id,
                revision_id=revision_id,
                target_language="zh-CN",
                adapter=FailingTranslationAdapter(),
            )
        except RuntimeError:
            pass
        else:
            raise RuntimeError("failing adapter translation unexpectedly succeeded")
        return {
            "failed_translation_records": _count(connection, "SELECT COUNT(*) AS count FROM translations"),
            "failed_output_files": len(list((vault / "outputs" / "translations").glob("*.md"))),
            "failed_executions_not_failed": _count(
                connection,
                "SELECT COUNT(*) AS count FROM executions WHERE type LIKE 'translation.%' AND status != 'failed'",
            ),
            "failed_tasks_not_failed": _count(
                connection,
                "SELECT COUNT(*) AS count FROM tasks WHERE type LIKE 'translation_%' AND status != 'failed'",
            ),
            "failed_errors_missing": int(_count(connection, "SELECT COUNT(*) AS count FROM errors WHERE component = 'translation'") == 0),
            "failed_execution_records": _count(
                connection,
                "SELECT COUNT(*) AS count FROM executions WHERE type LIKE 'translation.%' AND status = 'failed'",
            ),
            "running_translation_tasks": _count(
                connection,
                "SELECT COUNT(*) AS count FROM tasks WHERE type LIKE 'translation_%' AND status IN ('pending', 'running')",
            ),
            "running_translation_executions": _count(
                connection,
                "SELECT COUNT(*) AS count FROM executions WHERE type LIKE 'translation.%' AND status = 'running'",
            ),
        }


def _vault_with_chunks(root: Path) -> tuple[Path, Path, str, str, tuple[str, ...]]:
    vault = root / "vault"
    source = root / "source.md"
    root.mkdir(parents=True)
    source.write_text(
        "# Handbook\n"
        "Intro text for translation hardening gate.\n\n"
        "## Setup\n"
        "Install the package before running translation.\n\n"
        "## Usage\n"
        "Run translation from source chunks.\n",
        encoding="utf-8",
    )
    init_vault(vault)
    run_m3_ingest_pipeline(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc = connection.execute("SELECT doc_id, current_revision_id FROM documents").fetchone()
        chunks = tuple(
            row["chunk_id"]
            for row in connection.execute(
                """
                SELECT chunk_id
                FROM chunks
                WHERE doc_id = ?
                  AND revision_id = ?
                  AND is_current = 1
                ORDER BY sequence, chunk_id
                """,
                (doc["doc_id"], doc["current_revision_id"]),
            )
        )
    return vault, source, str(doc["doc_id"]), str(doc["current_revision_id"]), chunks


def _output_paths_missing(connection, vault: Path) -> int:
    missing = 0
    for row in connection.execute("SELECT translation_id FROM translations").fetchall():
        try:
            resolve_translation_output_path(connection, vault, row["translation_id"])
        except ValueError:
            missing += 1
    return missing


def _count(connection, sql: str, params: tuple[object, ...] = ()) -> int:
    return int(connection.execute(sql, params).fetchone()["count"] or 0)


if __name__ == "__main__":
    main()
