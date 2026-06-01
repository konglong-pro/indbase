"""Early ingest planning services."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import mimetypes
from pathlib import Path
import re
import shutil
import sqlite3
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse, urlunparse

from indbase_core.archive import archive_pending_sources
from indbase_core.chunker import chunk_current_revision
from indbase_core.conversion import convert_archived_sources, hash_markdown
from indbase_core.config import IndbaseConfig, IngestConfig, default_config, load_config
from indbase_core.db import connect
from indbase_core.errors import record_error
from indbase_core.ids import new_doc_id, new_prefixed_id
from indbase_core.indexer import FtsRebuildResult, rebuild_fts_index
from indbase_core.paths import normalize_source_uri, slugify, vault_paths
from indbase_core.promotion_policy import evaluate_swallow_promotion
from indbase_core.revisions import write_revisions_for_converted_sources
from indbase_core.reviews import create_review_item
from indbase_core.source_inspector import SourceInspection, hash_file, scan_sources
from indbase_core.swallow_adapter import (
    ArchiveExpansionCandidate,
    ArchiveLogicalDocumentCandidate,
    ConversionCandidate,
    SwallowIngestAdapter,
    artifact_manifest_json,
    candidate_to_quality_signals,
)
from indbase_core.tasks import add_task_event, create_task, finish_task, start_task
from indbase_core.time import utc_now_iso

M3_PIPELINE_CHECKPOINT_STAGES = (
    "plan",
    "archive",
    "conversion",
    "revision",
    "chunk",
    "index",
    "finalize",
)


class IngestCheckpointCancelled(Exception):
    def __init__(self, checkpoint: str, original: BaseException) -> None:
        self.checkpoint = checkpoint
        self.original = original
        super().__init__(str(original))


def _cancelled_checkpoint(exc: BaseException) -> str | None:
    checkpoint = getattr(exc, "checkpoint", None)
    return checkpoint if isinstance(checkpoint, str) and checkpoint else None


def _invoke_checkpoint(checkpoint: Callable[[str], None] | None, stage: str) -> None:
    if checkpoint is None:
        return
    try:
        checkpoint(stage)
    except BaseException as exc:
        cancelled_checkpoint = _cancelled_checkpoint(exc)
        if cancelled_checkpoint is not None:
            raise IngestCheckpointCancelled(cancelled_checkpoint, exc) from exc
        raise


def _finish_pipeline_task_on_error(
    connection: sqlite3.Connection,
    task_id: str,
    exc: BaseException,
) -> None:
    if isinstance(exc, IngestCheckpointCancelled):
        checkpoint = exc.checkpoint
        original = exc.original
        add_task_event(
            connection,
            task_id,
            "ingest_cancelled",
            "Ingest cancelled at cooperative checkpoint.",
            {"checkpoint": checkpoint},
        )
        finish_task(
            connection,
            task_id,
            "cancelled",
            error_data={
                "type": original.__class__.__name__,
                "message": str(original),
                "checkpoint": checkpoint,
            },
        )
        return
    finish_task(
        connection,
        task_id,
        "failed",
        error_data={"type": type(exc).__name__, "message": str(exc)},
    )


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
    resolved_ingest_config = _resolve_ingest_config(vault_path, ingest_config)
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
            ingest_config=resolved_ingest_config,
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
        archive_result = archive_pending_sources(
            connection,
            vault_path,
            plan.ingest_id,
            ingest_config=resolved_ingest_config,
        )
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
    checkpoint: Callable[[str], None] | None = None,
) -> IngestPipelineResult:
    resolved_ingest_config = _resolve_ingest_config(vault_path, ingest_config)
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
        _invoke_checkpoint(checkpoint, "plan")
        plan = plan_ingest_sources(
            connection,
            source_input,
            recursive=recursive,
            ingest_config=resolved_ingest_config,
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
        _invoke_checkpoint(checkpoint, "archive")
        archive_result = archive_pending_sources(
            connection,
            vault_path,
            plan.ingest_id,
            ingest_config=resolved_ingest_config,
        )
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
        _invoke_checkpoint(checkpoint, "conversion")
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
        _invoke_checkpoint(checkpoint, "revision")
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
        _invoke_checkpoint(checkpoint, "chunk")
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
        _invoke_checkpoint(checkpoint, "index")
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
        _invoke_checkpoint(checkpoint, "finalize")
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
            _finish_pipeline_task_on_error(connection, task_id, exc)
        if isinstance(exc, IngestCheckpointCancelled):
            raise exc.original from exc
        raise
    finally:
        connection.close()


def run_m3_url_ingest_pipeline(
    vault_path: Path | str,
    url: str,
) -> IngestPipelineResult:
    indbase_config = _load_indbase_config(vault_path)
    _validate_url_ingest_enabled(indbase_config)
    connection = connect(Path(vault_path) / ".indbase" / "db.sqlite")
    try:
        task_id = create_task(
            connection,
            "ingest",
            input_data={
                "source_input": url,
                "source_type": "url",
                "capture": "playwright",
                "m3_searchable": True,
            },
        )
        start_task(connection, task_id)
        add_task_event(connection, task_id, "ingest_started", "M3 URL Playwright ingest started.")
        plan = _create_url_ingest_source(connection, vault_path, url, task_id=task_id)
        add_task_event(
            connection,
            task_id,
            "sources_inspected",
            "URL source normalized and staged for local Playwright snapshot.",
            {
                "total_items": plan.total_items,
                "supported_items": plan.supported_items,
                "unsupported_items": plan.unsupported_items,
                "duplicate_items": plan.duplicate_items,
            },
        )
        add_task_event(
            connection,
            task_id,
            "originals_archived",
            "URL reference archived; Playwright snapshot will be captured by swallow.",
            {
                "archived_items": 1,
                "failed_items": 0,
            },
        )
        conversion_result = convert_archived_sources(connection, vault_path, plan.ingest_id)
        add_task_event(
            connection,
            task_id,
            "sources_converted",
            "URL captured with Playwright and converted to Markdown candidate.",
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


def run_m3_archive_ingest_pipeline(
    vault_path: Path | str,
    archive_path: Path | str,
) -> IngestPipelineResult:
    indbase_config = _load_indbase_config(vault_path)
    _validate_archive_ingest_enabled(indbase_config)
    source_path = Path(archive_path).resolve(strict=False)
    if not source_path.is_file():
        raise ValueError(f"Archive file not found: {source_path}")

    connection = connect(Path(vault_path) / ".indbase" / "db.sqlite")
    try:
        task_id = create_task(
            connection,
            "ingest",
            input_data={
                "source_input": str(archive_path),
                "source_type": "archive",
                "expansion": "one_to_many",
                "m3_searchable": True,
            },
        )
        start_task(connection, task_id)
        add_task_event(connection, task_id, "ingest_started", "M3 archive one-to-many ingest started.")

        paths = vault_paths(vault_path)
        adapter = SwallowIngestAdapter(vault_path=paths.root, config=indbase_config.ingest.swallow)
        expansion = adapter.expand_archive(source_path)
        if not expansion.logical_documents:
            raise ValueError("Archive conversion produced no logical documents.")
        add_task_event(
            connection,
            task_id,
            "archive_expanded",
            "Archive expanded into logical documents by swallow.",
            {
                "archive_type": expansion.archive_type,
                "logical_documents": len(expansion.logical_documents),
            },
        )

        plan = _create_archive_ingest_sources(
            connection,
            vault_path,
            source_path,
            expansion,
            indbase_config=indbase_config,
            task_id=task_id,
        )
        add_task_event(
            connection,
            task_id,
            "sources_converted",
            "Archive logical documents staged as Markdown candidates.",
            {
                "converted_items": plan.supported_items,
                "review_items": plan.review_items_count,
                "failed_items": 0,
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
            error_id = record_error(
                connection,
                component="archive_ingest",
                error_type=type(exc).__name__,
                message=str(exc),
                task_id=task_id,
                user_message="Failed to expand archive into indbase logical documents.",
                retryable=False,
                payload={"source_input": str(archive_path)},
            )
            finish_task(
                connection,
                task_id,
                "failed",
                error_data={"type": type(exc).__name__, "message": str(exc), "error_id": error_id},
            )
            connection.commit()
        raise
    finally:
        connection.close()


def _resolve_ingest_config(vault_path: Path | str, ingest_config: IngestConfig | None) -> IngestConfig:
    if ingest_config is not None:
        return ingest_config
    config_path = Path(vault_path) / ".indbase" / "config" / "config.toml"
    if config_path.is_file():
        return load_config(config_path).ingest
    return default_config(vault_path).ingest


def _load_indbase_config(vault_path: Path | str) -> IndbaseConfig:
    config_path = Path(vault_path) / ".indbase" / "config" / "config.toml"
    if config_path.is_file():
        return load_config(config_path)
    return default_config(vault_path)


def _validate_url_ingest_enabled(config: IndbaseConfig) -> None:
    if not config.features.swallow_ingest:
        raise ValueError("URL ingest requires features.swallow_ingest = true.")
    if not config.features.web_ingest:
        raise ValueError("URL ingest requires features.web_ingest = true.")


def _validate_archive_ingest_enabled(config: IndbaseConfig) -> None:
    if not config.features.swallow_ingest:
        raise ValueError("Archive ingest requires features.swallow_ingest = true.")


def _create_url_ingest_source(
    connection: sqlite3.Connection,
    vault_path: Path | str,
    url: str,
    *,
    task_id: str,
) -> IngestPlanResult:
    paths = vault_paths(vault_path)
    normalized_url = _normalize_url_source_uri(url)
    ingest_id = new_prefixed_id("ingest")
    ingest_item_id = new_prefixed_id("ingest_item")
    source_file_id = new_prefixed_id("source_file")
    now = utc_now_iso()
    existing_doc_id = _existing_doc_id_for_normalized_uri(connection, normalized_url)
    doc_id = existing_doc_id or new_doc_id()
    payload = _url_reference_payload(url, normalized_url)
    source_hash = _hash_bytes(payload)
    original_abs = _available_url_reference_path(paths, doc_id, source_file_id)
    original_abs.parent.mkdir(parents=True, exist_ok=True)
    original_abs.write_bytes(payload)
    original_rel = paths.relative_to_vault(original_abs)
    title = _title_from_url(normalized_url)
    filename_slug = slugify(title)

    connection.execute(
        """
        INSERT INTO ingest_runs(
          ingest_id, source_kind, source_input, status, total_items, succeeded_items,
          failed_items, unsupported_items, duplicate_items, review_items_count,
          task_id, created_at, updated_at
        )
        VALUES (?, 'url', ?, 'pending', 1, 0, 0, 0, 0, 0, ?, ?, ?)
        """,
        (ingest_id, url, task_id, now, now),
    )
    if existing_doc_id is None:
        connection.execute(
            """
            INSERT INTO documents(
              doc_id, current_revision_id, title, original_title, filename_slug,
              status, source_type, source_uri, normalized_source_uri, source_hash,
              canonical_path, original_path, language, category_id, quality_status,
              quality_signals_json, needs_review, ingest_status, fts_status,
              embedding_status, classification_status, access_context,
              privacy_flags_json, source_snapshot_path, created_at, updated_at
            )
            VALUES (
              ?, NULL, ?, ?, ?, 'active', 'url', ?, ?, ?, NULL, ?, NULL,
              'cat_uncategorized', NULL, NULL, 0, 'archived', 'not_indexed',
              'not_applicable', 'manual', 'public_url', '{}', NULL, ?, ?
            )
            """,
            (
                doc_id,
                title,
                title,
                filename_slug,
                url,
                normalized_url,
                source_hash,
                original_rel,
                now,
                now,
            ),
        )
    else:
        connection.execute(
            """
            UPDATE documents
            SET source_type = 'url',
                source_uri = ?,
                normalized_source_uri = ?,
                source_hash = ?,
                original_path = ?,
                access_context = 'public_url',
                privacy_flags_json = '{}',
                source_snapshot_path = NULL,
                ingest_status = 'archived',
                updated_at = ?
            WHERE doc_id = ?
            """,
            (url, normalized_url, source_hash, original_rel, now, doc_id),
        )

    connection.execute(
        """
        INSERT INTO source_files(
          source_file_id, doc_id, source_uri, normalized_source_uri, original_filename,
          original_ext, mime_type, size_bytes, source_hash, original_path,
          access_context, privacy_flags_json, source_snapshot_path,
          created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, 'url', 'application/json', ?, ?, ?,
                'public_url', '{}', NULL, ?, ?)
        """,
        (
            source_file_id,
            doc_id,
            url,
            normalized_url,
            _url_reference_filename(normalized_url),
            len(payload),
            source_hash,
            original_rel,
            now,
            now,
        ),
    )
    connection.execute(
        """
        INSERT INTO ingest_items(
          ingest_item_id, ingest_id, doc_id, source_uri, normalized_source_uri,
          status, created_at, updated_at, finished_at
        )
        VALUES (?, ?, ?, ?, ?, 'running', ?, ?, NULL)
        """,
        (ingest_item_id, ingest_id, doc_id, url, normalized_url, now, now),
    )
    connection.commit()
    return IngestPlanResult(
        ingest_id=ingest_id,
        status="pending",
        total_items=1,
        supported_items=1,
        unsupported_items=0,
        duplicate_items=0,
        review_items_count=0,
        inspections=(),
    )


def _create_archive_ingest_sources(
    connection: sqlite3.Connection,
    vault_path: Path | str,
    archive_path: Path,
    expansion: ArchiveExpansionCandidate,
    *,
    indbase_config: IndbaseConfig,
    task_id: str,
) -> IngestPlanResult:
    paths = vault_paths(vault_path)
    ingest_id = new_prefixed_id("ingest")
    parent_ingest_item_id = new_prefixed_id("ingest_item")
    now = utc_now_iso()
    normalized_archive_uri = normalize_source_uri(archive_path)
    archive_hash = hash_file(archive_path)
    archive_ext = archive_path.suffix.lower().lstrip(".") or "zip"
    mime_type = mimetypes.guess_type(archive_path.name)[0] or "application/zip"
    logical_records = [
        _prepare_archive_logical_record(connection, normalized_archive_uri, logical)
        for logical in expansion.logical_documents
    ]
    if not logical_records:
        raise ValueError("Archive conversion produced no logical documents.")

    storage_doc_id = logical_records[0]["doc_id"]
    original_abs = _available_archive_original_path(paths, storage_doc_id, archive_ext, ingest_id)
    original_abs.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(archive_path, original_abs)
    original_rel = paths.relative_to_vault(original_abs)
    archive_size = archive_path.stat().st_size

    connection.execute(
        """
        INSERT INTO ingest_runs(
          ingest_id, source_kind, source_input, status, total_items, succeeded_items,
          failed_items, unsupported_items, duplicate_items, review_items_count,
          task_id, created_at, updated_at
        )
        VALUES (?, 'archive', ?, 'pending', ?, 0, 0, 0, 0, 0, ?, ?, ?)
        """,
        (
            ingest_id,
            str(archive_path),
            len(logical_records) + 1,
            task_id,
            now,
            now,
        ),
    )
    connection.execute(
        """
        INSERT INTO ingest_items(
          ingest_item_id, ingest_id, doc_id, source_uri, normalized_source_uri,
          status, created_at, updated_at, finished_at, parent_ingest_item_id,
          logical_source_id
        )
        VALUES (?, ?, NULL, ?, ?, 'succeeded', ?, ?, ?, NULL, ?)
        """,
        (
            parent_ingest_item_id,
            ingest_id,
            str(archive_path),
            normalized_archive_uri,
            now,
            now,
            now,
            f"archive:{archive_hash}",
        ),
    )

    review_count = 0
    supported_count = 0
    for record in logical_records:
        logical = record["logical"]
        decision = _insert_archive_logical_source(
            connection,
            paths,
            ingest_id,
            parent_ingest_item_id,
            archive_path,
            normalized_archive_uri,
            archive_hash,
            archive_ext,
            mime_type,
            archive_size,
            original_rel,
            expansion,
            record,
            indbase_config=indbase_config,
            now=now,
        )
        if decision != "failed":
            supported_count += 1
        if decision == "review-before-current":
            review_count += 1

    connection.execute(
        """
        UPDATE ingest_runs
        SET status = 'running',
            review_items_count = ?,
            updated_at = ?
        WHERE ingest_id = ?
        """,
        (review_count, utc_now_iso(), ingest_id),
    )
    connection.commit()
    return IngestPlanResult(
        ingest_id=ingest_id,
        status="pending",
        total_items=len(logical_records) + 1,
        supported_items=supported_count,
        unsupported_items=0,
        duplicate_items=0,
        review_items_count=review_count,
        inspections=(),
    )


def _prepare_archive_logical_record(
    connection: sqlite3.Connection,
    normalized_archive_uri: str,
    logical: ArchiveLogicalDocumentCandidate,
) -> dict[str, Any]:
    normalized_uri = _archive_logical_normalized_uri(normalized_archive_uri, logical.logical_source_id)
    existing_doc_id = _existing_doc_id_for_normalized_uri(connection, normalized_uri)
    return {
        "logical": logical,
        "doc_id": existing_doc_id or new_doc_id(),
        "existing_doc_id": existing_doc_id,
        "source_uri": normalized_uri,
        "normalized_source_uri": normalized_uri,
    }


def _insert_archive_logical_source(
    connection: sqlite3.Connection,
    paths,
    ingest_id: str,
    parent_ingest_item_id: str,
    archive_path: Path,
    normalized_archive_uri: str,
    archive_hash: str,
    archive_ext: str,
    mime_type: str,
    archive_size: int,
    original_rel: str,
    expansion: ArchiveExpansionCandidate,
    record: dict[str, Any],
    *,
    indbase_config: IndbaseConfig,
    now: str,
) -> str:
    logical = record["logical"]
    doc_id = str(record["doc_id"])
    source_uri = str(record["source_uri"])
    normalized_source_uri = str(record["normalized_source_uri"])
    ingest_item_id = new_prefixed_id("ingest_item")
    source_file_id = new_prefixed_id("source_file")
    source_hash = _archive_logical_source_hash(archive_hash, logical)
    title = logical.title
    filename_slug = slugify(title)
    output_hash = hash_markdown(logical.markdown_body) if logical.markdown_body.strip() else None
    no_content_change = bool(output_hash and _current_revision_content_hash(connection, doc_id) == output_hash)
    converter_run_id = new_prefixed_id("converter_run")
    archived_artifact_map: dict[str, str] = {}
    candidate_rel: str | None = None

    if output_hash:
        archived_artifact_map = _archive_swallow_candidate_artifacts(
            paths,
            doc_id,
            converter_run_id,
            expansion.aggregate_candidate,
        )
        if not no_content_change:
            candidate_path = paths.converter_candidate_path(converter_run_id)
            candidate_path.parent.mkdir(parents=True, exist_ok=True)
            candidate_path.write_text(logical.markdown_body, encoding="utf-8")
            candidate_rel = paths.relative_to_vault(candidate_path)
    decision = _archive_logical_promotion_decision(
        indbase_config,
        expansion.aggregate_candidate,
        logical,
        archived_artifact_map=archived_artifact_map,
    )
    promotion_reason = _archive_logical_promotion_reason(
        indbase_config,
        expansion.aggregate_candidate,
        logical,
        archived_artifact_map=archived_artifact_map,
    )

    source_snapshot_path = _archive_logical_source_snapshot_path(logical, archived_artifact_map)
    quality_signals = _archive_logical_quality_signals(
        expansion,
        logical,
        archived_artifact_map=archived_artifact_map,
        original_path=original_rel,
    )
    privacy_flags = {"archive_type": expansion.archive_type, "one_to_many": True}

    if record["existing_doc_id"] is None:
        connection.execute(
            """
            INSERT INTO documents(
              doc_id, current_revision_id, title, original_title, filename_slug,
              status, source_type, source_uri, normalized_source_uri, source_hash,
              canonical_path, original_path, language, category_id, quality_status,
              quality_signals_json, needs_review, ingest_status, fts_status,
              embedding_status, classification_status, access_context,
              privacy_flags_json, source_snapshot_path, created_at, updated_at
            )
            VALUES (
              ?, NULL, ?, ?, ?, 'active', 'chatgpt_conversation', ?, ?, ?,
              NULL, ?, NULL, 'cat_uncategorized', ?, ?, ?, ?, 'not_indexed',
              'not_applicable', 'manual', 'local_archive', ?, ?, ?, ?
            )
            """,
            (
                doc_id,
                title,
                title,
                filename_slug,
                source_uri,
                normalized_source_uri,
                source_hash,
                original_rel,
                _quality_status_for_archive_decision(decision),
                _json(quality_signals),
                1 if decision == "review-before-current" else 0,
                _document_ingest_status_for_archive_decision(decision, no_content_change),
                _json(privacy_flags),
                source_snapshot_path,
                now,
                now,
            ),
        )
    else:
        connection.execute(
            """
            UPDATE documents
            SET title = ?,
                original_title = ?,
                filename_slug = ?,
                source_type = 'chatgpt_conversation',
                source_uri = ?,
                normalized_source_uri = ?,
                source_hash = ?,
                original_path = ?,
                quality_status = ?,
                quality_signals_json = ?,
                needs_review = ?,
                ingest_status = ?,
                fts_status = CASE WHEN ? THEN fts_status ELSE 'not_indexed' END,
                access_context = 'local_archive',
                privacy_flags_json = ?,
                source_snapshot_path = COALESCE(?, source_snapshot_path),
                updated_at = ?
            WHERE doc_id = ?
            """,
            (
                title,
                title,
                filename_slug,
                source_uri,
                normalized_source_uri,
                source_hash,
                original_rel,
                _quality_status_for_archive_decision(decision),
                _json(quality_signals),
                1 if decision == "review-before-current" else 0,
                _document_ingest_status_for_archive_decision(decision, no_content_change),
                1 if no_content_change else 0,
                _json(privacy_flags),
                source_snapshot_path,
                now,
                doc_id,
            ),
        )

    connection.execute(
        """
        INSERT INTO source_files(
          source_file_id, doc_id, source_uri, normalized_source_uri, original_filename,
          original_ext, mime_type, size_bytes, source_hash, original_path,
          access_context, privacy_flags_json, source_snapshot_path,
          created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'local_archive', ?, ?, ?, ?)
        """,
        (
            source_file_id,
            doc_id,
            source_uri,
            normalized_source_uri,
            archive_path.name,
            archive_ext,
            mime_type,
            archive_size,
            source_hash,
            original_rel,
            _json(privacy_flags),
            source_snapshot_path,
            now,
            now,
        ),
    )
    item_status = _archive_item_status_for_decision(decision, no_content_change)
    connection.execute(
        """
        INSERT INTO ingest_items(
          ingest_item_id, ingest_id, doc_id, source_uri, normalized_source_uri,
          status, created_at, updated_at, finished_at, parent_ingest_item_id,
          logical_source_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ingest_item_id,
            ingest_id,
            doc_id,
            source_uri,
            normalized_source_uri,
            item_status,
            now,
            now,
            now if item_status in {"succeeded", "pending_review", "failed"} else None,
            parent_ingest_item_id,
            logical.logical_source_id,
        ),
    )

    if no_content_change:
        _insert_archive_no_content_change_converter_run(
            connection,
            converter_run_id,
            doc_id,
            archive_hash,
            output_hash or "",
            quality_signals,
            expansion.aggregate_candidate,
            promotion_reason,
            now,
        )
        return decision
    if decision == "failed" or output_hash is None:
        _insert_archive_failed_converter_run(
            connection,
            converter_run_id,
            ingest_item_id,
            doc_id,
            archive_hash,
            quality_signals,
            expansion.aggregate_candidate,
            promotion_reason,
            now,
        )
        return decision

    _insert_archive_converter_run(
        connection,
        converter_run_id,
        doc_id,
        archive_hash,
        output_hash,
        quality_signals,
        expansion.aggregate_candidate,
        candidate_rel,
        archived_artifact_map,
        decision=decision,
        promotion_reason=promotion_reason,
        now=now,
    )
    if decision == "review-before-current":
        create_review_item(
            connection,
            review_type="conversion_pending_review",
            target_type="converter_run",
            target_id=converter_run_id,
            reason=promotion_reason,
            priority=40,
        )
    return decision


def _normalize_url_source_uri(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"URL must be an absolute http(s) URL: {url}")
    path = parsed.path or "/"
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", parsed.query, ""))


def _url_reference_payload(url: str, normalized_url: str) -> bytes:
    return (
        json.dumps(
            {
                "source_type": "url",
                "source_uri": url,
                "normalized_source_uri": normalized_url,
                "capture": "playwright",
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _hash_bytes(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _existing_doc_id_for_normalized_uri(connection: sqlite3.Connection, normalized_url: str) -> str | None:
    row = connection.execute(
        """
        SELECT doc_id
        FROM documents
        WHERE normalized_source_uri = ?
          AND deleted_at IS NULL
        ORDER BY created_at
        LIMIT 1
        """,
        (normalized_url,),
    ).fetchone()
    return str(row["doc_id"]) if row is not None else None


def _available_url_reference_path(paths, doc_id: str, source_file_id: str) -> Path:
    primary = paths.original_path(doc_id, "url.json")
    if not primary.exists():
        return primary
    return paths.original_dir(doc_id) / f"original__{source_file_id}.url.json"


def _title_from_url(normalized_url: str) -> str:
    parsed = urlparse(normalized_url)
    value = f"{parsed.netloc}{parsed.path}".strip("/") or parsed.netloc or "url"
    value = re.sub(r"\s+", " ", value.replace("-", " ").replace("_", " ")).strip()
    return value[:120] or "url"


def _url_reference_filename(normalized_url: str) -> str:
    parsed = urlparse(normalized_url)
    value = f"{parsed.netloc}{parsed.path}".strip("/") or "url"
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._") or "url"
    return f"{value[:80]}.url.json"


def _archive_logical_normalized_uri(normalized_archive_uri: str, logical_source_id: str) -> str:
    return f"{normalized_archive_uri}#chatgpt_conversation/{logical_source_id}"


def _available_archive_original_path(paths, doc_id: str, extension: str, ingest_id: str) -> Path:
    primary = paths.original_path(doc_id, extension)
    if not primary.exists():
        return primary
    clean_extension = extension.lower().lstrip(".") or "zip"
    return paths.original_dir(doc_id) / f"original__{ingest_id}.{clean_extension}"


def _archive_logical_source_hash(archive_hash: str, logical: ArchiveLogicalDocumentCandidate) -> str:
    payload = "\n".join(
        [
            archive_hash,
            logical.logical_source_id,
            logical.markdown_body,
        ]
    ).encode("utf-8")
    return _hash_bytes(payload)


def _archive_swallow_candidate_artifacts(
    paths,
    doc_id: str,
    converter_run_id: str,
    candidate: ConversionCandidate,
) -> dict[str, str]:
    archived: dict[str, str] = {}
    artifact_dir = paths.artifact_dir(doc_id, converter_run_id)
    for artifact in candidate.artifact_manifest.required:
        source = Path(artifact)
        if not source.is_absolute():
            source = paths.swallow_cache / artifact
        if not source.is_file():
            continue
        artifact_dir.mkdir(parents=True, exist_ok=True)
        target = _available_artifact_path(artifact_dir, source.name)
        shutil.copy2(source, target)
        archived[artifact] = paths.relative_to_vault(target)
    return archived


def _archive_logical_source_snapshot_path(
    logical: ArchiveLogicalDocumentCandidate,
    archived_artifact_map: dict[str, str],
) -> str | None:
    for locator in logical.source_locators:
        artifact = locator.get("artifact")
        if isinstance(artifact, str) and artifact in archived_artifact_map:
            return archived_artifact_map[artifact]
    for archived in archived_artifact_map.values():
        if Path(archived).name == "conversations.json":
            return archived
    return None


def _archive_logical_quality_signals(
    expansion: ArchiveExpansionCandidate,
    logical: ArchiveLogicalDocumentCandidate,
    *,
    archived_artifact_map: dict[str, str],
    original_path: str,
) -> dict[str, object]:
    aggregate = expansion.aggregate_candidate
    quality_signals = candidate_to_quality_signals(aggregate)
    warnings = [*aggregate.warnings, *logical.warnings]
    locators: list[dict[str, object]] = []
    for locator in logical.source_locators:
        durable = dict(locator)
        artifact = durable.get("artifact")
        if isinstance(artifact, str) and artifact in archived_artifact_map:
            durable["artifact"] = archived_artifact_map[artifact]
        durable["source_path"] = original_path
        locators.append(durable)
    artifact_manifest = aggregate.artifact_manifest.to_dict()
    artifact_manifest["archived_required"] = list(archived_artifact_map.values())
    return {
        **quality_signals,
        "warnings": warnings,
        "artifact_manifest": artifact_manifest,
        "source_locators": locators,
        "archive_type": expansion.archive_type,
        "logical_source_id": logical.logical_source_id,
        "archive_metadata": logical.metadata,
        "text_length": len(logical.markdown_body),
        "empty": len(logical.markdown_body.strip()) == 0,
    }


def _archive_logical_promotion_decision(
    indbase_config: IndbaseConfig,
    aggregate: ConversionCandidate,
    logical: ArchiveLogicalDocumentCandidate,
    *,
    archived_artifact_map: dict[str, str],
) -> str:
    if not logical.markdown_body.strip():
        return "failed"
    if "archive_conversation_no_messages" in logical.warnings:
        return "review-before-current"
    return _evaluate_archive_logical_promotion(
        indbase_config,
        aggregate,
        logical,
        archived_artifact_map=archived_artifact_map,
    ).status


def _archive_logical_promotion_reason(
    indbase_config: IndbaseConfig,
    aggregate: ConversionCandidate,
    logical: ArchiveLogicalDocumentCandidate,
    *,
    archived_artifact_map: dict[str, str],
) -> str:
    decision = _archive_logical_promotion_decision(
        indbase_config,
        aggregate,
        logical,
        archived_artifact_map=archived_artifact_map,
    )
    if decision == "trusted-current":
        return "Swallow archive conversation passed indbase promotion gate."
    if not logical.markdown_body.strip():
        return "Archive conversation has empty Markdown."
    if "archive_conversation_no_messages" in logical.warnings:
        return "Archive conversation has no messages and requires review."
    return _evaluate_archive_logical_promotion(
        indbase_config,
        aggregate,
        logical,
        archived_artifact_map=archived_artifact_map,
    ).reason


def _evaluate_archive_logical_promotion(
    indbase_config: IndbaseConfig,
    aggregate: ConversionCandidate,
    logical: ArchiveLogicalDocumentCandidate,
    *,
    archived_artifact_map: dict[str, str],
):
    return evaluate_swallow_promotion(
        indbase_config,
        status=aggregate.status,
        quality_score=aggregate.quality_score,
        markdown_body=logical.markdown_body,
        warnings=(*aggregate.warnings, *logical.warnings),
        errors=aggregate.errors,
        provenance=aggregate.provenance,
        source_locators=logical.source_locators,
        artifact_manifest_required=aggregate.artifact_manifest.required,
        archived_artifacts=tuple(archived_artifact_map.values()),
        access_context=aggregate.access_context,
        privacy_flags=aggregate.privacy_flags,
        trusted_reason="Swallow archive conversation passed indbase promotion gate.",
        generic_review_reason="Swallow archive conversation requires review.",
    )


def _quality_status_for_archive_decision(decision: str) -> str:
    if decision == "failed":
        return "failed"
    if decision == "review-before-current":
        return "warning"
    return "passed"


def _document_ingest_status_for_archive_decision(decision: str, no_content_change: bool) -> str:
    if no_content_change:
        return "revisioned"
    if decision == "trusted-current":
        return "converted"
    if decision == "review-before-current":
        return "pending_review"
    return "failed"


def _archive_item_status_for_decision(decision: str, no_content_change: bool) -> str:
    if no_content_change:
        return "succeeded"
    if decision == "trusted-current":
        return "running"
    if decision == "review-before-current":
        return "pending_review"
    return "failed"


def _insert_archive_converter_run(
    connection: sqlite3.Connection,
    converter_run_id: str,
    doc_id: str,
    archive_hash: str,
    output_hash: str,
    quality_signals: dict[str, object],
    aggregate: ConversionCandidate,
    candidate_rel: str | None,
    archived_artifact_map: dict[str, str],
    *,
    decision: str,
    promotion_reason: str,
    now: str,
) -> None:
    provenance = aggregate.provenance
    connection.execute(
        """
        INSERT INTO converter_runs(
          converter_run_id, doc_id, revision_id, converter_name, converter_version,
          input_hash, output_hash, warnings_json, quality_signals_json, status,
          started_at, finished_at, created_at, updated_at,
          external_job_id, external_trace_path, external_manifest_path,
          primary_worker, worker_chain_json, candidate_path,
          artifact_manifest_json, promotion_status, promotion_reason
        )
        VALUES (?, ?, NULL, 'swallow', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            converter_run_id,
            doc_id,
            provenance.swallow_version if provenance else "unknown",
            archive_hash,
            output_hash,
            _json(quality_signals.get("warnings", [])),
            _json(quality_signals),
            "succeeded" if decision == "trusted-current" else "pending_review",
            now,
            now,
            now,
            now,
            provenance.swallow_job_id if provenance else None,
            provenance.trace_path if provenance else None,
            provenance.manifest_path if provenance else None,
            provenance.primary_worker if provenance else "export_archive_worker",
            _json(list(provenance.worker_chain) if provenance else ["export_archive_worker"]),
            candidate_rel,
            artifact_manifest_json(aggregate, tuple(archived_artifact_map.values())),
            decision,
            promotion_reason,
        ),
    )


def _insert_archive_no_content_change_converter_run(
    connection: sqlite3.Connection,
    converter_run_id: str,
    doc_id: str,
    archive_hash: str,
    output_hash: str,
    quality_signals: dict[str, object],
    aggregate: ConversionCandidate,
    promotion_reason: str,
    now: str,
) -> None:
    provenance = aggregate.provenance
    connection.execute(
        """
        INSERT INTO converter_runs(
          converter_run_id, doc_id, revision_id, converter_name, converter_version,
          input_hash, output_hash, warnings_json, quality_signals_json, status,
          started_at, finished_at, created_at, updated_at,
          external_job_id, external_trace_path, external_manifest_path,
          primary_worker, worker_chain_json, promotion_status, promotion_reason
        )
        VALUES (?, ?, NULL, 'swallow', ?, ?, ?, ?, ?, 'skipped_no_content_change',
                ?, ?, ?, ?, ?, ?, ?, ?, ?, 'skipped_no_content_change', ?)
        """,
        (
            converter_run_id,
            doc_id,
            provenance.swallow_version if provenance else "unknown",
            archive_hash,
            output_hash,
            _json(quality_signals.get("warnings", [])),
            _json(quality_signals | {"no_content_change": True}),
            now,
            now,
            now,
            now,
            provenance.swallow_job_id if provenance else None,
            provenance.trace_path if provenance else None,
            provenance.manifest_path if provenance else None,
            provenance.primary_worker if provenance else "export_archive_worker",
            _json(list(provenance.worker_chain) if provenance else ["export_archive_worker"]),
            promotion_reason,
        ),
    )


def _insert_archive_failed_converter_run(
    connection: sqlite3.Connection,
    converter_run_id: str,
    ingest_item_id: str,
    doc_id: str,
    archive_hash: str,
    quality_signals: dict[str, object],
    aggregate: ConversionCandidate,
    promotion_reason: str,
    now: str,
) -> None:
    provenance = aggregate.provenance
    error_id = record_error(
        connection,
        component="conversion",
        error_type="archive_conversion_failed",
        message=promotion_reason,
        user_message="Failed to promote archive conversation into Markdown.",
        retryable=False,
        payload={"ingest_item_id": ingest_item_id, "doc_id": doc_id},
    )
    connection.execute(
        """
        UPDATE ingest_items
        SET error_id = ?, status = 'failed', updated_at = ?, finished_at = ?
        WHERE ingest_item_id = ?
        """,
        (error_id, now, now, ingest_item_id),
    )
    connection.execute(
        """
        INSERT INTO converter_runs(
          converter_run_id, doc_id, revision_id, converter_name, converter_version,
          input_hash, output_hash, warnings_json, quality_signals_json, status,
          started_at, finished_at, created_at, updated_at,
          external_job_id, external_trace_path, external_manifest_path,
          primary_worker, worker_chain_json, promotion_status, promotion_reason
        )
        VALUES (?, ?, NULL, 'swallow', ?, ?, NULL, ?, ?, 'failed',
                ?, ?, ?, ?, ?, ?, ?, ?, ?, 'failed', ?)
        """,
        (
            converter_run_id,
            doc_id,
            provenance.swallow_version if provenance else "unknown",
            archive_hash,
            _json(quality_signals.get("warnings", [])),
            _json(quality_signals | {"error_id": error_id}),
            now,
            now,
            now,
            now,
            provenance.swallow_job_id if provenance else None,
            provenance.trace_path if provenance else None,
            provenance.manifest_path if provenance else None,
            provenance.primary_worker if provenance else "export_archive_worker",
            _json(list(provenance.worker_chain) if provenance else ["export_archive_worker"]),
            promotion_reason,
        ),
    )
    create_review_item(
        connection,
        review_type="conversion_low_quality",
        target_type="converter_run",
        target_id=converter_run_id,
        reason=promotion_reason,
        priority=40,
    )


def _current_revision_content_hash(connection: sqlite3.Connection, doc_id: str) -> str | None:
    row = connection.execute(
        """
        SELECT dr.content_hash
        FROM documents d
        JOIN document_revisions dr ON dr.revision_id = d.current_revision_id
        WHERE d.doc_id = ?
        """,
        (doc_id,),
    ).fetchone()
    return str(row["content_hash"]) if row is not None else None


def _available_artifact_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    index = 2
    while True:
        next_candidate = directory / f"{stem}-{index}{suffix}"
        if not next_candidate.exists():
            return next_candidate
        index += 1


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


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
              AND ii.status IN ('failed', 'pending_review')
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
          AND doc_id IS NOT NULL
        """,
        (ingest_id,),
    ).fetchone()
    return int(succeeded["count"] or 0) > 0 and int(row["count"] or 0) == 0
