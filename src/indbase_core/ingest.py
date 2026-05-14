"""Early ingest planning services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from indbase_core.archive import archive_pending_sources
from indbase_core.chunker import chunk_current_revision
from indbase_core.conversion import convert_archived_sources
from indbase_core.config import IngestConfig
from indbase_core.db import connect
from indbase_core.errors import record_error
from indbase_core.ids import new_prefixed_id
from indbase_core.indexer import FtsRebuildResult, rebuild_fts_index
from indbase_core.revisions import write_revisions_for_converted_sources
from indbase_core.reviews import create_review_item
from indbase_core.source_inspector import SourceInspection, scan_sources
from indbase_core.tasks import add_task_event, create_task, finish_task, start_task
from indbase_core.time import utc_now_iso


@dataclass(frozen=True)
class IngestPlanResult:
    ingest_id: str
    status: str
    total_items: int
    supported_items: int
    unsupported_items: int
    duplicate_items: int
    review_items_count: int
    inspections: tuple[SourceInspection, ...]


@dataclass(frozen=True)
class IngestPipelineResult:
    ingest_id: str
    task_id: str
    status: str
    total_items: int
    succeeded_items: int
    failed_items: int
    unsupported_items: int
    duplicate_items: int
    review_items_count: int
    written_revisions: int
    searchable: bool
    chunked_documents: int = 0
    indexed_documents: int = 0
    indexed_chunks: int = 0
    index_failed_documents: int = 0


def plan_ingest_sources(
    connection: sqlite3.Connection,
    source_input: Path | str,
    *,
    recursive: bool = False,
    ingest_config: IngestConfig | None = None,
    task_id: str | None = None,
) -> IngestPlanResult:
    inspections = tuple(
        scan_sources(source_input, recursive=recursive, ingest_config=ingest_config)
    )
    source_kind = "file" if Path(source_input).is_file() else "folder"
    ingest_id = new_prefixed_id("ingest")
    now = utc_now_iso()

    existing_by_hash = _existing_doc_ids_by_source_hash(connection, inspections)
    existing_by_uri = _existing_doc_ids_by_normalized_source_uri(connection, inspections)
    duplicate_count = sum(1 for inspection in inspections if inspection.source_hash in existing_by_hash)
    supported_count = sum(
        1
        for inspection in inspections
        if inspection.is_supported and inspection.source_hash not in existing_by_hash
    )
    unsupported_count = len(inspections) - supported_count
    unsupported_count -= duplicate_count
    review_count = unsupported_count
    status = _initial_ingest_status(supported_count, unsupported_count)

    connection.execute(
        """
        INSERT INTO ingest_runs(
          ingest_id, source_kind, source_input, status, total_items, succeeded_items,
          failed_items, unsupported_items, duplicate_items, review_items_count,
          task_id,
          created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?)
        """,
        (
            ingest_id,
            source_kind,
            str(source_input),
            status,
            len(inspections),
            unsupported_count,
            duplicate_count,
            review_count,
            task_id,
            now,
            now,
        ),
    )

    for inspection in inspections:
        ingest_item_id = new_prefixed_id("ingest_item")
        existing_doc_id = existing_by_hash.get(inspection.source_hash)
        if existing_doc_id is not None:
            item_status = "duplicate"
            item_doc_id = existing_doc_id
        elif inspection.is_supported:
            item_status = "pending"
            item_doc_id = existing_by_uri.get(inspection.normalized_source_uri)
        else:
            item_status = "unsupported"
            item_doc_id = None
        connection.execute(
            """
            INSERT INTO ingest_items(
              ingest_item_id, ingest_id, doc_id, source_uri, normalized_source_uri,
              status, created_at, updated_at, finished_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ingest_item_id,
                ingest_id,
                item_doc_id,
                inspection.source_uri,
                inspection.normalized_source_uri,
                item_status,
                now,
                now,
                now if item_status in {"unsupported", "duplicate"} else None,
            ),
        )
        if item_status == "unsupported":
            create_review_item(
                connection,
                review_type="unsupported_source",
                target_type="ingest_item",
                target_id=ingest_item_id,
                reason=f"Unsupported source extension: .{inspection.original_ext}",
                priority=50,
            )

    connection.commit()
    return IngestPlanResult(
        ingest_id=ingest_id,
        status=status,
        total_items=len(inspections),
        supported_items=supported_count,
        unsupported_items=unsupported_count,
        duplicate_items=duplicate_count,
        review_items_count=review_count,
        inspections=inspections,
    )


def run_m2_ingest_pipeline(
    vault_path: Path | str,
    source_input: Path | str,
    *,
    recursive: bool = False,
    ingest_config: IngestConfig | None = None,
) -> IngestPipelineResult:
    connection = connect(Path(vault_path) / ".indbase" / "db.sqlite")
    try:
        task_id = create_task(
            connection,
            "ingest",
            input_data={
                "source_input": str(source_input),
                "recursive": recursive,
                "m2_revision_only": True,
            },
        )
        start_task(connection, task_id)
        add_task_event(connection, task_id, "ingest_started", "M2 revision ingest started.")
        plan = plan_ingest_sources(
            connection,
            source_input,
            recursive=recursive,
            ingest_config=ingest_config,
            task_id=task_id,
        )
        add_task_event(
            connection,
            task_id,
            "sources_inspected",
            "Sources inspected and classified.",
            {
                "total_items": plan.total_items,
                "supported_items": plan.supported_items,
                "unsupported_items": plan.unsupported_items,
                "duplicate_items": plan.duplicate_items,
            },
        )
        archive_result = archive_pending_sources(connection, vault_path, plan.ingest_id)
        add_task_event(
            connection,
            task_id,
            "originals_archived",
            "Supported originals archived.",
            {
                "archived_items": len(archive_result.archived_items),
                "failed_items": archive_result.failed_items,
            },
        )
        conversion_result = convert_archived_sources(connection, vault_path, plan.ingest_id)
        add_task_event(
            connection,
            task_id,
            "sources_converted",
            "Archived sources converted to Markdown candidates.",
            {
                "converted_items": len(conversion_result.converted_items),
                "skipped_items": conversion_result.skipped_items,
                "failed_items": conversion_result.failed_items,
            },
        )
        revision_result = write_revisions_for_converted_sources(connection, vault_path, plan.ingest_id)
        add_task_event(
            connection,
            task_id,
            "revisions_written",
            "Immutable source Markdown revisions written.",
            {
                "written_revisions": len(revision_result.written_revisions),
                "failed_items": revision_result.failed_items,
            },
        )
        _finalize_m2_ingest_run(connection, plan.ingest_id)
        result = _load_pipeline_result(connection, plan.ingest_id, task_id)
        finish_task(
            connection,
            task_id,
            result.status,
            result_data={
                "ingest_id": result.ingest_id,
                "total_items": result.total_items,
                "succeeded_items": result.succeeded_items,
                "failed_items": result.failed_items,
                "unsupported_items": result.unsupported_items,
                "duplicate_items": result.duplicate_items,
                "review_items_count": result.review_items_count,
                "written_revisions": result.written_revisions,
                "searchable": result.searchable,
                "note": "M2 ingest writes revisions only; chunks and FTS are added in M3.",
            },
        )
        return result
    except Exception as exc:
        if "task_id" in locals():
            finish_task(
                connection,
                task_id,
                "failed",
                error_data={"type": type(exc).__name__, "message": str(exc)},
            )
        raise
    finally:
        connection.close()


def run_m3_ingest_pipeline(
    vault_path: Path | str,
    source_input: Path | str,
    *,
    recursive: bool = False,
    ingest_config: IngestConfig | None = None,
) -> IngestPipelineResult:
    connection = connect(Path(vault_path) / ".indbase" / "db.sqlite")
    try:
        task_id = create_task(
            connection,
            "ingest",
            input_data={
                "source_input": str(source_input),
                "recursive": recursive,
                "m3_searchable": True,
            },
        )
        start_task(connection, task_id)
        add_task_event(connection, task_id, "ingest_started", "M3 searchable ingest started.")
        plan = plan_ingest_sources(
            connection,
            source_input,
            recursive=recursive,
            ingest_config=ingest_config,
            task_id=task_id,
        )
        add_task_event(
            connection,
            task_id,
            "sources_inspected",
            "Sources inspected and classified.",
            {
                "total_items": plan.total_items,
                "supported_items": plan.supported_items,
                "unsupported_items": plan.unsupported_items,
                "duplicate_items": plan.duplicate_items,
            },
        )
        archive_result = archive_pending_sources(connection, vault_path, plan.ingest_id)
        add_task_event(
            connection,
            task_id,
            "originals_archived",
            "Supported originals archived.",
            {
                "archived_items": len(archive_result.archived_items),
                "failed_items": archive_result.failed_items,
            },
        )
        conversion_result = convert_archived_sources(connection, vault_path, plan.ingest_id)
        add_task_event(
            connection,
            task_id,
            "sources_converted",
            "Archived sources converted to Markdown candidates.",
            {
                "converted_items": len(conversion_result.converted_items),
                "skipped_items": conversion_result.skipped_items,
                "failed_items": conversion_result.failed_items,
            },
        )
        revision_result = write_revisions_for_converted_sources(connection, vault_path, plan.ingest_id)
        add_task_event(
            connection,
            task_id,
            "revisions_written",
            "Immutable source Markdown revisions written.",
            {
                "written_revisions": len(revision_result.written_revisions),
                "failed_items": revision_result.failed_items,
            },
        )
        chunked_documents = _chunk_written_revisions(connection, vault_path, revision_result.written_revisions)
        add_task_event(
            connection,
            task_id,
            "chunks_written",
            "Current revisions chunked.",
            {
                "chunked_documents": chunked_documents,
            },
        )
        index_result = rebuild_fts_index(connection, vault_path)
        current_index_failed_documents = _mark_current_ingest_index_failures(
            connection,
            plan.ingest_id,
            index_result,
        )
        add_task_event(
            connection,
            task_id,
            "fts_rebuilt",
            "SQLite FTS index rebuilt.",
            {
                "indexed_documents": index_result.indexed_documents,
                "indexed_chunks": index_result.indexed_chunks,
                "failed_documents": index_result.failed_documents,
                "current_ingest_failed_documents": current_index_failed_documents,
            },
        )
        _finalize_m3_ingest_run(connection, plan.ingest_id)
        result = _load_pipeline_result(
            connection,
            plan.ingest_id,
            task_id,
            chunked_documents=chunked_documents,
            indexed_documents=index_result.indexed_documents,
            indexed_chunks=index_result.indexed_chunks,
            index_failed_documents=current_index_failed_documents,
        )
        finish_status = result.status
        if finish_status == "succeeded" and current_index_failed_documents > 0:
            finish_status = "completed_with_issues"
        finish_task(
            connection,
            task_id,
            finish_status,
            result_data={
                "ingest_id": result.ingest_id,
                "total_items": result.total_items,
                "succeeded_items": result.succeeded_items,
                "failed_items": result.failed_items,
                "unsupported_items": result.unsupported_items,
                "duplicate_items": result.duplicate_items,
                "review_items_count": result.review_items_count,
                "written_revisions": result.written_revisions,
                "chunked_documents": result.chunked_documents,
                "indexed_documents": result.indexed_documents,
                "indexed_chunks": result.indexed_chunks,
                "index_failed_documents": result.index_failed_documents,
                "searchable": result.searchable,
            },
        )
        if finish_status != result.status:
            return IngestPipelineResult(
                ingest_id=result.ingest_id,
                task_id=result.task_id,
                status=finish_status,
                total_items=result.total_items,
                succeeded_items=result.succeeded_items,
                failed_items=result.failed_items,
                unsupported_items=result.unsupported_items,
                duplicate_items=result.duplicate_items,
                review_items_count=result.review_items_count,
                written_revisions=result.written_revisions,
                searchable=result.searchable,
                chunked_documents=result.chunked_documents,
                indexed_documents=result.indexed_documents,
                indexed_chunks=result.indexed_chunks,
                index_failed_documents=result.index_failed_documents,
            )
        return result
    except Exception as exc:
        if "task_id" in locals():
            finish_task(
                connection,
                task_id,
                "failed",
                error_data={"type": type(exc).__name__, "message": str(exc)},
            )
        raise
    finally:
        connection.close()


def _initial_ingest_status(supported_count: int, unsupported_count: int) -> str:
    if supported_count == 0 and unsupported_count > 0:
        return "completed_with_issues"
    if unsupported_count > 0:
        return "pending"
    return "pending"


def _existing_doc_ids_by_source_hash(
    connection: sqlite3.Connection,
    inspections: tuple[SourceInspection, ...],
) -> dict[str, str]:
    hashes = sorted({inspection.source_hash for inspection in inspections if inspection.is_supported})
    if not hashes:
        return {}
    placeholders = ", ".join("?" for _ in hashes)
    rows = connection.execute(
        f"""
        SELECT source_hash, doc_id
        FROM source_files
        WHERE source_hash IN ({placeholders})
        ORDER BY created_at
        """,
        hashes,
    ).fetchall()
    existing: dict[str, str] = {}
    for row in rows:
        existing.setdefault(str(row["source_hash"]), str(row["doc_id"]))
    return existing


def _existing_doc_ids_by_normalized_source_uri(
    connection: sqlite3.Connection,
    inspections: tuple[SourceInspection, ...],
) -> dict[str, str]:
    uris = sorted({inspection.normalized_source_uri for inspection in inspections if inspection.is_supported})
    if not uris:
        return {}
    placeholders = ", ".join("?" for _ in uris)
    rows = connection.execute(
        f"""
        SELECT normalized_source_uri, doc_id
        FROM documents
        WHERE normalized_source_uri IN ({placeholders})
          AND deleted_at IS NULL
        ORDER BY created_at
        """,
        uris,
    ).fetchall()
    existing: dict[str, str] = {}
    for row in rows:
        existing.setdefault(str(row["normalized_source_uri"]), str(row["doc_id"]))
    return existing


def _finalize_m2_ingest_run(connection: sqlite3.Connection, ingest_id: str) -> None:
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE ingest_items
        SET status = 'succeeded', finished_at = ?, updated_at = ?
        WHERE ingest_id = ?
          AND status = 'running'
          AND doc_id IN (
            SELECT doc_id
            FROM documents
            WHERE current_revision_id IS NOT NULL
              AND ingest_status = 'revisioned'
          )
        """,
        (now, now, ingest_id),
    )
    counts = connection.execute(
        """
        SELECT
          COUNT(*) AS total_items,
          SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded_items,
          SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_items,
          SUM(CASE WHEN status = 'unsupported' THEN 1 ELSE 0 END) AS unsupported_items,
          SUM(CASE WHEN status = 'duplicate' THEN 1 ELSE 0 END) AS duplicate_items
        FROM ingest_items
        WHERE ingest_id = ?
        """,
        (ingest_id,),
    ).fetchone()
    review_count = _count_review_items_for_ingest(connection, ingest_id)
    total_items = int(counts["total_items"] or 0)
    succeeded_items = int(counts["succeeded_items"] or 0)
    failed_items = int(counts["failed_items"] or 0)
    unsupported_items = int(counts["unsupported_items"] or 0)
    duplicate_items = int(counts["duplicate_items"] or 0)
    has_issues = failed_items > 0 or unsupported_items > 0 or duplicate_items > 0 or int(review_count or 0) > 0
    status = "completed_with_issues" if has_issues else "succeeded"
    connection.execute(
        """
        UPDATE ingest_runs
        SET status = ?, total_items = ?, succeeded_items = ?, failed_items = ?,
            unsupported_items = ?, duplicate_items = ?, review_items_count = ?,
            finished_at = ?, updated_at = ?
        WHERE ingest_id = ?
        """,
        (
            status,
            total_items,
            succeeded_items,
            failed_items,
            unsupported_items,
            duplicate_items,
            int(review_count or 0),
            now,
            now,
            ingest_id,
        ),
    )
    connection.commit()


def _finalize_m3_ingest_run(connection: sqlite3.Connection, ingest_id: str) -> None:
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE ingest_items
        SET status = 'succeeded', finished_at = ?, updated_at = ?
        WHERE ingest_id = ?
          AND status = 'running'
          AND doc_id IN (
            SELECT d.doc_id
            FROM documents d
            WHERE d.current_revision_id IS NOT NULL
              AND d.ingest_status = 'revisioned'
              AND d.fts_status = 'indexed'
              AND EXISTS (
                SELECT 1
                FROM chunks c
                WHERE c.doc_id = d.doc_id
                  AND c.revision_id = d.current_revision_id
                  AND c.is_current = 1
                  AND c.deleted_at IS NULL
              )
          )
        """,
        (now, now, ingest_id),
    )
    _write_ingest_run_counts(connection, ingest_id)


def _write_ingest_run_counts(connection: sqlite3.Connection, ingest_id: str) -> None:
    now = utc_now_iso()
    counts = connection.execute(
        """
        SELECT
          COUNT(*) AS total_items,
          SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded_items,
          SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_items,
          SUM(CASE WHEN status = 'unsupported' THEN 1 ELSE 0 END) AS unsupported_items,
          SUM(CASE WHEN status = 'duplicate' THEN 1 ELSE 0 END) AS duplicate_items
        FROM ingest_items
        WHERE ingest_id = ?
        """,
        (ingest_id,),
    ).fetchone()
    review_count = _count_review_items_for_ingest(connection, ingest_id)
    total_items = int(counts["total_items"] or 0)
    succeeded_items = int(counts["succeeded_items"] or 0)
    failed_items = int(counts["failed_items"] or 0)
    unsupported_items = int(counts["unsupported_items"] or 0)
    duplicate_items = int(counts["duplicate_items"] or 0)
    has_issues = failed_items > 0 or unsupported_items > 0 or duplicate_items > 0 or int(review_count or 0) > 0
    status = "completed_with_issues" if has_issues else "succeeded"
    connection.execute(
        """
        UPDATE ingest_runs
        SET status = ?, total_items = ?, succeeded_items = ?, failed_items = ?,
            unsupported_items = ?, duplicate_items = ?, review_items_count = ?,
            finished_at = ?, updated_at = ?
        WHERE ingest_id = ?
        """,
        (
            status,
            total_items,
            succeeded_items,
            failed_items,
            unsupported_items,
            duplicate_items,
            int(review_count or 0),
            now,
            now,
            ingest_id,
        ),
    )
    connection.commit()


def _count_review_items_for_ingest(connection: sqlite3.Connection, ingest_id: str) -> int:
    row = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM review_items ri
        WHERE (
          ri.target_type = 'ingest_item'
          AND ri.target_id IN (
            SELECT ingest_item_id
            FROM ingest_items
            WHERE ingest_id = ?
          )
        )
        OR (
          ri.target_type = 'converter_run'
          AND ri.target_id IN (
            SELECT cr.converter_run_id
            FROM converter_runs cr
            JOIN ingest_items ii ON ii.doc_id = cr.doc_id
            JOIN ingest_runs ir ON ir.ingest_id = ii.ingest_id
            WHERE ii.ingest_id = ?
              AND ii.status = 'failed'
              AND cr.created_at >= ir.created_at
          )
        )
        OR (
          ri.target_type = 'document'
          AND ri.target_id IN (
            SELECT doc_id
            FROM ingest_items
            WHERE ingest_id = ?
          )
        )
        """,
        (ingest_id, ingest_id, ingest_id),
    ).fetchone()
    return int(row["count"] or 0)


def _chunk_written_revisions(connection: sqlite3.Connection, vault_path: Path | str, written_revisions: tuple) -> int:
    chunked = 0
    for written in written_revisions:
        try:
            chunk_current_revision(connection, vault_path, written.doc_id)
            chunked += 1
        except Exception as exc:
            _mark_chunking_failed(connection, written.ingest_item_id, written.doc_id, exc)
    return chunked


def _mark_chunking_failed(
    connection: sqlite3.Connection,
    ingest_item_id: str,
    doc_id: str,
    exc: Exception,
) -> None:
    now = utc_now_iso()
    error_id = record_error(
        connection,
        component="chunker",
        error_type=type(exc).__name__,
        message=str(exc),
        user_message="Failed to chunk source Markdown revision.",
        retryable=True,
        payload={"ingest_item_id": ingest_item_id, "doc_id": doc_id},
    )
    connection.execute(
        """
        UPDATE ingest_items
        SET status = 'failed', error_id = ?, updated_at = ?, finished_at = ?
        WHERE ingest_item_id = ?
        """,
        (error_id, now, now, ingest_item_id),
    )
    connection.execute(
        """
        UPDATE documents
        SET ingest_status = 'failed', fts_status = 'failed', updated_at = ?
        WHERE doc_id = ?
        """,
        (now, doc_id),
    )
    create_review_item(
        connection,
        review_type="chunking_failed",
        target_type="document",
        target_id=doc_id,
        reason=f"Chunking failed: {exc}",
        priority=30,
    )
    connection.commit()


def _mark_current_ingest_index_failures(
    connection: sqlite3.Connection,
    ingest_id: str,
    index_result: FtsRebuildResult,
) -> int:
    marked = 0
    for failure in index_result.failures:
        now = utc_now_iso()
        cursor = connection.execute(
            """
            UPDATE ingest_items
            SET status = 'failed', error_id = ?, updated_at = ?, finished_at = ?
            WHERE ingest_id = ?
              AND doc_id = ?
              AND status = 'running'
            """,
            (failure.error_id, now, now, ingest_id, failure.doc_id),
        )
        marked += cursor.rowcount
    connection.commit()
    return marked


def _load_pipeline_result(
    connection: sqlite3.Connection,
    ingest_id: str,
    task_id: str,
    *,
    chunked_documents: int = 0,
    indexed_documents: int = 0,
    indexed_chunks: int = 0,
    index_failed_documents: int = 0,
) -> IngestPipelineResult:
    row = connection.execute(
        """
        SELECT status, total_items, succeeded_items, failed_items, unsupported_items,
               duplicate_items, review_items_count
        FROM ingest_runs
        WHERE ingest_id = ?
        """,
        (ingest_id,),
    ).fetchone()
    written_revisions = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM ingest_items ii
        JOIN ingest_runs ir ON ir.ingest_id = ii.ingest_id
        JOIN converter_runs cr ON cr.doc_id = ii.doc_id
        WHERE ii.ingest_id = ?
          AND ii.status = 'succeeded'
          AND cr.status = 'succeeded'
          AND cr.revision_id IS NOT NULL
          AND cr.created_at >= ir.created_at
        """,
        (ingest_id,),
    ).fetchone()["count"]
    searchable = _ingest_successful_items_are_searchable(connection, ingest_id)
    return IngestPipelineResult(
        ingest_id=ingest_id,
        task_id=task_id,
        status=str(row["status"]),
        total_items=int(row["total_items"] or 0),
        succeeded_items=int(row["succeeded_items"] or 0),
        failed_items=int(row["failed_items"] or 0),
        unsupported_items=int(row["unsupported_items"] or 0),
        duplicate_items=int(row["duplicate_items"] or 0),
        review_items_count=int(row["review_items_count"] or 0),
        written_revisions=int(written_revisions or 0),
        searchable=searchable,
        chunked_documents=chunked_documents,
        indexed_documents=indexed_documents,
        indexed_chunks=indexed_chunks,
        index_failed_documents=index_failed_documents,
    )


def _ingest_successful_items_are_searchable(connection: sqlite3.Connection, ingest_id: str) -> bool:
    row = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM ingest_items ii
        JOIN documents d ON d.doc_id = ii.doc_id
        WHERE ii.ingest_id = ?
          AND ii.status = 'succeeded'
          AND (
            d.fts_status != 'indexed'
            OR d.current_revision_id IS NULL
            OR NOT EXISTS (
              SELECT 1
              FROM chunks c
              WHERE c.doc_id = d.doc_id
                AND c.revision_id = d.current_revision_id
                AND c.is_current = 1
                AND c.deleted_at IS NULL
            )
          )
        """,
        (ingest_id,),
    ).fetchone()
    succeeded = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM ingest_items
        WHERE ingest_id = ?
          AND status = 'succeeded'
        """,
        (ingest_id,),
    ).fetchone()
    return int(succeeded["count"] or 0) > 0 and int(row["count"] or 0) == 0
