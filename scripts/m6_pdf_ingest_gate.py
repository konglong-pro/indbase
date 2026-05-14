"""Run the M6.1 PDF text ingest gate against temporary vaults."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3

import indbase_core.normalizers as normalizers
from indbase_core.db import connect
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.search import search_chunks
from indbase_core.vault import init_vault


ROOT = Path.cwd()


def main() -> None:
    root = ROOT / ".tmp" / f"m6-pdf-ingest-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    root.mkdir(parents=True)
    original_markitdown = normalizers._run_markitdown_file

    try:
        success_summary = _run_pdf_success(root / "success")
        failure_summary = _run_pdf_no_text_failure(root / "failure")
        unsupported_summary = _run_unsupported_image(root / "unsupported")
    finally:
        normalizers._run_markitdown_file = original_markitdown

    summary = {
        **success_summary,
        **failure_summary,
        **unsupported_summary,
    }
    print(json.dumps(summary, sort_keys=True))
    print("M6_PDF_INGEST_GATE=passed")
    print(f"DOGFOOD_ROOT={root}")


def _run_pdf_success(root: Path) -> dict[str, int]:
    vault = root / "vault"
    source = root / "paper.pdf"
    root.mkdir(parents=True)
    source.write_bytes(b"%PDF placeholder text pdf")
    normalizers._run_markitdown_file = lambda _path: "# Paper\nPDF gate text needle\n"

    init_vault(vault)
    result = run_m3_ingest_pipeline(vault, source)
    if result.status != "succeeded" or result.searchable is not True:
        raise RuntimeError(f"PDF success ingest failed: {result}")

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        document = connection.execute(
            "SELECT source_type, current_revision_id, original_path, fts_status FROM documents"
        ).fetchone()
        search = search_chunks(connection, "PDF gate text needle")
        summary = {
            "pdf_success_documents": connection.execute(
                "SELECT COUNT(*) AS count FROM documents WHERE source_type = 'pdf'"
            ).fetchone()["count"],
            "pdf_success_revisions": connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()["count"],
            "pdf_success_chunks": connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()["count"],
            "pdf_success_fts": connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()["count"],
            "pdf_success_search_results": search.result_count,
        }
    finally:
        connection.close()

    if document["source_type"] != "pdf":
        raise RuntimeError(f"PDF source_type not recorded: {document['source_type']}")
    if document["current_revision_id"] is None or document["fts_status"] != "indexed":
        raise RuntimeError(f"PDF document was not searchable: {dict(document)}")
    if not str(document["original_path"]).endswith("/original.pdf"):
        raise RuntimeError(f"PDF original path was not preserved: {document['original_path']}")
    if summary["pdf_success_search_results"] != 1:
        raise RuntimeError("PDF text was not searchable")
    return summary


def _run_pdf_no_text_failure(root: Path) -> dict[str, int]:
    vault = root / "vault"
    source = root / "scan.pdf"
    root.mkdir(parents=True)
    source.write_bytes(b"%PDF image only")
    normalizers._run_markitdown_file = lambda _path: "   "

    init_vault(vault)
    result = run_m3_ingest_pipeline(vault, source)
    if result.status != "completed_with_issues" or result.searchable is not False:
        raise RuntimeError(f"PDF no-text ingest did not fail visibly: {result}")

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        summary = {
            "pdf_failure_documents": connection.execute(
                "SELECT COUNT(*) AS count FROM documents WHERE source_type = 'pdf'"
            ).fetchone()["count"],
            "pdf_failure_revisions": connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()["count"],
            "pdf_failure_chunks": connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()["count"],
            "pdf_failure_fts": connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()["count"],
            "pdf_failure_errors": connection.execute(
                "SELECT COUNT(*) AS count FROM errors WHERE error_type = 'no_extractable_content'"
            ).fetchone()["count"],
            "pdf_failure_reviews": connection.execute(
                "SELECT COUNT(*) AS count FROM review_items WHERE type = 'conversion_low_quality'"
            ).fetchone()["count"],
            "zero_chunk_current_revisions": connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM documents d
                WHERE d.current_revision_id IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1
                    FROM chunks c
                    WHERE c.doc_id = d.doc_id
                      AND c.revision_id = d.current_revision_id
                      AND c.is_current = 1
                      AND c.deleted_at IS NULL
                  )
                """
            ).fetchone()["count"],
        }
    finally:
        connection.close()

    expected = {
        "pdf_failure_documents": 1,
        "pdf_failure_revisions": 0,
        "pdf_failure_chunks": 0,
        "pdf_failure_fts": 0,
        "pdf_failure_errors": 1,
        "pdf_failure_reviews": 1,
        "zero_chunk_current_revisions": 0,
    }
    for key, value in expected.items():
        if summary[key] != value:
            raise RuntimeError(f"unexpected PDF failure summary {key}: {summary}")
    return summary


def _run_unsupported_image(root: Path) -> dict[str, int]:
    vault = root / "vault"
    source = root / "image.png"
    root.mkdir(parents=True)
    source.write_bytes(b"png")

    init_vault(vault)
    result = run_m3_ingest_pipeline(vault, source)
    if result.status != "completed_with_issues" or result.unsupported_items != 1:
        raise RuntimeError(f"unsupported image did not remain unsupported: {result}")

    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        return {
            "unsupported_documents_created": connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
            "unsupported_items": connection.execute(
                "SELECT COUNT(*) FROM ingest_items WHERE status = 'unsupported'"
            ).fetchone()[0],
            "unsupported_reviews": connection.execute(
                "SELECT COUNT(*) FROM review_items WHERE type = 'unsupported_source'"
            ).fetchone()[0],
        }


if __name__ == "__main__":
    main()
