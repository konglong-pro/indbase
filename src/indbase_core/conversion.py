"""Conversion orchestration for archived source files."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3

from indbase_core.errors import record_error
from indbase_core.ids import new_prefixed_id
from indbase_core.normalizers import NormalizedMarkdown, normalize_tier1_source, normalize_tier2_source
from indbase_core.paths import VaultPaths, vault_paths
from indbase_core.reviews import create_review_item
from indbase_core.time import utc_now_iso

CONVERTER_VERSION = "indbase.v0.1"
SUPPORTED_TIER1_CONVERSION_TYPES = {"md", "txt", "csv", "json", "html"}
SUPPORTED_TIER2_CONVERSION_TYPES = {"docx", "xlsx", "pptx", "pdf"}


class NoExtractableContentError(ValueError):
    """Raised when normalization produced no searchable body text."""


@dataclass(frozen=True)
class ConvertedSource:
    ingest_item_id: str
    doc_id: str
    converter_run_id: str
    candidate_path: str
    markdown: str
    output_hash: str
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ConversionBatchResult:
    ingest_id: str
    converted_items: tuple[ConvertedSource, ...]
    skipped_items: int
    failed_items: int


def convert_archived_sources(
    connection: sqlite3.Connection,
    vault_path: Path | str,
    ingest_id: str,
) -> ConversionBatchResult:
    paths = vault_paths(vault_path)
    rows = connection.execute(
        """
        SELECT ii.ingest_item_id, ii.doc_id, sf.source_file_id, sf.original_ext,
               sf.source_hash, sf.original_path
        FROM ingest_items ii
        JOIN ingest_runs ir ON ir.ingest_id = ii.ingest_id
        JOIN documents d ON d.doc_id = ii.doc_id
        JOIN source_files sf ON sf.doc_id = d.doc_id
        WHERE ii.ingest_id = ?
          AND ii.status = 'running'
          AND d.ingest_status = 'archived'
          AND sf.normalized_source_uri = ii.normalized_source_uri
          AND sf.created_at >= ir.created_at
        ORDER BY ii.source_uri
        """,
        (ingest_id,),
    ).fetchall()

    converted: list[ConvertedSource] = []
    skipped = 0
    failed = 0
    for row in rows:
        source_type = str(row["original_ext"] or "").lower()
        if source_type not in SUPPORTED_TIER1_CONVERSION_TYPES | SUPPORTED_TIER2_CONVERSION_TYPES:
            skipped += 1
            failed += 1
            _mark_conversion_skipped(
                connection,
                row["ingest_item_id"],
                row["doc_id"],
                row["source_hash"],
                source_type,
            )
            continue
        try:
            converted_source = _convert_one(connection, paths, row)
            if converted_source is None:
                skipped += 1
            else:
                converted.append(converted_source)
        except Exception as exc:
            failed += 1
            _mark_conversion_failed(connection, row, exc)

    _update_ingest_run_after_conversion(connection, ingest_id, failed)
    connection.commit()
    return ConversionBatchResult(
        ingest_id=ingest_id,
        converted_items=tuple(converted),
        skipped_items=skipped,
        failed_items=failed,
    )


def _convert_one(
    connection: sqlite3.Connection,
    paths: VaultPaths,
    row: sqlite3.Row,
) -> ConvertedSource | None:
    now = utc_now_iso()
    original_path = paths.root / row["original_path"]
    normalized = _normalize_source(original_path, row["original_ext"])
    if not normalized.markdown.strip():
        raise NoExtractableContentError("No extractable content after normalization.")

    output_hash = hash_markdown(normalized.markdown)
    quality_signals = {
        "text_length": len(normalized.markdown),
        "empty": len(normalized.markdown.strip()) == 0,
    }
    if _current_revision_content_hash(connection, row["doc_id"]) == output_hash:
        _mark_no_content_change(connection, row, normalized, output_hash, quality_signals, now)
        return None

    converter_run_id = new_prefixed_id("converter_run")
    candidate_path = paths.converter_candidate_path(converter_run_id)
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(normalized.markdown, encoding="utf-8")
    candidate_rel = paths.relative_to_vault(candidate_path)
    connection.execute(
        """
        INSERT INTO converter_runs(
          converter_run_id, doc_id, revision_id, converter_name, converter_version,
          input_hash, output_hash, warnings_json, quality_signals_json, status,
          started_at, finished_at, created_at, updated_at
        )
        VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, 'succeeded', ?, ?, ?, ?)
        """,
        (
            converter_run_id,
            row["doc_id"],
            normalized.converter_name,
            CONVERTER_VERSION,
            row["source_hash"],
            output_hash,
            _json(list(normalized.warnings)),
            _json(quality_signals),
            now,
            now,
            now,
            now,
        ),
    )
    connection.execute(
        """
        UPDATE documents
        SET ingest_status = 'converted', quality_status = ?, quality_signals_json = ?, updated_at = ?
        WHERE doc_id = ?
        """,
        (
            "warning" if normalized.warnings else "passed",
            _json(quality_signals | {"warnings": list(normalized.warnings)}),
            now,
            row["doc_id"],
        ),
    )
    return ConvertedSource(
        ingest_item_id=row["ingest_item_id"],
        doc_id=row["doc_id"],
        converter_run_id=converter_run_id,
        candidate_path=candidate_rel,
        markdown=normalized.markdown,
        output_hash=output_hash,
        warnings=normalized.warnings,
    )


def _normalize_source(path: Path, source_type: str) -> NormalizedMarkdown:
    normalized_type = source_type.lower().lstrip(".")
    if normalized_type in SUPPORTED_TIER1_CONVERSION_TYPES:
        return normalize_tier1_source(path, normalized_type)
    if normalized_type in SUPPORTED_TIER2_CONVERSION_TYPES:
        return normalize_tier2_source(path, normalized_type)
    raise ValueError(f"Conversion is not implemented for .{source_type}")


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
    if row is None:
        return None
    return str(row["content_hash"])


def _mark_no_content_change(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    normalized: NormalizedMarkdown,
    output_hash: str,
    quality_signals: dict[str, object],
    now: str,
) -> None:
    converter_run_id = new_prefixed_id("converter_run")
    connection.execute(
        """
        INSERT INTO converter_runs(
          converter_run_id, doc_id, revision_id, converter_name, converter_version,
          input_hash, output_hash, warnings_json, quality_signals_json, status,
          started_at, finished_at, created_at, updated_at
        )
        VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, 'skipped_no_content_change', ?, ?, ?, ?)
        """,
        (
            converter_run_id,
            row["doc_id"],
            normalized.converter_name,
            CONVERTER_VERSION,
            row["source_hash"],
            output_hash,
            _json(list(normalized.warnings)),
            _json(quality_signals | {"no_content_change": True}),
            now,
            now,
            now,
            now,
        ),
    )
    connection.execute(
        """
        UPDATE documents
        SET ingest_status = 'revisioned',
            quality_status = ?,
            quality_signals_json = ?,
            updated_at = ?
        WHERE doc_id = ?
        """,
        (
            "warning" if normalized.warnings else "passed",
            _json(quality_signals | {"warnings": list(normalized.warnings), "no_content_change": True}),
            now,
            row["doc_id"],
        ),
    )
    connection.execute(
        """
        UPDATE ingest_items
        SET status = 'succeeded', updated_at = ?, finished_at = ?
        WHERE ingest_item_id = ?
        """,
        (now, now, row["ingest_item_id"]),
    )


def _mark_conversion_failed(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    exc: Exception,
) -> None:
    now = utc_now_iso()
    doc_id = str(row["doc_id"])
    source_type = str(row["original_ext"] or "").lower()
    converter_name = _failure_converter_name(source_type)
    error_id = record_error(
        connection,
        component="conversion",
        error_type=_conversion_error_type(exc),
        message=str(exc),
        user_message="Failed to normalize source into Markdown.",
        retryable=False,
        payload={
            "ingest_item_id": row["ingest_item_id"],
            "doc_id": doc_id,
            "source_type": source_type,
        },
    )
    converter_run_id = new_prefixed_id("converter_run")
    connection.execute(
        """
        UPDATE ingest_items
        SET status = 'failed', error_id = ?, updated_at = ?, finished_at = ?
        WHERE ingest_item_id = ?
        """,
        (error_id, now, now, row["ingest_item_id"]),
    )
    connection.execute(
        """
        UPDATE documents
        SET ingest_status = CASE
                WHEN current_revision_id IS NULL THEN 'failed'
                ELSE 'revisioned'
            END,
            fts_status = CASE
                WHEN current_revision_id IS NULL THEN 'not_indexed'
                ELSE fts_status
            END,
            needs_review = 1,
            quality_status = CASE
                WHEN current_revision_id IS NULL THEN 'failed'
                ELSE 'warning'
            END,
            quality_signals_json = ?,
            updated_at = ?
        WHERE doc_id = ?
        """,
        (
            _json({"source_type": source_type, "error_id": error_id}),
            now,
            doc_id,
        ),
    )
    connection.execute(
        """
        INSERT INTO converter_runs(
          converter_run_id, doc_id, revision_id, converter_name, converter_version,
          input_hash, output_hash, warnings_json, quality_signals_json, status,
          started_at, finished_at, created_at, updated_at
        )
        VALUES (?, ?, NULL, ?, ?, ?, NULL, ?, ?, 'failed', ?, ?, ?, ?)
        """,
        (
            converter_run_id,
            doc_id,
            converter_name,
            CONVERTER_VERSION,
            row["source_hash"],
            _json([str(exc)]),
            _json({"source_type": source_type, "error_id": error_id}),
            now,
            now,
            now,
            now,
        ),
    )
    create_review_item(
        connection,
        review_type="conversion_low_quality",
        target_type="converter_run",
        target_id=converter_run_id,
        reason=f"Conversion failed for .{source_type}: {exc}",
        priority=40,
    )


def _conversion_error_type(exc: Exception) -> str:
    if isinstance(exc, NoExtractableContentError):
        return "no_extractable_content"
    return type(exc).__name__


def _failure_converter_name(source_type: str) -> str:
    if source_type in SUPPORTED_TIER2_CONVERSION_TYPES:
        return "markitdown"
    if source_type in {"html"}:
        return "markitdown"
    return "tier1_normalizer"



def _mark_conversion_skipped(
    connection: sqlite3.Connection,
    ingest_item_id: str,
    doc_id: str,
    source_hash: str,
    source_type: str,
) -> None:
    now = utc_now_iso()
    message = f"Source type .{source_type} is not converted in M2."
    error_id = record_error(
        connection,
        component="conversion",
        error_type="UnsupportedConversionType",
        message=message,
        user_message="This source type is supported only by a later conversion stage.",
        retryable=False,
        payload={
            "ingest_item_id": ingest_item_id,
            "doc_id": doc_id,
            "source_type": source_type,
            "mvp_stage": "M2",
        },
    )
    converter_run_id = new_prefixed_id("converter_run")
    connection.execute(
        """
        INSERT INTO converter_runs(
          converter_run_id, doc_id, revision_id, converter_name, converter_version,
          input_hash, output_hash, warnings_json, quality_signals_json, status,
          started_at, finished_at, created_at, updated_at
        )
        VALUES (?, ?, NULL, 'conversion_gate', ?, ?, NULL, ?, ?, 'failed', ?, ?, ?, ?)
        """,
        (
            converter_run_id,
            doc_id,
            CONVERTER_VERSION,
            source_hash,
            _json([message]),
            _json({"source_type": source_type, "m2_supported": False}),
            now,
            now,
            now,
            now,
        ),
    )
    create_review_item(
        connection,
        review_type="conversion_low_quality",
        target_type="converter_run",
        target_id=converter_run_id,
        reason=message,
        priority=40,
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
        SET ingest_status = CASE
                WHEN current_revision_id IS NULL THEN 'failed'
                ELSE 'revisioned'
            END,
            fts_status = CASE
                WHEN current_revision_id IS NULL THEN 'not_indexed'
                ELSE fts_status
            END,
            needs_review = 1,
            quality_status = CASE
                WHEN current_revision_id IS NULL THEN 'failed'
                ELSE 'warning'
            END,
            quality_signals_json = ?,
            updated_at = ?
        WHERE doc_id = ?
        """,
        (
            _json({"source_type": source_type, "m2_supported": False, "error_id": error_id}),
            now,
            doc_id,
        ),
    )


def _update_ingest_run_after_conversion(
    connection: sqlite3.Connection,
    ingest_id: str,
    failed_count: int,
) -> None:
    if failed_count == 0:
        connection.execute(
            "UPDATE ingest_runs SET updated_at = ? WHERE ingest_id = ?",
            (utc_now_iso(), ingest_id),
        )
        return
    connection.execute(
        """
        UPDATE ingest_runs
        SET failed_items = COALESCE(failed_items, 0) + ?,
            status = 'completed_with_issues',
            updated_at = ?
        WHERE ingest_id = ?
        """,
        (failed_count, utc_now_iso(), ingest_id),
    )


def hash_markdown(markdown: str) -> str:
    return f"sha256:{hashlib.sha256(markdown.encode('utf-8')).hexdigest()}"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
