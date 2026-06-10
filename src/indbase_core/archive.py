"""Original source archive service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import sqlite3

from indbase_core.errors import record_error
from indbase_core.ids import new_doc_id, new_prefixed_id
from indbase_core.config import IngestConfig
from indbase_core.paths import VaultPaths, slugify, vault_paths
from indbase_core.source_inspector import SourceInspection, inspect_source
from indbase_core.time import utc_now_iso


@dataclass(frozen=True)
class ArchivedSource:
    ingest_item_id: str
    doc_id: str
    source_file_id: str
    original_path: str


@dataclass(frozen=True)
class ArchiveResult:
    ingest_id: str
    archived_items: tuple[ArchivedSource, ...]
    failed_items: int


def archive_pending_sources(
    connection: sqlite3.Connection,
    vault_path: Path | str,
    ingest_id: str,
    *,
    ingest_config: IngestConfig | None = None,
) -> ArchiveResult:
    paths = vault_paths(vault_path)
    rows = connection.execute(
        """
        SELECT ingest_item_id, doc_id, source_uri, normalized_source_uri
        FROM ingest_items
        WHERE ingest_id = ? AND status = 'pending'
        ORDER BY source_uri
        """,
        (ingest_id,),
    ).fetchall()

    archived: list[ArchivedSource] = []
    failed = 0
    for row in rows:
        try:
            inspection = inspect_source(
                row["normalized_source_uri"],
                source_uri=row["source_uri"],
                ingest_config=ingest_config,
            )
            if not inspection.is_supported:
                continue
            archived.append(
                _archive_one(
                    connection,
                    paths,
                    row["ingest_item_id"],
                    inspection,
                    existing_doc_id=row["doc_id"],
                )
            )
        except Exception as exc:
            failed += 1
            _mark_archive_failed(connection, row["ingest_item_id"], exc)

    _update_ingest_run_after_archive(connection, ingest_id, len(archived), failed)
    connection.commit()
    return ArchiveResult(
        ingest_id=ingest_id,
        archived_items=tuple(archived),
        failed_items=failed,
    )


def _archive_one(
    connection: sqlite3.Connection,
    paths: VaultPaths,
    ingest_item_id: str,
    inspection: SourceInspection,
    *,
    existing_doc_id: str | None = None,
) -> ArchivedSource:
    doc_id = existing_doc_id or new_doc_id()
    source_file_id = new_prefixed_id("source_file")
    now = utc_now_iso()
    original_abs = _available_original_path(paths, doc_id, inspection.original_ext, source_file_id)
    original_abs.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(inspection.path, original_abs)
    original_rel = paths.relative_to_vault(original_abs)
    title = inspection.path.stem
    filename_slug = slugify(title)

    if existing_doc_id is None:
        connection.execute(
            """
            INSERT INTO documents(
              doc_id, current_revision_id, title, original_title, filename_slug,
              status, source_type, source_uri, normalized_source_uri, source_hash,
              canonical_path, original_path, language, category_id, quality_status,
              quality_signals_json, needs_review, ingest_status, fts_status,
              embedding_status, classification_status, created_at, updated_at
            )
            VALUES (
              ?, NULL, ?, ?, ?, 'active', ?, ?, ?, ?, NULL, ?, NULL,
              'cat_uncategorized', NULL, NULL, 0, 'archived', 'not_indexed',
              'not_applicable', NULL, ?, ?
            )
            """,
            (
                doc_id,
                title,
                title,
                filename_slug,
                inspection.original_ext,
                inspection.source_uri,
                inspection.normalized_source_uri,
                inspection.source_hash,
                original_rel,
                now,
                now,
            ),
        )
    else:
        connection.execute(
            """
            UPDATE documents
            SET source_type = ?,
                source_uri = ?,
                normalized_source_uri = ?,
                source_hash = ?,
                original_path = ?,
                ingest_status = 'archived',
                updated_at = ?
            WHERE doc_id = ?
            """,
            (
                inspection.original_ext,
                inspection.source_uri,
                inspection.normalized_source_uri,
                inspection.source_hash,
                original_rel,
                now,
                doc_id,
            ),
        )
    connection.execute(
        """
        INSERT INTO source_files(
          source_file_id, doc_id, source_uri, normalized_source_uri, original_filename,
          original_ext, mime_type, size_bytes, source_hash, original_path, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_file_id,
            doc_id,
            inspection.source_uri,
            inspection.normalized_source_uri,
            inspection.original_filename,
            inspection.original_ext,
            inspection.mime_type,
            inspection.size_bytes,
            inspection.source_hash,
            original_rel,
            now,
            now,
        ),
    )
    connection.execute(
        """
        UPDATE ingest_items
        SET doc_id = ?, status = 'running', updated_at = ?
        WHERE ingest_item_id = ?
        """,
        (doc_id, now, ingest_item_id),
    )
    return ArchivedSource(
        ingest_item_id=ingest_item_id,
        doc_id=doc_id,
        source_file_id=source_file_id,
        original_path=original_rel,
    )


def _available_original_path(
    paths: VaultPaths,
    doc_id: str,
    extension: str,
    source_file_id: str,
) -> Path:
    primary = paths.original_path(doc_id, extension)
    if not primary.exists():
        return primary
    clean_extension = extension.lower().lstrip(".") or "bin"
    return paths.original_dir(doc_id) / f"original__{source_file_id}.{clean_extension}"


def _mark_archive_failed(
    connection: sqlite3.Connection,
    ingest_item_id: str,
    exc: Exception,
) -> None:
    now = utc_now_iso()
    error_id = record_error(
        connection,
        component="source_archiver",
        error_type=type(exc).__name__,
        message=str(exc),
        user_message="Failed to archive original source file.",
        retryable=True,
        payload={"ingest_item_id": ingest_item_id},
    )
    connection.execute(
        """
        UPDATE ingest_items
        SET status = 'failed', error_id = ?, updated_at = ?, finished_at = ?
        WHERE ingest_item_id = ?
        """,
        (error_id, now, now, ingest_item_id),
    )


def _update_ingest_run_after_archive(
    connection: sqlite3.Connection,
    ingest_id: str,
    archived_count: int,
    failed_count: int,
) -> None:
    row = connection.execute(
        """
        SELECT unsupported_items
        FROM ingest_runs
        WHERE ingest_id = ?
        """,
        (ingest_id,),
    ).fetchone()
    if row is None:
        return
    unsupported_count = int(row["unsupported_items"] or 0)
    if failed_count > 0 and archived_count == 0:
        status = "completed_with_issues"
    elif failed_count > 0 or unsupported_count > 0:
        status = "running"
    elif archived_count > 0:
        status = "running"
    else:
        status = "pending"

    connection.execute(
        """
        UPDATE ingest_runs
        SET status = ?, failed_items = COALESCE(failed_items, 0) + ?, updated_at = ?
        WHERE ingest_id = ?
        """,
        (status, failed_count, utc_now_iso(), ingest_id),
    )
