"""Manual tag operations for v0.1."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from indbase_core.ids import new_prefixed_id
from indbase_core.indexer import refresh_document_fts_metadata
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
    description: str | None = None,
    language: str | None = None,
) -> str:
    clean_name = _clean_tag_name(name)
    normalized = normalize_tag_name(clean_name)
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
          tag_id, name, normalized_name, description, language, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (tag_id, clean_name, normalized, description, language, now, now),
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
                SELECT tag_id, name, normalized_name, description, language, created_at, deleted_at
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
            SELECT tag_id, name, normalized_name, description, language, created_at, deleted_at
            FROM tags
            WHERE deleted_at IS NULL
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
    if row["deleted_at"] is not None:
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
    if row["deleted_at"] is not None:
        return TagChange(tag_id=tag_id, tag_name=str(row["name"]), changed=False)
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE tags
        SET deleted_at = ?, updated_at = ?
        WHERE tag_id = ?
        """,
        (now, now, tag_id),
    )
    _refresh_linked_documents_fts_metadata(connection, tag_id)
    connection.commit()
    return TagChange(tag_id=tag_id, tag_name=str(row["name"]), changed=True)


def restore_tag(connection: sqlite3.Connection, tag_id: str) -> TagChange:
    row = _load_tag(connection, tag_id)
    if row is None:
        raise ValueError(f"Tag not found: {tag_id}")
    if row["deleted_at"] is None:
        return TagChange(tag_id=tag_id, tag_name=str(row["name"]), changed=False)
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE tags
        SET deleted_at = NULL, updated_at = ?
        WHERE tag_id = ?
        """,
        (now, tag_id),
    )
    _refresh_linked_documents_fts_metadata(connection, tag_id)
    connection.commit()
    return TagChange(tag_id=tag_id, tag_name=str(row["name"]), changed=True)


def add_document_tag(
    connection: sqlite3.Connection,
    doc_id: str,
    tag_name: str,
) -> DocumentTagChange:
    _require_document(connection, doc_id)
    tag_id = add_tag(connection, tag_name)
    now = utc_now_iso()
    existing = connection.execute(
        """
        SELECT deleted_at
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
              doc_id, tag_id, source, confidence, created_at, updated_at
            )
            VALUES (?, ?, 'manual', 1.0, ?, ?)
            """,
            (doc_id, tag_id, now, now),
        )
        changed = True
    elif existing["deleted_at"] is not None:
        connection.execute(
            """
            UPDATE document_tags
            SET source = 'manual', confidence = 1.0, deleted_at = NULL, updated_at = ?
            WHERE doc_id = ?
              AND tag_id = ?
            """,
            (now, doc_id, tag_id),
        )
        changed = True
    else:
        changed = False
    if changed:
        refresh_document_fts_metadata(connection, doc_id)
    connection.commit()
    return DocumentTagChange(doc_id=doc_id, tag_id=tag_id, tag_name=_clean_tag_name(tag_name), changed=changed)


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
        SELECT t.tag_id, t.name, dt.deleted_at
        FROM tags t
        LEFT JOIN document_tags dt ON dt.tag_id = t.tag_id AND dt.doc_id = ?
        WHERE t.normalized_name = ?
          AND t.deleted_at IS NULL
        """,
        (doc_id, normalized),
    ).fetchone()
    if row is None:
        raise ValueError(f"Tag not found: {clean_name}")
    changed = row["deleted_at"] is None
    if changed:
        now = utc_now_iso()
        connection.execute(
            """
            UPDATE document_tags
            SET deleted_at = ?, updated_at = ?
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
            SELECT t.tag_id, t.name, t.normalized_name, dt.source, dt.created_at
            FROM document_tags dt
            JOIN tags t ON t.tag_id = dt.tag_id
            WHERE dt.doc_id = ?
              AND dt.deleted_at IS NULL
              AND t.deleted_at IS NULL
            ORDER BY t.name
            """,
            (doc_id,),
        )
    )


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


def _load_tag(connection: sqlite3.Connection, tag_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT tag_id, name, deleted_at
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
        """,
        (tag_id,),
    ).fetchall()
    for row in rows:
        refresh_document_fts_metadata(connection, str(row["doc_id"]))
