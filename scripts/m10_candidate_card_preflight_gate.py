"""Run the M10 candidate-card preflight gate against temporary vaults."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3

import indbase_core.normalizers as normalizers
from indbase_core.db import connect
from indbase_core.doctor import run_doctor
from indbase_core.documents import archive_document
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.search import SearchOptions, search_chunks
from indbase_core.translations import translate_full_document, translate_selected_chunks
from indbase_core.vault import init_vault


ROOT = Path.cwd()


class FailingSecondChunkTranslationAdapter:
    model = "local/partial-failing-translation-gate"

    def __init__(self) -> None:
        self.calls = 0

    def translate(self, text: str, *, source_language: str | None, target_language: str) -> str:
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("simulated partial translation failure")
        return f"[{target_language}] {text}"


def main() -> None:
    root = ROOT / ".tmp" / f"m10-candidate-card-preflight-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    root.mkdir(parents=True)
    original_markitdown = normalizers._run_markitdown_file
    try:
        summary = _run_gate(root)
    finally:
        normalizers._run_markitdown_file = original_markitdown

    hard_zero = {
        "duplicate_translation_overwrites": summary["duplicate_translation_overwrites"],
        "duplicate_translation_output_collisions": summary["duplicate_translation_output_collisions"],
        "multiple_target_output_collisions": summary["multiple_target_output_collisions"],
        "selected_full_output_collisions": summary["selected_full_output_collisions"],
        "partial_translation_success_records": summary["partial_translation_success_records"],
        "partial_translation_output_files": summary["partial_translation_output_files"],
        "partial_failed_errors_missing": summary["partial_failed_errors_missing"],
        "missing_translation_output_source_corruption": summary["missing_translation_output_source_corruption"],
        "orphan_translation_records_undetected": summary["orphan_translation_records_undetected"],
        "archived_docs_in_default_search": summary["archived_docs_in_default_search"],
        "source_shells_searchable": summary["source_shells_searchable"],
        "old_revision_default_results": summary["old_revision_default_results"],
        "translations_mutating_source": summary["translations_mutating_source"],
    }
    if any(value != 0 for value in hard_zero.values()):
        raise RuntimeError(f"M10 preflight hard zero metrics failed: {hard_zero}")

    positives = {
        "multiple_target_language_records": summary["multiple_target_language_records"] >= 3,
        "selected_and_full_outputs_coexist": summary["selected_and_full_outputs_coexist"] == 1,
        "missing_translation_output_detected": summary["missing_translation_output_detected"] == 1,
        "orphan_translation_records_detected": summary["orphan_translation_records_detected"] > 0,
        "active_current_documents": summary["active_current_documents"] > 0,
        "current_chunks": summary["current_chunks"] > 0,
        "review_queue_works": summary["review_queue_works"] == 1,
        "candidate_preflight_passed": summary["candidate_preflight_passed"] == 1,
    }
    if not all(positives.values()):
        raise RuntimeError(f"M10 preflight positive metrics failed: {positives}; summary={summary}")

    print(json.dumps(summary, sort_keys=True))
    print("M10_CANDIDATE_CARD_PREFLIGHT_GATE=passed")
    print(f"DOGFOOD_ROOT={root}")


def _run_gate(root: Path) -> dict[str, int]:
    translation = _translation_duplicate_multilanguage_gate(root / "translation")
    doctor = _translation_doctor_gate(root / "doctor")
    preflight = _source_binding_preflight_gate(root / "source-binding")
    return {**translation, **doctor, **preflight}


def _translation_duplicate_multilanguage_gate(root: Path) -> dict[str, int]:
    vault, _source, doc_id, revision_id, chunks = _vault_with_chunks(root)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        first = translate_full_document(connection, vault, doc_id=doc_id, revision_id=revision_id, target_language="zh-CN")
        duplicate = translate_full_document(
            connection,
            vault,
            doc_id=doc_id,
            revision_id=revision_id,
            target_language="zh-CN",
        )
        en = translate_full_document(connection, vault, doc_id=doc_id, revision_id=revision_id, target_language="en")
        ja = translate_full_document(connection, vault, doc_id=doc_id, revision_id=revision_id, target_language="ja")
        selected = translate_selected_chunks(
            connection,
            vault,
            doc_id=doc_id,
            revision_id=revision_id,
            chunk_ids=(chunks[0],),
            target_language="zh-CN",
        )
        rows = connection.execute("SELECT translation_mode, target_language, output_path FROM translations").fetchall()
        before_partial_translations = _count(connection, "SELECT COUNT(*) AS count FROM translations")
        before_partial_outputs = len(list((vault / "outputs" / "translations").glob("*.md")))
        try:
            translate_full_document(
                connection,
                vault,
                doc_id=doc_id,
                revision_id=revision_id,
                target_language="fr",
                adapter=FailingSecondChunkTranslationAdapter(),
            )
        except RuntimeError:
            pass
        else:
            raise RuntimeError("partial failing full-document translation unexpectedly succeeded")
        after_partial_translations = _count(connection, "SELECT COUNT(*) AS count FROM translations")
        after_partial_outputs = len(list((vault / "outputs" / "translations").glob("*.md")))
        partial_error_count = _count(connection, "SELECT COUNT(*) AS count FROM errors WHERE component = 'translation'")

    output_paths = [str(row["output_path"]) for row in rows]
    full_output_paths = [str(row["output_path"]) for row in rows if row["translation_mode"] == "full_document"]
    mode_paths = {first.output_path, duplicate.output_path, en.output_path, ja.output_path, selected.output_path}
    return {
        "duplicate_translation_overwrites": int(first.output_path == duplicate.output_path),
        "duplicate_translation_output_collisions": _collision_count([first.output_path, duplicate.output_path]),
        "multiple_target_language_records": len({row["target_language"] for row in rows}),
        "multiple_target_output_collisions": _collision_count(full_output_paths),
        "selected_and_full_outputs_coexist": int({"selected_chunks", "full_document"} == {row["translation_mode"] for row in rows}),
        "selected_full_output_collisions": len(mode_paths) - len(set(mode_paths)),
        "partial_translation_success_records": after_partial_translations - before_partial_translations,
        "partial_translation_output_files": after_partial_outputs - before_partial_outputs,
        "partial_failed_errors_missing": int(partial_error_count == 0),
        "translation_output_collisions": _collision_count(output_paths),
    }


def _translation_doctor_gate(root: Path) -> dict[str, int]:
    missing = _missing_translation_output_gate(root / "missing-output")
    orphan = _orphan_translation_record_gate(root / "orphan-record")
    return {**missing, **orphan}


def _missing_translation_output_gate(root: Path) -> dict[str, int]:
    vault, _source, doc_id, revision_id, _chunks = _vault_with_chunks(root)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        result = translate_full_document(connection, vault, doc_id=doc_id, revision_id=revision_id, target_language="zh-CN")
    (vault / result.output_path).unlink()

    codes = _doctor_codes(vault)
    source_corruption_codes = {"missing_canonical_markdown", "missing_revision_markdown", "missing_current_chunks"}
    return {
        "missing_translation_output_detected": int("missing_translation_output" in codes),
        "missing_translation_output_source_corruption": len(codes & source_corruption_codes),
    }


def _orphan_translation_record_gate(root: Path) -> dict[str, int]:
    vault, _source, doc_id, revision_id, _chunks = _vault_with_chunks(root)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        result = translate_full_document(connection, vault, doc_id=doc_id, revision_id=revision_id, target_language="zh-CN")
        row = connection.execute("SELECT execution_id FROM translations WHERE translation_id = ?", (result.translation_id,)).fetchone()

    raw_connection = sqlite3.connect(vault / ".indbase" / "db.sqlite")
    try:
        raw_connection.execute(
            """
            UPDATE translations
            SET source_doc_id = 'doc_missing',
                source_revision_id = 'rev_missing',
                source_chunk_ids_json = '["chunk_missing"]'
            WHERE translation_id = ?
            """,
            (result.translation_id,),
        )
        raw_connection.execute(
            "UPDATE executions SET output_path = 'outputs/translations/mismatch.md' WHERE execution_id = ?",
            (row["execution_id"],),
        )
        raw_connection.commit()
    finally:
        raw_connection.close()

    codes = _doctor_codes(vault)
    expected = {
        "orphan_translation_document",
        "orphan_translation_revision",
        "translation_source_chunk_missing",
        "translation_execution_output_mismatch",
    }
    detected = len(codes & expected)
    return {
        "orphan_translation_records_detected": detected,
        "orphan_translation_records_undetected": len(expected) - detected,
    }


def _source_binding_preflight_gate(root: Path) -> dict[str, int]:
    vault = root / "vault"
    root.mkdir(parents=True)
    active_source = root / "active.md"
    active_source.write_text(
        "# Active\nm10activecurrentonlytoken for candidate cards.\n",
        encoding="utf-8",
    )
    archived_source = root / "archived.md"
    archived_source.write_text(
        "# Archived\nm10archivedonlytoken should not appear.\n",
        encoding="utf-8",
    )
    changing_source = root / "changing.md"
    changing_source.write_text(
        "# Changing\nm10oldrevisiononlytoken before reingest.\n",
        encoding="utf-8",
    )
    unsupported_source = root / "unsupported.bin"
    unsupported_source.write_bytes(b"unsupported")
    shell_source = root / "scan.pdf"
    shell_source.write_bytes(b"%PDF image only")

    init_vault(vault)
    run_m3_ingest_pipeline(vault, active_source)
    run_m3_ingest_pipeline(vault, archived_source)
    run_m3_ingest_pipeline(vault, changing_source)
    run_m3_ingest_pipeline(vault, unsupported_source)
    normalizers._run_markitdown_file = lambda _path: " "
    run_m3_ingest_pipeline(vault, shell_source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        active_doc = connection.execute(
            "SELECT doc_id, canonical_path FROM documents WHERE title = 'active'"
        ).fetchone()
        archived_doc = connection.execute("SELECT doc_id FROM documents WHERE title = 'archived'").fetchone()
        archive_document(connection, archived_doc["doc_id"])

    changing_source.write_text("# Changing\nm10newrevisiononlytoken after reingest.\n", encoding="utf-8")
    run_m3_ingest_pipeline(vault, changing_source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        before_revisions = _count(connection, "SELECT COUNT(*) AS count FROM document_revisions")
        before_chunks = _count(connection, "SELECT COUNT(*) AS count FROM chunks")
        before_markdown = (vault / active_doc["canonical_path"]).read_text(encoding="utf-8")
        current = connection.execute(
            "SELECT current_revision_id FROM documents WHERE doc_id = ?",
            (active_doc["doc_id"],),
        ).fetchone()
        translate_full_document(
            connection,
            vault,
            doc_id=active_doc["doc_id"],
            revision_id=current["current_revision_id"],
            target_language="zh-CN",
        )
        after_revisions = _count(connection, "SELECT COUNT(*) AS count FROM document_revisions")
        after_chunks = _count(connection, "SELECT COUNT(*) AS count FROM chunks")
        after_markdown = (vault / active_doc["canonical_path"]).read_text(encoding="utf-8")

        active_current_documents = _count(
            connection,
            """
            SELECT COUNT(*) AS count
            FROM documents
            WHERE status = 'active'
              AND current_revision_id IS NOT NULL
              AND deleted_at IS NULL
            """,
        )
        current_chunks = _count(
            connection,
            """
            SELECT COUNT(*) AS count
            FROM chunks c
            JOIN documents d ON d.doc_id = c.doc_id
            WHERE d.status = 'active'
              AND d.current_revision_id = c.revision_id
              AND c.is_current = 1
              AND c.deleted_at IS NULL
              AND d.deleted_at IS NULL
            """,
        )
        archived_results = search_chunks(
            connection,
            "m10archivedonlytoken",
            options=SearchOptions(log_queries=False),
        ).result_count
        old_revision_results = search_chunks(
            connection,
            "m10oldrevisiononlytoken",
            options=SearchOptions(log_queries=False),
        ).result_count
        source_shells_searchable = _count(
            connection,
            """
            SELECT COUNT(*) AS count
            FROM documents d
            JOIN chunks_fts f ON f.doc_id = d.doc_id
            WHERE d.current_revision_id IS NULL
              AND d.deleted_at IS NULL
            """,
        )
        review_queue_works = int(
            _count(connection, "SELECT COUNT(*) AS count FROM review_items WHERE status = 'pending'") > 0
        )
        translations_mutating_source = int(
            before_revisions != after_revisions or before_chunks != after_chunks or before_markdown != after_markdown
        )

    candidate_preflight_passed = int(
        active_current_documents > 0
        and current_chunks > 0
        and archived_results == 0
        and old_revision_results == 0
        and source_shells_searchable == 0
        and translations_mutating_source == 0
        and review_queue_works == 1
    )
    return {
        "active_current_documents": active_current_documents,
        "current_chunks": current_chunks,
        "archived_docs_in_default_search": archived_results,
        "source_shells_searchable": source_shells_searchable,
        "old_revision_default_results": old_revision_results,
        "translations_mutating_source": translations_mutating_source,
        "review_queue_works": review_queue_works,
        "candidate_preflight_passed": candidate_preflight_passed,
    }


def _vault_with_chunks(root: Path) -> tuple[Path, Path, str, str, tuple[str, ...]]:
    vault = root / "vault"
    source = root / "source.md"
    root.mkdir(parents=True)
    source.write_text(
        "# Handbook\n"
        "Intro text for M10 readiness translation gate.\n\n"
        "## Setup\n"
        "Install the package before running translation.\n\n"
        "## Usage\n"
        "Run selected and full-document translation from current chunks.\n",
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


def _doctor_codes(vault: Path) -> set[str]:
    return {finding.code for finding in run_doctor(vault).findings}


def _collision_count(values: list[str]) -> int:
    return len(values) - len(set(values))


def _count(connection, sql: str, params: tuple[object, ...] = ()) -> int:
    return int(connection.execute(sql, params).fetchone()["count"] or 0)


if __name__ == "__main__":
    main()
