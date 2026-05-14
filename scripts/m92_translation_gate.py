"""Run the M9.2 full-document translation gate against temporary vaults."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import indbase_core.normalizers as normalizers
from indbase_core.db import connect
from indbase_core.documents import archive_document
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.translations import translate_full_document
from indbase_core.vault import init_vault


ROOT = Path.cwd()


def main() -> None:
    root = ROOT / ".tmp" / f"m92-translation-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    root.mkdir(parents=True)
    original_markitdown = normalizers._run_markitdown_file
    try:
        summary = _run_gate(root)
    finally:
        normalizers._run_markitdown_file = original_markitdown

    hard_zero = {
        "source_revision_mutations": summary["source_revision_mutations"],
        "source_chunk_mutations": summary["source_chunk_mutations"],
        "source_markdown_mutations": summary["source_markdown_mutations"],
        "old_revision_full_document_translations": summary["old_revision_full_document_translations"],
        "archived_doc_full_document_translations": summary["archived_doc_full_document_translations"],
        "source_shell_full_document_translations": summary["source_shell_full_document_translations"],
        "no_chunk_full_document_translations": summary["no_chunk_full_document_translations"],
        "translation_missing_doc_revision_chunks": summary["translation_missing_doc_revision_chunks"],
        "full_document_missing_current_chunk_ids": summary["full_document_missing_current_chunk_ids"],
        "outputs_outside_translations_dir": summary["outputs_outside_translations_dir"],
        "citations_created": summary["citations_created"],
    }
    if any(value != 0 for value in hard_zero.values()):
        raise RuntimeError(f"M9.2 hard zero metrics failed: {hard_zero}")
    if summary["translations_written"] <= 0 or summary["executions_written"] <= 0:
        raise RuntimeError(f"M9.2 did not write translation/execution records: {summary}")
    if summary["output_files_written"] <= 0:
        raise RuntimeError(f"M9.2 did not write output files: {summary}")
    if summary["full_document_chunk_ids_recorded"] <= 0:
        raise RuntimeError(f"M9.2 did not record source chunk ids: {summary}")
    if summary["full_document_mode_records"] != 1:
        raise RuntimeError(f"M9.2 did not record full_document mode: {summary}")

    print(json.dumps(summary, sort_keys=True))
    print("M92_TRANSLATION_GATE=passed")
    print(f"DOGFOOD_ROOT={root}")


def _run_gate(root: Path) -> dict[str, int]:
    success = _success_gate(root / "success")
    old_revision = _old_revision_gate(root / "old-revision")
    archived = _archived_gate(root / "archived")
    shell = _source_shell_gate(root / "shell")
    no_chunk = _no_chunk_gate(root / "no-chunk")
    return {**success, **old_revision, **archived, **shell, **no_chunk}


def _success_gate(root: Path) -> dict[str, int]:
    vault, source, doc_id, revision_id, chunks = _vault_with_chunks(root)
    canonical = _scalar(vault, "SELECT canonical_path FROM documents WHERE doc_id = ?", (doc_id,))
    markdown_before = (vault / str(canonical)).read_text(encoding="utf-8")
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        before_revisions = _count(connection, "SELECT COUNT(*) AS count FROM document_revisions")
        before_chunks = _count(connection, "SELECT COUNT(*) AS count FROM chunks")
        result = translate_full_document(
            connection,
            vault,
            doc_id=doc_id,
            revision_id=revision_id,
            target_language="zh-CN",
        )
        after_revisions = _count(connection, "SELECT COUNT(*) AS count FROM document_revisions")
        after_chunks = _count(connection, "SELECT COUNT(*) AS count FROM chunks")
        output_files = len(list((vault / "outputs" / "translations").glob("*.md")))
        output_path = vault / result.output_path
        output_text = output_path.read_text(encoding="utf-8")
        translation_chunk_ids = json.loads(
            connection.execute("SELECT source_chunk_ids_json FROM translations").fetchone()[0]
        )
        summary = {
            "translations_written": _count(connection, "SELECT COUNT(*) AS count FROM translations"),
            "executions_written": _count(connection, "SELECT COUNT(*) AS count FROM executions"),
            "output_files_written": output_files,
            "full_document_chunk_ids_recorded": len(translation_chunk_ids),
            "full_document_mode_records": _count(
                connection,
                "SELECT COUNT(*) AS count FROM translations WHERE translation_mode = 'full_document'",
            ),
            "full_document_missing_current_chunk_ids": int(tuple(translation_chunk_ids) != chunks),
            "source_revision_mutations": int(after_revisions != before_revisions),
            "source_chunk_mutations": int(after_chunks != before_chunks),
            "source_markdown_mutations": int((vault / str(canonical)).read_text(encoding="utf-8") != markdown_before),
            "translation_missing_doc_revision_chunks": _translation_integrity_errors(connection),
            "outputs_outside_translations_dir": int(not str(result.output_path).startswith("outputs/translations/")),
            "citations_created": _count(connection, "SELECT COUNT(*) AS count FROM citations"),
            "output_contains_source_ids": int(
                doc_id in output_text and revision_id in output_text and all(chunk_id in output_text for chunk_id in chunks)
            ),
        }
    if summary["output_contains_source_ids"] != 1:
        raise RuntimeError(f"full-document output did not include required source IDs: {summary}")
    return summary


def _old_revision_gate(root: Path) -> dict[str, int]:
    vault, source, doc_id, old_revision_id, _chunks = _vault_with_chunks(root)
    source.write_text("# Handbook\nChanged revision for full-document translation gate.\n", encoding="utf-8")
    run_m3_ingest_pipeline(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        try:
            translate_full_document(
                connection,
                vault,
                doc_id=doc_id,
                revision_id=old_revision_id,
                target_language="zh-CN",
            )
        except ValueError:
            pass
        else:
            raise RuntimeError("old revision full-document translation unexpectedly succeeded")
        translations = _count(connection, "SELECT COUNT(*) AS count FROM translations")
    return {"old_revision_full_document_translations": translations}


def _archived_gate(root: Path) -> dict[str, int]:
    vault, _source, doc_id, revision_id, _chunks = _vault_with_chunks(root)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        archive_document(connection, doc_id)
        try:
            translate_full_document(
                connection,
                vault,
                doc_id=doc_id,
                revision_id=revision_id,
                target_language="zh-CN",
            )
        except ValueError:
            pass
        else:
            raise RuntimeError("archived full-document translation unexpectedly succeeded")
        translations = _count(connection, "SELECT COUNT(*) AS count FROM translations")
    return {"archived_doc_full_document_translations": translations}


def _source_shell_gate(root: Path) -> dict[str, int]:
    vault = root / "vault"
    source = root / "scan.pdf"
    root.mkdir(parents=True)
    source.write_bytes(b"%PDF image only")
    init_vault(vault)
    normalizers._run_markitdown_file = lambda _path: " "
    run_m3_ingest_pipeline(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()[0]
        try:
            translate_full_document(
                connection,
                vault,
                doc_id=doc_id,
                revision_id="rev_missing",
                target_language="zh-CN",
            )
        except ValueError:
            pass
        else:
            raise RuntimeError("source shell full-document translation unexpectedly succeeded")
        translations = _count(connection, "SELECT COUNT(*) AS count FROM translations")
    return {"source_shell_full_document_translations": translations}


def _no_chunk_gate(root: Path) -> dict[str, int]:
    vault, _source, doc_id, revision_id, _chunks = _vault_with_chunks(root)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        connection.execute("DELETE FROM chunks WHERE doc_id = ? AND revision_id = ?", (doc_id, revision_id))
        connection.commit()
        try:
            translate_full_document(
                connection,
                vault,
                doc_id=doc_id,
                revision_id=revision_id,
                target_language="zh-CN",
            )
        except ValueError:
            pass
        else:
            raise RuntimeError("no-chunk full-document translation unexpectedly succeeded")
        translations = _count(connection, "SELECT COUNT(*) AS count FROM translations")
    return {"no_chunk_full_document_translations": translations}


def _vault_with_chunks(root: Path) -> tuple[Path, Path, str, str, tuple[str, ...]]:
    vault = root / "vault"
    source = root / "source.md"
    root.mkdir(parents=True)
    source.write_text(
        "# Handbook\n"
        "Intro text for full-document translation gate.\n\n"
        "## Setup\n"
        "Install the package before running translation.\n\n"
        "## Usage\n"
        "Run full-document translation from current source chunks.\n",
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


def _translation_integrity_errors(connection) -> int:
    rows = connection.execute("SELECT source_doc_id, source_revision_id, source_chunk_ids_json FROM translations").fetchall()
    errors = 0
    for row in rows:
        chunk_ids = json.loads(row["source_chunk_ids_json"])
        chunks_exist = _count(
            connection,
            f"""
            SELECT COUNT(*) AS count
            FROM chunks
            WHERE chunk_id IN ({", ".join("?" for _ in chunk_ids)})
              AND doc_id = ?
              AND revision_id = ?
              AND deleted_at IS NULL
            """,
            (*chunk_ids, row["source_doc_id"], row["source_revision_id"]),
        )
        current_chunks = tuple(
            chunk["chunk_id"]
            for chunk in connection.execute(
                """
                SELECT chunk_id
                FROM chunks
                WHERE doc_id = ?
                  AND revision_id = ?
                  AND is_current = 1
                  AND deleted_at IS NULL
                ORDER BY sequence, chunk_id
                """,
                (row["source_doc_id"], row["source_revision_id"]),
            )
        )
        if chunks_exist != len(chunk_ids) or tuple(chunk_ids) != current_chunks:
            errors += 1
    return errors


def _scalar(vault: Path, sql: str, params: tuple[object, ...] = ()) -> object:
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        return connection.execute(sql, params).fetchone()[0]


def _count(connection, sql: str, params: tuple[object, ...] = ()) -> int:
    return int(connection.execute(sql, params).fetchone()["count"] or 0)


if __name__ == "__main__":
    main()
