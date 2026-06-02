"""Manual tag operations."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3

from indbase_core.ids import new_prefixed_id
from indbase_core.indexer import refresh_document_fts_metadata
from indbase_core.taxonomy import (
    ASSIGNABLE_TAG_STATUSES,
    validate_document_tag_source,
    validate_tag_type,
)
from indbase_core.time import utc_now_iso


@dataclass(frozen=True)
class DocumentTagChange:
    doc_id: str
    tag_id: str
    tag_name: str
    changed: bool


@dataclass(frozen=True)
class TagChange:
    tag_id: str
    tag_name: str
    changed: bool


def add_tag(
    connection: sqlite3.Connection,
    name: str,
    *,
    tag_type: str,
    description: str | None = None,
    language: str | None = None,
    created_by: str = "manual",
) -> str:
    clean_name = _clean_tag_name(name)
    normalized = normalize_tag_name(clean_name)
    clean_type = validate_tag_type(tag_type)
    existing = connection.execute(
        """
        SELECT tag_id
        FROM tags
        WHERE normalized_name = ?
          AND deleted_at IS NULL
        """,
        (normalized,),
    ).fetchone()
    if existing is not None:
        return str(existing["tag_id"])

    tag_id = new_prefixed_id("tag")
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO tags(
          tag_id, name, normalized_name, description, language,
          type, status, created_by, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
        """,
        (tag_id, clean_name, normalized, description, language, clean_type, created_by, now, now),
    )
    connection.execute(
        """
        INSERT INTO tag_lifecycle_events(
          event_id, tag_id, event_type, payload_json, created_by, created_at
        )
        VALUES (?, ?, 'created', ?, ?, ?)
        """,
        (
            new_prefixed_id("tagevent"),
            tag_id,
            json.dumps({"name": clean_name, "type": clean_type}, ensure_ascii=False),
            created_by,
            now,
        ),
    )
    connection.commit()
    return tag_id


def list_tags(
    connection: sqlite3.Connection,
    *,
    limit: int = 50,
    include_inactive: bool = False,
) -> list[sqlite3.Row]:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if include_inactive:
        return list(
            connection.execute(
                """
                SELECT tag_id, name, normalized_name, type, status,
                       description, language, created_at, deleted_at
                FROM tags
                ORDER BY name
                LIMIT ?
                """,
                (limit,),
            )
        )
    return list(
        connection.execute(
            """
            SELECT tag_id, name, normalized_name, type, status,
                   description, language, created_at, deleted_at
            FROM tags
            WHERE deleted_at IS NULL
              AND status = 'active'
            ORDER BY name
            LIMIT ?
            """,
            (limit,),
        )
    )


def update_tag(
    connection: sqlite3.Connection,
    tag_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    language: str | None = None,
) -> TagChange:
    row = _load_tag(connection, tag_id)
    if row is None:
        raise ValueError(f"Tag not found: {tag_id}")
    if row["deleted_at"] is not None or str(row["status"]) != "active":
        raise ValueError(f"Tag is archived: {tag_id}")
    updates: list[str] = []
    values: list[object] = []
    tag_name = str(row["name"])
    if name is not None:
        clean_name = _clean_tag_name(name)
        normalized = normalize_tag_name(clean_name)
        conflict = connection.execute(
            """
            SELECT tag_id
            FROM tags
            WHERE normalized_name = ?
              AND tag_id != ?
            """,
            (normalized, tag_id),
        ).fetchone()
        if conflict is not None:
            raise ValueError(f"Tag name already exists: {clean_name}")
        updates.extend(["name = ?", "normalized_name = ?"])
        values.extend([clean_name, normalized])
        tag_name = clean_name
    if description is not None:
        updates.append("description = ?")
        values.append(description)
    if language is not None:
        updates.append("language = ?")
        values.append(language)
    if not updates:
        return TagChange(tag_id=tag_id, tag_name=tag_name, changed=False)

    updates.append("updated_at = ?")
    values.append(utc_now_iso())
    values.append(tag_id)
    connection.execute(
        f"""
        UPDATE tags
        SET {", ".join(updates)}
        WHERE tag_id = ?
        """,
        values,
    )
    _refresh_linked_documents_fts_metadata(connection, tag_id)
    connection.commit()
    return TagChange(tag_id=tag_id, tag_name=tag_name, changed=True)


def archive_tag(connection: sqlite3.Connection, tag_id: str) -> TagChange:
    row = _load_tag(connection, tag_id)
    if row is None:
        raise ValueError(f"Tag not found: {tag_id}")
    if row["deleted_at"] is not None and str(row["status"]) == "archived":
        return TagChange(tag_id=tag_id, tag_name=str(row["name"]), changed=False)
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE tags
        SET deleted_at = ?, status = 'archived', updated_at = ?
        WHERE tag_id = ?
        """,
        (now, now, tag_id),
    )
    connection.execute(
        """
        INSERT INTO tag_lifecycle_events(
          event_id, tag_id, event_type, payload_json, created_by, created_at
        )
        VALUES (?, ?, 'archived', NULL, 'manual', ?)
        """,
        (new_prefixed_id("tagevent"), tag_id, now),
    )
    _refresh_linked_documents_fts_metadata(connection, tag_id)
    connection.commit()
    return TagChange(tag_id=tag_id, tag_name=str(row["name"]), changed=True)


def restore_tag(connection: sqlite3.Connection, tag_id: str) -> TagChange:
    row = _load_tag(connection, tag_id)
    if row is None:
        raise ValueError(f"Tag not found: {tag_id}")
    if row["deleted_at"] is None and str(row["status"]) == "active":
        return TagChange(tag_id=tag_id, tag_name=str(row["name"]), changed=False)
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE tags
        SET deleted_at = NULL, status = 'active', updated_at = ?
        WHERE tag_id = ?
        """,
        (now, tag_id),
    )
    connection.execute(
        """
        INSERT INTO tag_lifecycle_events(
          event_id, tag_id, event_type, payload_json, created_by, created_at
        )
        VALUES (?, ?, 'restored', NULL, 'manual', ?)
        """,
        (new_prefixed_id("tagevent"), tag_id, now),
    )
    _refresh_linked_documents_fts_metadata(connection, tag_id)
    connection.commit()
    return TagChange(tag_id=tag_id, tag_name=str(row["name"]), changed=True)


def attach_document_tag_by_id(
    connection: sqlite3.Connection,
    doc_id: str,
    tag_id: str,
    *,
    source: str = "manual",
    confidence: float = 1.0,
    created_by: str = "manual",
    revision_id: str | None = None,
    suggestion_id: str | None = None,
    evidence_chunk_ids: list[str] | None = None,
    candidate_id: str | None = None,
) -> DocumentTagChange:
    _require_document(connection, doc_id)
    clean_source = validate_document_tag_source(source)
    tag_row = connection.execute(
        """
        SELECT tag_id, name, status
        FROM tags
        WHERE tag_id = ?
          AND deleted_at IS NULL
        """,
        (tag_id,),
    ).fetchone()
    if tag_row is None:
        raise ValueError(f"Tag not found: {tag_id}")
    if str(tag_row["status"]) not in ASSIGNABLE_TAG_STATUSES:
        raise ValueError(f"Tag is not assignable: {tag_id} (status={tag_row['status']})")
    if revision_id is None:
        revision_id = _current_revision_id(connection, doc_id)
    now = utc_now_iso()
    evidence_json = json.dumps(evidence_chunk_ids or [], ensure_ascii=False)
    existing = connection.execute(
        """
        SELECT deleted_at, status
        FROM document_tags
        WHERE doc_id = ?
          AND tag_id = ?
        """,
        (doc_id, tag_id),
    ).fetchone()
    if existing is None:
        connection.execute(
            """
            INSERT INTO document_tags(
              doc_id, tag_id, revision_id, source, confidence,
              evidence_chunk_ids_json, suggestion_id, candidate_id, status, created_by,
              created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (
                doc_id,
                tag_id,
                revision_id,
                clean_source,
                confidence,
                evidence_json,
                suggestion_id,
                candidate_id,
                created_by,
                now,
                now,
            ),
        )
        changed = True
    elif existing["deleted_at"] is not None or str(existing["status"]) != "active":
        connection.execute(
            """
            UPDATE document_tags
            SET revision_id = ?, source = ?, confidence = ?,
                evidence_chunk_ids_json = ?, suggestion_id = ?, candidate_id = ?,
                status = 'active', created_by = ?, deleted_at = NULL, updated_at = ?
            WHERE doc_id = ?
              AND tag_id = ?
            """,
            (
                revision_id,
                clean_source,
                confidence,
                evidence_json,
                suggestion_id,
                candidate_id,
                created_by,
                now,
                doc_id,
                tag_id,
            ),
        )
        changed = True
    else:
        changed = False
    if changed:
        refresh_document_fts_metadata(connection, doc_id)
    connection.commit()
    return DocumentTagChange(
        doc_id=doc_id,
        tag_id=tag_id,
        tag_name=str(tag_row["name"]),
        changed=changed,
    )


def add_document_tag(
    connection: sqlite3.Connection,
    doc_id: str,
    tag_name: str,
    *,
    source: str = "manual",
    confidence: float = 1.0,
    created_by: str = "manual",
    revision_id: str | None = None,
    suggestion_id: str | None = None,
    evidence_chunk_ids: list[str] | None = None,
    candidate_id: str | None = None,
) -> DocumentTagChange:
    _require_document(connection, doc_id)
    clean_name = _clean_tag_name(tag_name)
    normalized = normalize_tag_name(clean_name)
    clean_source = validate_document_tag_source(source)
    tag_row = connection.execute(
        """
        SELECT tag_id, name, status
        FROM tags
        WHERE normalized_name = ?
          AND deleted_at IS NULL
        """,
        (normalized,),
    ).fetchone()
    if tag_row is None:
        raise ValueError(
            f"Tag not found: {clean_name}. Create it first with `indb tag add --type <type> {clean_name}`."
        )
    if str(tag_row["status"]) not in ASSIGNABLE_TAG_STATUSES:
        raise ValueError(f"Tag is not assignable: {clean_name} (status={tag_row['status']})")

    return attach_document_tag_by_id(
        connection,
        doc_id,
        str(tag_row["tag_id"]),
        source=clean_source,
        confidence=confidence,
        created_by=created_by,
        revision_id=revision_id,
        suggestion_id=suggestion_id,
        evidence_chunk_ids=evidence_chunk_ids,
        candidate_id=candidate_id,
    )


def remove_document_tag(
    connection: sqlite3.Connection,
    doc_id: str,
    tag_name: str,
) -> DocumentTagChange:
    _require_document(connection, doc_id)
    clean_name = _clean_tag_name(tag_name)
    normalized = normalize_tag_name(clean_name)
    row = connection.execute(
        """
        SELECT t.tag_id, t.name, dt.deleted_at, dt.status
        FROM tags t
        LEFT JOIN document_tags dt ON dt.tag_id = t.tag_id AND dt.doc_id = ?
        WHERE t.normalized_name = ?
          AND t.deleted_at IS NULL
        """,
        (doc_id, normalized),
    ).fetchone()
    if row is None:
        raise ValueError(f"Tag not found: {clean_name}")
    changed = row["deleted_at"] is None and str(row["status"] or "active") == "active"
    if changed:
        now = utc_now_iso()
        connection.execute(
            """
            UPDATE document_tags
            SET deleted_at = ?, status = 'removed', updated_at = ?
            WHERE doc_id = ?
              AND tag_id = ?
              AND deleted_at IS NULL
            """,
            (now, now, doc_id, row["tag_id"]),
        )
        refresh_document_fts_metadata(connection, doc_id)
    connection.commit()
    return DocumentTagChange(doc_id=doc_id, tag_id=str(row["tag_id"]), tag_name=str(row["name"]), changed=changed)


def list_document_tags(connection: sqlite3.Connection, doc_id: str) -> list[sqlite3.Row]:
    _require_document(connection, doc_id)
    return list(
        connection.execute(
            """
            SELECT t.tag_id, t.name, t.normalized_name, t.type, dt.source, dt.created_at
            FROM document_tags dt
            JOIN tags t ON t.tag_id = dt.tag_id
            WHERE dt.doc_id = ?
              AND dt.deleted_at IS NULL
              AND dt.status = 'active'
              AND t.deleted_at IS NULL
              AND t.status = 'active'
            ORDER BY t.name
            """,
            (doc_id,),
        )
    )


def get_tag_by_normalized_name(
    connection: sqlite3.Connection,
    normalized_name: str,
    *,
    include_inactive: bool = False,
) -> sqlite3.Row | None:
    if include_inactive:
        return connection.execute(
            """
            SELECT tag_id, name, normalized_name, type, status
            FROM tags
            WHERE normalized_name = ?
            """,
            (normalized_name,),
        ).fetchone()
    return connection.execute(
        """
        SELECT tag_id, name, normalized_name, type, status
        FROM tags
        WHERE normalized_name = ?
          AND deleted_at IS NULL
          AND status = 'active'
        """,
        (normalized_name,),
    ).fetchone()


def normalize_tag_name(name: str) -> str:
    return " ".join(name.strip().casefold().split())


def _clean_tag_name(name: str) -> str:
    clean = " ".join(name.strip().split())
    if not clean:
        raise ValueError("Tag name must not be empty.")
    return clean


def _require_document(connection: sqlite3.Connection, doc_id: str) -> None:
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


def _current_revision_id(connection: sqlite3.Connection, doc_id: str) -> str | None:
    row = connection.execute(
        "SELECT current_revision_id FROM documents WHERE doc_id = ?",
        (doc_id,),
    ).fetchone()
    if row is None:
        return None
    return row["current_revision_id"]


def _load_tag(connection: sqlite3.Connection, tag_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT tag_id, name, status, deleted_at
        FROM tags
        WHERE tag_id = ?
        """,
        (tag_id,),
    ).fetchone()


def _refresh_linked_documents_fts_metadata(connection: sqlite3.Connection, tag_id: str) -> None:
    rows = connection.execute(
        """
        SELECT doc_id
        FROM document_tags
        WHERE tag_id = ?
          AND deleted_at IS NULL
          AND status = 'active'
        """,
        (tag_id,),
    ).fetchall()
    for row in rows:
        refresh_document_fts_metadata(connection, str(row["doc_id"]))
