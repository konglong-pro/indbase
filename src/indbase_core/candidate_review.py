"""Review actions for conversion candidates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from indbase_core.chunker import chunk_current_revision
from indbase_core.indexer import rebuild_fts_index
from indbase_core.reviews import get_review_item, resolve_review_item
from indbase_core.revisions import write_revision_for_converter_run
from indbase_core.time import utc_now_iso


@dataclass(frozen=True)
class CandidateReviewActionResult:
    review_id: str
    converter_run_id: str
    action: str
    doc_id: str | None = None
    revision_id: str | None = None
    chunked_documents: int = 0
    indexed_documents: int = 0
    indexed_chunks: int = 0


def accept_conversion_candidate_review(
    connection: sqlite3.Connection,
    vault_path: Path | str,
    review_id: str,
    *,
    note: str | None = None,
    resolved_by: str | None = None,
) -> CandidateReviewActionResult:
    converter_run_id = _converter_run_for_review(connection, review_id)
    now = utc_now_iso()
    row = connection.execute(
        "SELECT doc_id FROM converter_runs WHERE converter_run_id = ?",
        (converter_run_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Pending conversion candidate not found: {converter_run_id}")
    connection.execute(
        """
        UPDATE documents
        SET needs_review = 0, quality_status = 'passed', updated_at = ?
        WHERE doc_id = ?
        """,
        (now, row["doc_id"]),
    )
    written = write_revision_for_converter_run(connection, vault_path, converter_run_id)
    chunk_current_revision(connection, vault_path, written.doc_id)
    index_result = rebuild_fts_index(connection, vault_path)
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE converter_runs
        SET status = 'succeeded',
            promotion_status = 'promoted',
            promotion_reason = ?,
            updated_at = ?
        WHERE converter_run_id = ?
        """,
        (
            note or "Conversion candidate accepted from review.",
            now,
            converter_run_id,
        ),
    )
    connection.execute(
        """
        UPDATE ingest_items
        SET status = 'succeeded', updated_at = ?, finished_at = COALESCE(finished_at, ?)
        WHERE doc_id = ? AND status = 'pending_review'
        """,
        (now, now, written.doc_id),
    )
    connection.execute(
        """
        UPDATE documents
        SET needs_review = 0, quality_status = 'passed', updated_at = ?
        WHERE doc_id = ?
        """,
        (now, written.doc_id),
    )
    resolve_review_item(connection, review_id, note=note, resolved_by=resolved_by)
    connection.commit()
    return CandidateReviewActionResult(
        review_id=review_id,
        converter_run_id=converter_run_id,
        action="accepted",
        doc_id=written.doc_id,
        revision_id=written.revision_id,
        chunked_documents=1,
        indexed_documents=index_result.indexed_documents,
        indexed_chunks=index_result.indexed_chunks,
    )


def reject_conversion_candidate_review(
    connection: sqlite3.Connection,
    review_id: str,
    *,
    note: str | None = None,
    resolved_by: str | None = None,
) -> CandidateReviewActionResult:
    converter_run_id = _converter_run_for_review(connection, review_id)
    row = connection.execute(
        """
        SELECT cr.doc_id, d.current_revision_id
        FROM converter_runs cr
        JOIN documents d ON d.doc_id = cr.doc_id
        WHERE cr.converter_run_id = ?
          AND cr.status = 'pending_review'
        """,
        (converter_run_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Pending conversion candidate not found: {converter_run_id}")

    now = utc_now_iso()
    doc_id = str(row["doc_id"])
    connection.execute(
        """
        UPDATE converter_runs
        SET status = 'rejected',
            promotion_status = 'rejected',
            promotion_reason = ?,
            updated_at = ?
        WHERE converter_run_id = ?
        """,
        (
            note or "Conversion candidate rejected from review.",
            now,
            converter_run_id,
        ),
    )
    connection.execute(
        """
        UPDATE ingest_items
        SET status = 'rejected', updated_at = ?, finished_at = COALESCE(finished_at, ?)
        WHERE doc_id = ? AND status = 'pending_review'
        """,
        (now, now, doc_id),
    )
    connection.execute(
        """
        UPDATE documents
        SET ingest_status = CASE
                WHEN current_revision_id IS NULL THEN 'failed'
                ELSE 'revisioned'
            END,
            needs_review = 0,
            quality_status = CASE
                WHEN current_revision_id IS NULL THEN 'failed'
                ELSE quality_status
            END,
            updated_at = ?
        WHERE doc_id = ?
        """,
        (now, doc_id),
    )
    resolve_review_item(connection, review_id, note=note, resolved_by=resolved_by)
    connection.commit()
    return CandidateReviewActionResult(
        review_id=review_id,
        converter_run_id=converter_run_id,
        action="rejected",
        doc_id=doc_id,
        revision_id=None,
    )


def _converter_run_for_review(connection: sqlite3.Connection, review_id: str) -> str:
    review = get_review_item(connection, review_id)
    if review is None:
        raise ValueError(f"Review item not found: {review_id}")
    if review["status"] == "resolved":
        raise ValueError(f"Review item is already resolved: {review_id}")
    if review["type"] != "conversion_pending_review" or review["target_type"] != "converter_run":
        raise ValueError(f"Review item is not a conversion candidate review: {review_id}")
    return str(review["target_id"])
