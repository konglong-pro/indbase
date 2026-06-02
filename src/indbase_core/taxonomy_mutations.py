"""Governed tag alias and lifecycle mutations."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3

from indbase_core.ids import new_prefixed_id
from indbase_core.indexer import refresh_document_fts_metadata
from indbase_core.tag_feedback import record_tag_feedback
from indbase_core.tag_governance import record_tag_governance_event
from indbase_core.tags import archive_tag, normalize_tag_name
from indbase_core.time import utc_now_iso


@dataclass(frozen=True)
class TagMutationResult:
    tag_id: str
    changed: bool
    detail: str = ""


def add_tag_alias(
    connection: sqlite3.Connection,
    tag_id: str,
    alias: str,
    *,
    created_by: str = "manual",
) -> TagMutationResult:
    row = connection.execute(
        """
        SELECT tag_id, name, status
        FROM tags
        WHERE tag_id = ?
          AND deleted_at IS NULL
        """,
        (tag_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Tag not found: {tag_id}")
    if str(row["status"]) != "active":
        raise ValueError(f"Tag is not active: {tag_id}")

    clean_alias = " ".join(alias.strip().split())
    if not clean_alias:
        raise ValueError("Alias must not be empty.")
    normalized = normalize_tag_name(clean_alias)
    conflict = connection.execute(
        """
        SELECT alias_id, tag_id, status
        FROM tag_aliases
        WHERE normalized_alias = ?
        """,
        (normalized,),
    ).fetchone()
    if conflict is not None and str(conflict["tag_id"]) != tag_id:
        raise ValueError(f"Alias already assigned to another tag: {clean_alias}")

    now = utc_now_iso()
    if conflict is not None:
        connection.execute(
            """
            UPDATE tag_aliases
            SET alias = ?, status = 'active', deleted_at = NULL, updated_at = ?
            WHERE alias_id = ?
            """,
            (clean_alias, now, conflict["alias_id"]),
        )
        changed = str(conflict["status"]) != "active"
    else:
        connection.execute(
            """
            INSERT INTO tag_aliases(
              alias_id, tag_id, alias, normalized_alias, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'active', ?, ?)
            """,
            (new_prefixed_id("alias"), tag_id, clean_alias, normalized, now, now),
        )
        changed = True

    connection.execute(
        """
        INSERT INTO tag_lifecycle_events(
          event_id, tag_id, event_type, payload_json, created_by, created_at
        )
        VALUES (?, ?, 'alias_added', ?, ?, ?)
        """,
        (
            new_prefixed_id("tagevent"),
            tag_id,
            json.dumps({"alias": clean_alias}, ensure_ascii=False),
            created_by,
            now,
        ),
    )
    record_tag_feedback(
        connection,
        "alias_added",
        tag_id=tag_id,
        new={"alias": clean_alias},
        created_by=created_by,
    )
    record_tag_governance_event(
        connection,
        "alias_added",
        tag_id=tag_id,
        payload={"alias": clean_alias},
        created_by=created_by,
    )
    connection.commit()
    return TagMutationResult(tag_id=tag_id, changed=changed, detail=clean_alias)


def merge_tags(
    connection: sqlite3.Connection,
    source_tag_id: str,
    target_tag_id: str,
    *,
    created_by: str = "manual",
) -> TagMutationResult:
    if source_tag_id == target_tag_id:
        raise ValueError("Source and target tags must differ.")
    source = _require_active_tag(connection, source_tag_id)
    target = _require_active_tag(connection, target_tag_id)
    now = utc_now_iso()

    assignments = connection.execute(
        """
        SELECT doc_id
        FROM document_tags
        WHERE tag_id = ?
          AND deleted_at IS NULL
          AND status = 'active'
        """,
        (source_tag_id,),
    ).fetchall()
    for row in assignments:
        doc_id = str(row["doc_id"])
        existing = connection.execute(
            """
            SELECT 1
            FROM document_tags
            WHERE doc_id = ?
              AND tag_id = ?
              AND deleted_at IS NULL
              AND status = 'active'
            """,
            (doc_id, target_tag_id),
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO document_tags(
                  doc_id, tag_id, source, confidence, status, created_by, created_at, updated_at
                )
                VALUES (?, ?, 'manual', 1.0, 'active', ?, ?, ?)
                """,
                (doc_id, target_tag_id, created_by, now, now),
            )
        connection.execute(
            """
            UPDATE document_tags
            SET deleted_at = ?, status = 'removed', updated_at = ?
            WHERE doc_id = ?
              AND tag_id = ?
              AND deleted_at IS NULL
            """,
            (now, now, doc_id, source_tag_id),
        )
        refresh_document_fts_metadata(connection, doc_id)

    add_tag_alias(connection, target_tag_id, str(source["name"]), created_by=created_by)
    connection.execute(
        """
        UPDATE tags
        SET status = 'deprecated', updated_at = ?
        WHERE tag_id = ?
        """,
        (now, source_tag_id),
    )
    connection.execute(
        """
        INSERT INTO tag_lifecycle_events(
          event_id, tag_id, event_type, payload_json, created_by, created_at
        )
        VALUES (?, ?, 'merged', ?, ?, ?)
        """,
        (
            new_prefixed_id("tagevent"),
            source_tag_id,
            json.dumps({"target_tag_id": target_tag_id}, ensure_ascii=False),
            created_by,
            now,
        ),
    )
    record_tag_feedback(
        connection,
        "merged",
        tag_id=source_tag_id,
        new={"target_tag_id": target_tag_id},
        created_by=created_by,
    )
    record_tag_governance_event(
        connection,
        "merged",
        tag_id=source_tag_id,
        payload={"target_tag_id": target_tag_id},
        created_by=created_by,
    )
    connection.commit()
    return TagMutationResult(tag_id=source_tag_id, changed=True, detail=f"merged into {target_tag_id}")


def deprecate_tag(connection: sqlite3.Connection, tag_id: str, *, created_by: str = "manual") -> TagMutationResult:
    row = _require_active_tag(connection, tag_id)
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE tags
        SET status = 'deprecated', updated_at = ?
        WHERE tag_id = ?
        """,
        (now, tag_id),
    )
    connection.execute(
        """
        INSERT INTO tag_lifecycle_events(
          event_id, tag_id, event_type, created_by, created_at
        )
        VALUES (?, ?, 'deprecated', ?, ?)
        """,
        (new_prefixed_id("tagevent"), tag_id, created_by, now),
    )
    record_tag_feedback(
        connection,
        "deprecated",
        tag_id=tag_id,
        created_by=created_by,
    )
    record_tag_governance_event(
        connection,
        "deprecated",
        tag_id=tag_id,
        created_by=created_by,
    )
    connection.commit()
    return TagMutationResult(tag_id=tag_id, changed=True, detail=str(row["name"]))


def archive_tag_mutation(connection: sqlite3.Connection, tag_id: str) -> TagMutationResult:
    result = archive_tag(connection, tag_id)
    return TagMutationResult(tag_id=tag_id, changed=result.changed, detail=result.tag_name)


def _require_active_tag(connection: sqlite3.Connection, tag_id: str) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT tag_id, name, status
        FROM tags
        WHERE tag_id = ?
          AND deleted_at IS NULL
        """,
        (tag_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Tag not found: {tag_id}")
    if str(row["status"]) != "active":
        raise ValueError(f"Tag is not active: {tag_id}")
    return row
