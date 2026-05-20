"""Conversion orchestration for archived source files."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

from indbase_core.config import IndbaseConfig, default_config, load_config
from indbase_core.errors import record_error
from indbase_core.ids import new_prefixed_id
from indbase_core.paths import VaultPaths, vault_paths
from indbase_core.promotion_policy import evaluate_swallow_promotion
from indbase_core.reviews import create_review_item
from indbase_core.swallow_adapter import (
    ConversionCandidate,
    SwallowIngestAdapter,
    artifact_manifest_json,
    candidate_to_quality_signals,
)
from indbase_core.time import utc_now_iso

CONVERTER_VERSION = "indbase.v0.1"


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
    indbase_config = _load_indbase_config_if_present(paths)
    rows = connection.execute(
        """
        SELECT ii.ingest_item_id, ii.doc_id, ii.source_uri, sf.source_file_id, sf.original_ext,
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
        if not indbase_config.features.swallow_ingest:
            failed += 1
            _mark_legacy_conversion_retired(
                connection,
                row["ingest_item_id"],
                row["doc_id"],
                row["source_hash"],
                str(row["original_ext"] or "").lower(),
            )
            continue
        try:
            converted_source = _convert_one(connection, paths, row, indbase_config=indbase_config)
            if converted_source is None:
                skipped += 1
            else:
                converted.append(converted_source)
        except Exception as exc:
            failed += 1
            _mark_conversion_failed(
                connection,
                row,
                exc,
                converter_name_override="swallow",
            )

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
    *,
    indbase_config: IndbaseConfig,
) -> ConvertedSource | None:
    now = utc_now_iso()
    original_path = paths.root / row["original_path"]
    return _convert_one_with_swallow(connection, paths, row, original_path, indbase_config, now)


def _convert_one_with_swallow(
    connection: sqlite3.Connection,
    paths: VaultPaths,
    row: sqlite3.Row,
    original_path: Path,
    indbase_config: IndbaseConfig,
    now: str,
) -> ConvertedSource | None:
    adapter = SwallowIngestAdapter(vault_path=paths.root, config=indbase_config.ingest.swallow)
    source_type = str(row["original_ext"] or "").lower()
    candidate = adapter.convert_url(row["source_uri"]) if source_type == "url" else adapter.convert_file(original_path)
    if not candidate.markdown_body.strip():
        raise NoExtractableContentError("No extractable content after swallow conversion.")

    output_hash = hash_markdown(candidate.markdown_body)
    quality_signals = candidate_to_quality_signals(candidate) | {
        "text_length": len(candidate.markdown_body),
        "empty": len(candidate.markdown_body.strip()) == 0,
    }
    if _current_revision_content_hash(connection, row["doc_id"]) == output_hash:
        _mark_no_content_change_with_swallow(connection, row, candidate, output_hash, quality_signals, now)
        return None

    converter_run_id = new_prefixed_id("converter_run")
    candidate_path = paths.converter_candidate_path(converter_run_id)
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(candidate.markdown_body, encoding="utf-8")
    candidate_rel = paths.relative_to_vault(candidate_path)
    archived_artifacts = _archive_required_artifacts(paths, row["doc_id"], converter_run_id, candidate)
    source_snapshot_path = _archived_source_snapshot_path(candidate, archived_artifacts)
    quality_signals = _quality_signals_with_durable_locator_paths(
        quality_signals,
        archived_artifacts=archived_artifacts,
        original_path=row["original_path"],
        source_snapshot_path=source_snapshot_path,
    )
    decision = _swallow_promotion_decision(indbase_config, candidate, archived_artifacts)
    promotion_reason = _swallow_promotion_reason(indbase_config, candidate, archived_artifacts)
    if decision == "failed":
        if candidate_path.exists():
            candidate_path.unlink()
        shutil.rmtree(paths.artifact_dir(row["doc_id"], converter_run_id), ignore_errors=True)
        raise NoExtractableContentError(promotion_reason)
    status = "succeeded" if decision == "trusted-current" else "pending_review"
    provenance = candidate.provenance

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
            row["doc_id"],
            provenance.swallow_version if provenance else "unknown",
            row["source_hash"],
            output_hash,
            _json(list(candidate.warnings)),
            _json(quality_signals),
            status,
            now,
            now,
            now,
            now,
            provenance.swallow_job_id if provenance else None,
            provenance.trace_path if provenance else None,
            provenance.manifest_path if provenance else None,
            provenance.primary_worker if provenance else None,
            _json(list(provenance.worker_chain) if provenance else []),
            candidate_rel,
            artifact_manifest_json(candidate, archived_artifacts),
            decision,
            promotion_reason,
        ),
    )
    connection.execute(
        """
        UPDATE documents
        SET ingest_status = ?,
            quality_status = ?,
            quality_signals_json = ?,
            access_context = ?,
            privacy_flags_json = ?,
            source_snapshot_path = COALESCE(?, source_snapshot_path),
            needs_review = ?,
            updated_at = ?
        WHERE doc_id = ?
        """,
        (
            "converted" if decision == "trusted-current" else "pending_review",
            _quality_status_for_swallow_decision(decision, candidate),
            _json(quality_signals),
            candidate.access_context,
            _json(candidate.privacy_flags),
            source_snapshot_path,
            0 if decision == "trusted-current" else 1,
            now,
            row["doc_id"],
        ),
    )
    connection.execute(
        """
        UPDATE source_files
        SET access_context = ?,
            privacy_flags_json = ?,
            source_snapshot_path = COALESCE(?, source_snapshot_path),
            updated_at = ?
        WHERE source_file_id = ?
        """,
        (
            candidate.access_context,
            _json(candidate.privacy_flags),
            source_snapshot_path,
            now,
            row["source_file_id"],
        ),
    )
    if decision != "trusted-current":
        connection.execute(
            """
            UPDATE ingest_items
            SET status = 'pending_review', updated_at = ?, finished_at = ?
            WHERE ingest_item_id = ?
            """,
            (now, now, row["ingest_item_id"]),
        )
        create_review_item(
            connection,
            review_type="conversion_pending_review",
            target_type="converter_run",
            target_id=converter_run_id,
            reason=promotion_reason,
            priority=40,
        )
        return None

    return ConvertedSource(
        ingest_item_id=row["ingest_item_id"],
        doc_id=row["doc_id"],
        converter_run_id=converter_run_id,
        candidate_path=candidate_rel,
        markdown=candidate.markdown_body,
        output_hash=output_hash,
        warnings=candidate.warnings,
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
    if row is None:
        return None
    return str(row["content_hash"])


def _mark_no_content_change_with_swallow(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    candidate: ConversionCandidate,
    output_hash: str,
    quality_signals: dict[str, object],
    now: str,
) -> None:
    converter_run_id = new_prefixed_id("converter_run")
    provenance = candidate.provenance
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
            row["doc_id"],
            provenance.swallow_version if provenance else "unknown",
            row["source_hash"],
            output_hash,
            _json(list(candidate.warnings)),
            _json(quality_signals | {"no_content_change": True}),
            now,
            now,
            now,
            now,
            provenance.swallow_job_id if provenance else None,
            provenance.trace_path if provenance else None,
            provenance.manifest_path if provenance else None,
            provenance.primary_worker if provenance else None,
            _json(list(provenance.worker_chain) if provenance else []),
            "Output matches the current revision content hash.",
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
            "warning" if candidate.warnings else "passed",
            _json(quality_signals | {"warnings": list(candidate.warnings), "no_content_change": True}),
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
    *,
    converter_name_override: str | None = None,
) -> None:
    now = utc_now_iso()
    doc_id = str(row["doc_id"])
    source_type = str(row["original_ext"] or "").lower()
    converter_name = converter_name_override or "swallow"
    error_id = record_error(
        connection,
        component="conversion",
        error_type=_conversion_error_type(exc),
        message=str(exc),
        user_message="Failed to convert source into Markdown with swallow.",
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


def _archive_required_artifacts(
    paths: VaultPaths,
    doc_id: str,
    converter_run_id: str,
    candidate: ConversionCandidate,
) -> tuple[str, ...]:
    archived: list[str] = []
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
        archived.append(paths.relative_to_vault(target))
    return tuple(archived)


def _archived_source_snapshot_path(candidate: ConversionCandidate, archived_artifacts: tuple[str, ...]) -> str | None:
    if not archived_artifacts:
        return None
    if candidate.source_snapshot_path:
        source_name = Path(candidate.source_snapshot_path).name
        for archived in archived_artifacts:
            if Path(archived).name == source_name:
                return archived
    for archived in archived_artifacts:
        filename = Path(archived).name.lower()
        if filename in {"rendered.html", "page.html"} or filename.endswith(".html"):
            return archived
    return None


def _quality_signals_with_durable_locator_paths(
    quality_signals: dict[str, object],
    *,
    archived_artifacts: tuple[str, ...],
    original_path: str | None,
    source_snapshot_path: str | None,
) -> dict[str, object]:
    locators = quality_signals.get("source_locators")
    if not isinstance(locators, list):
        return quality_signals
    rewritten: list[object] = []
    for locator in locators:
        if not isinstance(locator, dict):
            rewritten.append(locator)
            continue
        durable_locator = dict(locator)
        if locator.get("kind") in {"web_snapshot", "browser_capture"}:
            if source_snapshot_path:
                durable_locator["artifact"] = source_snapshot_path
        else:
            for key in ("artifact", "page_image_artifact", "normalized_audio_artifact"):
                if key in durable_locator:
                    durable_locator[key] = _archived_artifact_for_locator_path(
                        str(durable_locator[key]),
                        archived_artifacts,
                    )
            if locator.get("kind") in {"ocr", "ocr_page", "asr_transcript", "asr_segment"} and original_path:
                durable_locator["source_path"] = original_path
        rewritten.append(durable_locator)
    return quality_signals | {"source_locators": rewritten}


def _archived_artifact_for_locator_path(locator_path: str, archived_artifacts: tuple[str, ...]) -> str:
    locator_name = Path(locator_path).name
    for archived in archived_artifacts:
        if Path(archived).name == locator_name:
            return archived
    return locator_path


def _quality_status_for_swallow_decision(decision: str, candidate: ConversionCandidate) -> str:
    if decision != "trusted-current":
        return "warning"
    return "warning" if candidate.warnings else "passed"


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


def _swallow_promotion_decision(
    indbase_config: IndbaseConfig,
    candidate: ConversionCandidate,
    archived_artifacts: tuple[str, ...],
) -> str:
    return _evaluate_swallow_candidate_promotion(indbase_config, candidate, archived_artifacts).status


def _swallow_promotion_reason(
    indbase_config: IndbaseConfig,
    candidate: ConversionCandidate,
    archived_artifacts: tuple[str, ...],
) -> str:
    return _evaluate_swallow_candidate_promotion(indbase_config, candidate, archived_artifacts).reason


def _evaluate_swallow_candidate_promotion(
    indbase_config: IndbaseConfig,
    candidate: ConversionCandidate,
    archived_artifacts: tuple[str, ...],
):
    return evaluate_swallow_promotion(
        indbase_config,
        status=candidate.status,
        quality_score=candidate.quality_score,
        markdown_body=candidate.markdown_body,
        warnings=candidate.warnings,
        errors=candidate.errors,
        provenance=candidate.provenance,
        source_locators=candidate.source_locators,
        source_snapshot_path=candidate.source_snapshot_path,
        artifact_manifest_required=candidate.artifact_manifest.required,
        archived_artifacts=archived_artifacts,
        access_context=candidate.access_context,
        privacy_flags=candidate.privacy_flags,
    )


def _load_indbase_config_if_present(paths: VaultPaths) -> IndbaseConfig:
    if paths.config_path.is_file():
        return load_config(paths.config_path)
    return default_config(paths.root)



def _mark_legacy_conversion_retired(
    connection: sqlite3.Connection,
    ingest_item_id: str,
    doc_id: str,
    source_hash: str,
    source_type: str,
) -> None:
    now = utc_now_iso()
    message = (
        "Legacy indbase conversion has been retired; enable features.swallow_ingest "
        "and install the swallow extra to convert this source."
    )
    error_id = record_error(
        connection,
        component="conversion",
        error_type="legacy_conversion_retired",
        message=message,
        user_message="Swallow conversion is required for ingest.",
        retryable=False,
        payload={
            "ingest_item_id": ingest_item_id,
            "doc_id": doc_id,
            "source_type": source_type,
            "required_feature": "features.swallow_ingest",
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
        VALUES (?, ?, NULL, 'swallow_required_gate', ?, ?, NULL, ?, ?, 'failed', ?, ?, ?, ?)
        """,
        (
            converter_run_id,
            doc_id,
            CONVERTER_VERSION,
            source_hash,
            _json([message]),
            _json(
                {
                    "source_type": source_type,
                    "legacy_conversion_retired": True,
                    "required_feature": "features.swallow_ingest",
                }
            ),
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
            _json(
                {
                    "source_type": source_type,
                    "legacy_conversion_retired": True,
                    "required_feature": "features.swallow_ingest",
                    "error_id": error_id,
                }
            ),
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
