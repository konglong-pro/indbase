"""Historical M6 PDF ingest gate for provider-era semantics.

The original M6 gate expected indbase direct/MarkItDown PDF conversion to
create a trusted searchable revision. That path is retired. This guard now
proves the current default behavior: legacy PDF direct conversion fails visibly
as ``legacy_conversion_retired`` and does not create source-search pollution.
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3

from indbase_core.db import connect
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.search import search_chunks
from indbase_core.vault import init_vault


ROOT = Path.cwd()


def main() -> None:
    root = ROOT / ".tmp" / f"m6-pdf-ingest-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    root.mkdir(parents=True)

    summary = {
        **_run_pdf_legacy_retired(root / "legacy-retired"),
        **_run_unsupported_image(root / "unsupported"),
    }
    print(json.dumps(summary, sort_keys=True))
    print("M6_PDF_INGEST_GATE_MODE=historical_provider_era_legacy_retired")
    print("M6_PDF_INGEST_GATE=passed")
    print(f"DOGFOOD_ROOT={root}")


def _run_pdf_legacy_retired(root: Path) -> dict[str, int]:
    vault = root / "vault"
    source = root / "paper.pdf"
    root.mkdir(parents=True)
    source.write_bytes(b"%PDF placeholder text pdf")

    init_vault(vault)
    result = run_m3_ingest_pipeline(vault, source)
    if result.status != "completed_with_issues" or result.searchable is not False:
        raise RuntimeError(f"PDF legacy retirement was not visible: {result}")
    if result.written_revisions != 0:
        raise RuntimeError(f"PDF legacy retirement wrote revisions: {result}")

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        document = connection.execute(
            """
            SELECT source_type, current_revision_id, original_path, ingest_status,
                   fts_status, quality_status, needs_review
            FROM documents
            """
        ).fetchone()
        summary = {
            "pdf_legacy_retired_documents": connection.execute(
                "SELECT COUNT(*) AS count FROM documents WHERE source_type = 'pdf'"
            ).fetchone()["count"],
            "pdf_legacy_retired_revisions": connection.execute(
                "SELECT COUNT(*) AS count FROM document_revisions"
            ).fetchone()["count"],
            "pdf_legacy_retired_chunks": connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()["count"],
            "pdf_legacy_retired_fts": connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()["count"],
            "pdf_legacy_retired_errors": connection.execute(
                "SELECT COUNT(*) AS count FROM errors WHERE error_type = 'legacy_conversion_retired'"
            ).fetchone()["count"],
            "pdf_legacy_retired_reviews": connection.execute(
                "SELECT COUNT(*) AS count FROM review_items WHERE type = 'conversion_low_quality'"
            ).fetchone()["count"],
            "pdf_legacy_retired_provider_runs": connection.execute(
                "SELECT COUNT(*) AS count FROM provider_runs"
            ).fetchone()["count"],
            "pdf_legacy_retired_search_results": search_chunks(connection, "PDF gate text needle").result_count,
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

    if document["source_type"] != "pdf":
        raise RuntimeError(f"PDF source_type not recorded: {document['source_type']}")
    if document["current_revision_id"] is not None or document["fts_status"] != "not_indexed":
        raise RuntimeError(f"PDF legacy failure became searchable: {dict(document)}")
    if document["ingest_status"] != "failed" or document["quality_status"] != "failed":
        raise RuntimeError(f"PDF legacy failure state was not explicit: {dict(document)}")
    if int(document["needs_review"]) != 1:
        raise RuntimeError(f"PDF legacy failure did not require review: {dict(document)}")
    if not str(document["original_path"]).endswith("/original.pdf"):
        raise RuntimeError(f"PDF original path was not preserved: {document['original_path']}")

    expected = {
        "pdf_legacy_retired_documents": 1,
        "pdf_legacy_retired_revisions": 0,
        "pdf_legacy_retired_chunks": 0,
        "pdf_legacy_retired_fts": 0,
        "pdf_legacy_retired_errors": 1,
        "pdf_legacy_retired_reviews": 1,
        "pdf_legacy_retired_provider_runs": 0,
        "pdf_legacy_retired_search_results": 0,
        "zero_chunk_current_revisions": 0,
    }
    for key, value in expected.items():
        if summary[key] != value:
            raise RuntimeError(f"unexpected PDF legacy-retired summary {key}: {summary}")
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
