"""Document lifecycle operations."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from indbase_core.indexer import refresh_document_fts_metadata
from indbase_core.tags import normalize_tag_name
from indbase_core.taxonomy import validate_category_source
from indbase_core.time import utc_now_iso


@dataclass(frozen=True)
class DocumentStatusChange:
    doc_id: str
    previous_status: str
    status: str
    archived_at: str | None
    changed: bool


@dataclass(frozen=True)
class DocumentCategoryChange:
    doc_id: str
    previous_category_id: str | None
    category_id: str
    changed: bool


def archive_document(connection: sqlite3.Connection, doc_id: str) -> DocumentStatusChange:
    row = _load_document_status(connection, doc_id)
    if row is None:
        raise ValueError(f"Document not found: {doc_id}")
    previous_status = str(row["status"])
    if previous_status == "archived" and row["archived_at"]:
        return DocumentStatusChange(
            doc_id=doc_id,
            previous_status=previous_status,
            status="archived",
            archived_at=row["archived_at"],
            changed=False,
        )

    now = utc_now_iso()
    connection.execute(
        """
        UPDATE documents
        SET status = 'archived', archived_at = COALESCE(archived_at, ?), updated_at = ?
        WHERE doc_id = ?
          AND deleted_at IS NULL
        """,
        (now, now, doc_id),
    )
    connection.commit()
    return DocumentStatusChange(
        doc_id=doc_id,
        previous_status=previous_status,
        status="archived",
        archived_at=row["archived_at"] or now,
        changed=previous_status != "archived" or row["archived_at"] is None,
    )


def restore_document(connection: sqlite3.Connection, doc_id: str) -> DocumentStatusChange:
    row = _load_document_status(connection, doc_id)
    if row is None:
        raise ValueError(f"Document not found: {doc_id}")
    previous_status = str(row["status"])
    if previous_status == "active" and row["archived_at"] is None:
        return DocumentStatusChange(
            doc_id=doc_id,
            previous_status=previous_status,
            status="active",
            archived_at=None,
            changed=False,
        )

    now = utc_now_iso()
    connection.execute(
        """
        UPDATE documents
        SET status = 'active', archived_at = NULL, updated_at = ?
        WHERE doc_id = ?
          AND deleted_at IS NULL
        """,
        (now, doc_id),
    )
    refresh_document_fts_metadata(connection, doc_id)
    connection.commit()
    return DocumentStatusChange(
        doc_id=doc_id,
        previous_status=previous_status,
        status="active",
        archived_at=None,
        changed=previous_status != "active" or row["archived_at"] is not None,
    )


def set_document_category(
    connection: sqlite3.Connection,
    doc_id: str,
    category_id: str,
    *,
    category_source: str = "manual",
    category_suggestion_id: str | None = None,
    category_updated_by: str = "manual",
) -> DocumentCategoryChange:
    row = connection.execute(
        """
        SELECT doc_id, category_id
        FROM documents
        WHERE doc_id = ?
          AND deleted_at IS NULL
        """,
        (doc_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Document not found: {doc_id}")

    category = connection.execute(
        """
        SELECT category_id
        FROM categories
        WHERE category_id = ?
          AND is_active = 1
          AND deleted_at IS NULL
        """,
        (category_id,),
    ).fetchone()
    if category is None:
        raise ValueError(f"Active category not found: {category_id}")

    clean_source = validate_category_source(category_source)
    previous = row["category_id"]
    changed = previous != category_id
    if changed:
        now = utc_now_iso()
        connection.execute(
            """
            UPDATE documents
            SET category_id = ?,
                category_source = ?,
                category_suggestion_id = ?,
                category_updated_by = ?,
                category_updated_at = ?,
                updated_at = ?
            WHERE doc_id = ?
              AND deleted_at IS NULL
            """,
            (
                category_id,
                clean_source,
                category_suggestion_id,
                category_updated_by,
                now,
                now,
                doc_id,
            ),
        )
        refresh_document_fts_metadata(connection, doc_id)
        connection.commit()
    return DocumentCategoryChange(
        doc_id=doc_id,
        previous_category_id=previous,
        category_id=category_id,
        changed=changed,
    )


def list_document_revisions(connection: sqlite3.Connection, doc_id: str) -> list[sqlite3.Row]:
    row = connection.execute(
        """
        SELECT doc_id
        FROM documents
        WHERE doc_id = ?
          AND deleted_at IS NULL
        """,
        (doc_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Document not found: {doc_id}")
    return list(
        connection.execute(
            """
            SELECT dr.revision_id, dr.sequence, dr.markdown_path, dr.content_hash,
                   dr.converter_name, dr.text_length, dr.chunk_count, dr.created_at,
                   CASE WHEN d.current_revision_id = dr.revision_id THEN 1 ELSE 0 END AS is_current
            FROM document_revisions dr
            JOIN documents d ON d.doc_id = dr.doc_id
            WHERE dr.doc_id = ?
              AND dr.deleted_at IS NULL
            ORDER BY dr.sequence
            """,
            (doc_id,),
        )
    )


def list_documents(
    connection: sqlite3.Connection,
    *,
    status: str = "active",
    category_id: str | None = None,
    tag: str | None = None,
    limit: int = 50,
) -> list[sqlite3.Row]:
    """List non-deleted documents without changing document state."""
    if status not in {"active", "archived", "all"}:
        raise ValueError("status must be active, archived, or all")
    if limit < 1:
        raise ValueError("limit must be >= 1")

    clauses = ["d.deleted_at IS NULL"]
    params: list[object] = []
    if status != "all":
        clauses.append("d.status = ?")
        params.append(status)
    if category_id is not None:
        clauses.append("d.category_id = ?")
        params.append(category_id)
    if tag is not None:
        clauses.append(
            """
            EXISTS (
              SELECT 1
              FROM document_tags filter_dt
              JOIN tags filter_t ON filter_t.tag_id = filter_dt.tag_id
              WHERE filter_dt.doc_id = d.doc_id
                AND filter_dt.deleted_at IS NULL
                AND filter_t.deleted_at IS NULL
                AND filter_t.normalized_name = ?
            )
            """
        )
        params.append(normalize_tag_name(tag))
    params.append(limit)

    return list(
        connection.execute(
            f"""
            SELECT d.doc_id, d.title, d.status, d.category_id, c.name AS category_name,
                   d.current_revision_id, d.canonical_path, d.original_path,
                   d.ingest_status, d.fts_status, d.archived_at, d.created_at, d.updated_at,
                   (
                     SELECT GROUP_CONCAT(t.name, ', ')
                     FROM document_tags dt
                     JOIN tags t ON t.tag_id = dt.tag_id
                     WHERE dt.doc_id = d.doc_id
                       AND dt.deleted_at IS NULL
                       AND t.deleted_at IS NULL
                   ) AS tags
            FROM documents d
            LEFT JOIN categories c ON c.category_id = d.category_id
            WHERE {" AND ".join(clauses)}
            ORDER BY COALESCE(d.updated_at, d.created_at) DESC, d.doc_id
            LIMIT ?
            """,
            params,
        )
    )


def _load_document_status(connection: sqlite3.Connection, doc_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT doc_id, status, archived_at
        FROM documents
        WHERE doc_id = ?
          AND deleted_at IS NULL
        """,
        (doc_id,),
    ).fetchone()
