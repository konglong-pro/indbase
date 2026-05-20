"""SQLite FTS indexing services."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3

from indbase_core.errors import record_error
from indbase_core.paths import vault_paths
from indbase_core.reviews import create_review_item
from indbase_core.search_text import build_fts_text
from indbase_core.time import utc_now_iso


@dataclass(frozen=True)
class FtsIndexFailure:
    doc_id: str
    reason: str
    error_id: str


@dataclass(frozen=True)
class FtsRebuildResult:
    active_documents: int
    indexed_documents: int
    indexed_chunks: int
    failed_documents: int
    failures: tuple[FtsIndexFailure, ...]


def rebuild_fts_index(connection: sqlite3.Connection, vault_path: Path | str) -> FtsRebuildResult:
    paths = vault_paths(vault_path)
    now = utc_now_iso()
    active_documents = _active_documents(connection)

    connection.execute("DELETE FROM chunks_fts")
    connection.execute(
        """
        UPDATE documents
        SET fts_status = 'not_indexed', updated_at = ?
        WHERE status = 'active'
          AND ingest_status = 'revisioned'
          AND deleted_at IS NULL
        """,
        (now,),
    )

    indexed_documents = 0
    indexed_chunks = 0
    failures: list[FtsIndexFailure] = []
    for document in active_documents:
        validation_failure = _validate_document_for_fts(connection, paths.root, document)
        if validation_failure is not None:
            failures.append(
                _record_index_failure(
                    connection,
                    doc_id=str(document["doc_id"]),
                    reason=validation_failure,
                )
            )
            continue

        chunks = _current_chunks(connection, str(document["doc_id"]), str(document["current_revision_id"]))
        for chunk in chunks:
            connection.execute(
                """
                INSERT INTO chunks_fts(chunk_id, doc_id, revision_id, title, heading_path, text, tags, category)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk["chunk_id"],
                    document["doc_id"],
                    document["current_revision_id"],
                    build_fts_text(document["title"]),
                    build_fts_text(_heading_path_text(chunk["heading_path_json"])),
                    build_fts_text(chunk["text"]),
                    build_fts_text(_document_tags(connection, str(document["doc_id"]))),
                    build_fts_text(document["category_name"]),
                ),
            )
        connection.execute(
            """
            UPDATE documents
            SET fts_status = 'indexed', updated_at = ?
            WHERE doc_id = ?
            """,
            (utc_now_iso(), document["doc_id"]),
        )
        indexed_documents += 1
        indexed_chunks += len(chunks)

    connection.commit()
    return FtsRebuildResult(
        active_documents=len(active_documents),
        indexed_documents=indexed_documents,
        indexed_chunks=indexed_chunks,
        failed_documents=len(failures),
        failures=tuple(failures),
    )


def reindex_document_fts(connection: sqlite3.Connection, vault_path: Path | str, doc_id: str) -> None:
    """Rebuild FTS rows for one document's current revision only."""
    refresh_document_fts_metadata(connection, doc_id)
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE documents
        SET fts_status = 'indexed', updated_at = ?
        WHERE doc_id = ?
        """,
        (now, doc_id),
    )


def refresh_document_fts_metadata(connection: sqlite3.Connection, doc_id: str) -> None:
    """Refresh title/category/tag FTS columns for one document's current chunks."""
    document = connection.execute(
        """
        SELECT d.doc_id, d.current_revision_id, d.title, c.name AS category_name
        FROM documents d
        LEFT JOIN categories c ON c.category_id = d.category_id
        WHERE d.doc_id = ?
          AND d.deleted_at IS NULL
        """,
        (doc_id,),
    ).fetchone()
    if document is None or document["current_revision_id"] is None:
        return

    chunks = _current_chunks(connection, doc_id, str(document["current_revision_id"]))
    if not chunks:
        return

    connection.execute("DELETE FROM chunks_fts WHERE doc_id = ?", (doc_id,))
    tags = build_fts_text(_document_tags(connection, doc_id))
    category = build_fts_text(document["category_name"])
    title = build_fts_text(document["title"])
    for chunk in chunks:
        connection.execute(
            """
            INSERT INTO chunks_fts(chunk_id, doc_id, revision_id, title, heading_path, text, tags, category)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chunk["chunk_id"],
                doc_id,
                document["current_revision_id"],
                title,
                build_fts_text(_heading_path_text(chunk["heading_path_json"])),
                build_fts_text(chunk["text"]),
                tags,
                category,
            ),
        )


def _active_documents(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT d.doc_id, d.current_revision_id, d.title, d.status,
                   dr.markdown_path,
                   c.name AS category_name
            FROM documents d
            LEFT JOIN document_revisions dr ON dr.revision_id = d.current_revision_id
            LEFT JOIN categories c ON c.category_id = d.category_id
            WHERE d.status = 'active'
              AND d.ingest_status = 'revisioned'
              AND d.deleted_at IS NULL
            ORDER BY d.created_at, d.doc_id
            """
        )
    )


def _validate_document_for_fts(
    connection: sqlite3.Connection,
    vault_root: Path,
    document: sqlite3.Row,
) -> str | None:
    doc_id = str(document["doc_id"])
    revision_id = document["current_revision_id"]
    if revision_id is None:
        return "missing_current_revision"
    markdown_path = document["markdown_path"]
    if markdown_path is None:
        return "missing_current_revision_record"
    if not (vault_root / markdown_path).is_file():
        return "missing_revision_markdown"
    chunk_count = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM chunks
        WHERE doc_id = ?
          AND revision_id = ?
          AND is_current = 1
          AND deleted_at IS NULL
        """,
        (doc_id, revision_id),
    ).fetchone()["count"]
    if int(chunk_count or 0) == 0:
        return "missing_current_chunks"
    return None


def _current_chunks(connection: sqlite3.Connection, doc_id: str, revision_id: str) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT chunk_id, heading_path_json, text
            FROM chunks
            WHERE doc_id = ?
              AND revision_id = ?
              AND is_current = 1
              AND deleted_at IS NULL
            ORDER BY sequence
            """,
            (doc_id, revision_id),
        )
    )


def _record_index_failure(
    connection: sqlite3.Connection,
    *,
    doc_id: str,
    reason: str,
) -> FtsIndexFailure:
    now = utc_now_iso()
    message = f"FTS indexing failed for {doc_id}: {reason}."
    error_id = record_error(
        connection,
        component="fts_indexer",
        error_type=reason,
        message=message,
        user_message="Document could not be indexed for search.",
        retryable=True,
        payload={"doc_id": doc_id, "reason": reason},
    )
    _create_review_once(
        connection,
        review_type="indexing_failed",
        target_type="document",
        target_id=doc_id,
        reason=message,
    )
    connection.execute(
        """
        UPDATE documents
        SET fts_status = 'failed', updated_at = ?
        WHERE doc_id = ?
        """,
        (now, doc_id),
    )
    return FtsIndexFailure(doc_id=doc_id, reason=reason, error_id=error_id)


def _create_review_once(
    connection: sqlite3.Connection,
    *,
    review_type: str,
    target_type: str,
    target_id: str,
    reason: str,
) -> None:
    existing = connection.execute(
        """
        SELECT review_id
        FROM review_items
        WHERE type = ?
          AND target_type = ?
          AND target_id = ?
          AND reason = ?
          AND status = 'pending'
        LIMIT 1
        """,
        (review_type, target_type, target_id, reason),
    ).fetchone()
    if existing is not None:
        return
    create_review_item(
        connection,
        review_type=review_type,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        priority=30,
    )


def _heading_path_text(heading_path_json: str | None) -> str:
    if not heading_path_json:
        return ""
    parsed = json.loads(heading_path_json)
    if not isinstance(parsed, list):
        return ""
    return " ".join(str(value) for value in parsed)


def _document_tags(connection: sqlite3.Connection, doc_id: str) -> str:
    rows = connection.execute(
        """
        SELECT t.name
        FROM document_tags dt
        JOIN tags t ON t.tag_id = dt.tag_id
        WHERE dt.doc_id = ?
          AND dt.deleted_at IS NULL
          AND t.deleted_at IS NULL
        ORDER BY t.name
        """,
        (doc_id,),
    ).fetchall()
    return " ".join(str(row["name"]) for row in rows)
