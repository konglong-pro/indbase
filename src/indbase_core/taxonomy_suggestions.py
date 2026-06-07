"""Taxonomy suggestion governance for v0.3.1."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3

from indbase_core.documents import set_document_category
from indbase_core.ids import new_prefixed_id
from indbase_core.indexer import refresh_document_fts_metadata
from indbase_core.reviews import create_review_item
from indbase_core.tags import add_document_tag
from indbase_core.time import utc_now_iso


@dataclass(frozen=True)
class TaxonomySuggestionApplyResult:
    suggestion_id: str
    doc_id: str | None
    suggestion_type: str
    status: str
    category_changed: bool = False
    tags_added: tuple[str, ...] = ()
    tag_id: str | None = None


def mark_stale_taxonomy_suggestions(connection: sqlite3.Connection) -> int:
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE taxonomy_suggestions
        SET status = 'stale', updated_at = ?
        WHERE status = 'pending'
          AND type IN ('category_assign', 'tag_assign', 'tag_candidate')
          AND doc_id IS NOT NULL
          AND revision_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1
            FROM documents d
            WHERE d.doc_id = taxonomy_suggestions.doc_id
              AND d.deleted_at IS NULL
              AND d.current_revision_id = taxonomy_suggestions.revision_id
          )
        """,
        (now,),
    )
    return int(connection.execute("SELECT changes() AS count").fetchone()["count"] or 0)


def list_taxonomy_suggestions(
    connection: sqlite3.Connection,
    *,
    suggestion_type: str | None = None,
    status: str | None = "pending",
    doc_id: str | None = None,
    limit: int = 20,
    active_current_only: bool = True,
) -> list[sqlite3.Row]:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    mark_stale_taxonomy_suggestions(connection)
    clauses: list[str] = []
    params: list[object] = []
    if suggestion_type is not None:
        clauses.append("ts.type = ?")
        params.append(suggestion_type)
    if status is not None:
        clauses.append("ts.status = ?")
        params.append(status)
    if doc_id is not None:
        clauses.append("ts.doc_id = ?")
        params.append(doc_id)
    if active_current_only:
        clauses.extend(
            [
                "d.status = 'active'",
                "d.deleted_at IS NULL",
                "d.current_revision_id = ts.revision_id",
            ]
        )
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(limit)
    return list(
        connection.execute(
            f"""
            SELECT ts.suggestion_id, ts.type, ts.doc_id, ts.revision_id, d.title,
                   ts.target_id, ts.payload_json, ts.confidence, ts.source,
                   ts.status, ts.created_at, ts.updated_at,
                   c.name AS suggested_category_name
            FROM taxonomy_suggestions ts
            LEFT JOIN documents d ON d.doc_id = ts.doc_id
            LEFT JOIN categories c ON c.category_id = json_extract(ts.payload_json, '$.category_id')
            {where}
            ORDER BY ts.created_at DESC, ts.suggestion_id
            LIMIT ?
            """,
            params,
        )
    )


def get_taxonomy_suggestion(connection: sqlite3.Connection, suggestion_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT ts.suggestion_id, ts.type, ts.doc_id, ts.revision_id,
               d.title, d.status AS document_status, d.current_revision_id,
               d.category_id AS current_category_id,
               ts.target_id, ts.payload_json, ts.confidence, ts.source,
               ts.status, ts.created_at, ts.updated_at,
               c.name AS suggested_category_name
        FROM taxonomy_suggestions ts
        LEFT JOIN documents d ON d.doc_id = ts.doc_id
        LEFT JOIN categories c ON c.category_id = json_extract(ts.payload_json, '$.category_id')
        WHERE ts.suggestion_id = ?
        """,
        (suggestion_id,),
    ).fetchone()


def insert_category_assign_suggestion(
    connection: sqlite3.Connection,
    *,
    doc_id: str,
    revision_id: str,
    category_id: str,
    confidence: float,
    reason: str,
    alternative_category_ids: list[str] | None = None,
    source: str = "deterministic",
) -> str:
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

    existing = connection.execute(
        """
        SELECT suggestion_id
        FROM taxonomy_suggestions
        WHERE doc_id = ?
          AND revision_id = ?
          AND type = 'category_assign'
          AND status = 'pending'
          AND json_extract(payload_json, '$.category_id') = ?
        """,
        (doc_id, revision_id, category_id),
    ).fetchone()
    payload = {
        "category_id": category_id,
        "reason": reason,
        "alternative_category_ids": alternative_category_ids or [],
    }
    now = utc_now_iso()
    if existing is not None:
        connection.execute(
            """
            UPDATE taxonomy_suggestions
            SET payload_json = ?, confidence = ?, source = ?, updated_at = ?
            WHERE suggestion_id = ?
            """,
            (json.dumps(payload, ensure_ascii=False), confidence, source, now, existing["suggestion_id"]),
        )
        return str(existing["suggestion_id"])

    suggestion_id = new_prefixed_id("taxsugg")
    connection.execute(
        """
        INSERT INTO taxonomy_suggestions(
          suggestion_id, type, doc_id, revision_id, target_id, payload_json,
          confidence, status, source, created_at, updated_at
        )
        VALUES (?, 'category_assign', ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
        """,
        (
            suggestion_id,
            doc_id,
            revision_id,
            category_id,
            json.dumps(payload, ensure_ascii=False),
            confidence,
            source,
            now,
            now,
        ),
    )
    create_review_item(
        connection,
        review_type="taxonomy_suggestion",
        target_type="taxonomy_suggestion",
        target_id=suggestion_id,
        reason=f"Confirm category suggestion for {doc_id}: {reason}",
        priority=60,
    )
    return suggestion_id


def accept_taxonomy_suggestion(
    connection: sqlite3.Connection,
    suggestion_id: str,
    *,
    force_category: bool = False,
    reason: str | None = None,
) -> TaxonomySuggestionApplyResult:
    row = _require_pending_taxonomy_suggestion(connection, suggestion_id)
    suggestion_type = str(row["type"])
    if suggestion_type == "category_assign":
        return _accept_category_assign(connection, row, force_category=force_category, reason=reason)
    if suggestion_type == "tag_assign":
        return _accept_tag_assign(connection, row, reason=reason)
    raise ValueError(f"Suggestion type cannot be accepted via this command: {suggestion_type}")


def reject_taxonomy_suggestion(
    connection: sqlite3.Connection,
    suggestion_id: str,
    *,
    reason: str | None = None,
) -> TaxonomySuggestionApplyResult:
    row = _require_pending_taxonomy_suggestion(connection, suggestion_id)
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE taxonomy_suggestions
        SET status = 'rejected', updated_at = ?
        WHERE suggestion_id = ?
        """,
        (now, suggestion_id),
    )
    _resolve_taxonomy_review(connection, suggestion_id, reason or "rejected")
    connection.commit()
    return TaxonomySuggestionApplyResult(
        suggestion_id=suggestion_id,
        doc_id=row["doc_id"],
        suggestion_type=str(row["type"]),
        status="rejected",
    )


def _accept_category_assign(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    force_category: bool,
    reason: str | None,
) -> TaxonomySuggestionApplyResult:
    doc_id = str(row["doc_id"])
    _require_current_active_taxonomy_suggestion(row)
    payload = _parse_payload(row["payload_json"])
    category_id = str(payload.get("category_id") or row["target_id"] or "")
    if not category_id:
        raise ValueError("Category suggestion is missing category_id.")

    old_category = str(row["current_category_id"] or "")
    can_replace = old_category in {"", "cat_uncategorized"} or force_category
    if not can_replace and old_category != category_id:
        raise ValueError(
            "Manual category is protected. Re-run with --force-category to replace a non-uncategorized category."
        )

    category_changed = False
    if can_replace and old_category != category_id:
        result = set_document_category(connection, doc_id, category_id)
        category_changed = result.changed
        now = utc_now_iso()
        connection.execute(
            """
            UPDATE documents
            SET category_source = 'accepted_suggestion',
                category_suggestion_id = ?,
                category_updated_by = 'taxonomy',
                updated_at = ?
            WHERE doc_id = ?
              AND deleted_at IS NULL
            """,
            (str(row["suggestion_id"]), now, doc_id),
        )

    now = utc_now_iso()
    connection.execute(
        """
        UPDATE taxonomy_suggestions
        SET status = 'accepted', updated_at = ?
        WHERE suggestion_id = ?
        """,
        (now, row["suggestion_id"]),
    )
    _resolve_taxonomy_review(connection, str(row["suggestion_id"]), reason or "accepted")
    connection.commit()
    return TaxonomySuggestionApplyResult(
        suggestion_id=str(row["suggestion_id"]),
        doc_id=doc_id,
        suggestion_type="category_assign",
        status="accepted",
        category_changed=category_changed,
    )


def _accept_tag_assign(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    reason: str | None,
) -> TaxonomySuggestionApplyResult:
    doc_id = str(row["doc_id"])
    revision_id = str(row["revision_id"])
    _require_current_active_taxonomy_suggestion(row)
    payload = _parse_payload(row["payload_json"])
    tag_id = str(payload.get("tag_id") or row["target_id"] or "")
    tag_name = str(payload.get("tag_name") or "")
    if not tag_id and not tag_name:
        raise ValueError("Tag assignment suggestion is missing tag reference.")
    if not tag_name:
        tag_row = connection.execute("SELECT name FROM tags WHERE tag_id = ?", (tag_id,)).fetchone()
        if tag_row is None:
            raise ValueError(f"Suggested tag not found: {tag_id}")
        tag_name = str(tag_row["name"])

    evidence = payload.get("evidence_chunk_ids")
    chunk_ids = evidence if isinstance(evidence, list) else []
    if payload.get("chunk_id") and str(payload["chunk_id"]) not in chunk_ids:
        chunk_ids = [str(payload["chunk_id"]), *chunk_ids]

    change = add_document_tag(
        connection,
        doc_id,
        tag_name,
        source="accepted_suggestion",
        confidence=float(row["confidence"] or payload.get("confidence") or 1.0),
        created_by="taxonomy",
        revision_id=revision_id,
        suggestion_id=str(row["suggestion_id"]),
        evidence_chunk_ids=[str(item) for item in chunk_ids],
    )
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE taxonomy_suggestions
        SET status = 'accepted', updated_at = ?
        WHERE suggestion_id = ?
        """,
        (now, row["suggestion_id"]),
    )
    _resolve_taxonomy_review(connection, str(row["suggestion_id"]), reason or "accepted")
    connection.commit()
    return TaxonomySuggestionApplyResult(
        suggestion_id=str(row["suggestion_id"]),
        doc_id=doc_id,
        suggestion_type="tag_assign",
        status="accepted",
        tags_added=(change.tag_name,) if change.changed else (),
        tag_id=change.tag_id,
    )


def _require_pending_taxonomy_suggestion(connection: sqlite3.Connection, suggestion_id: str) -> sqlite3.Row:
    mark_stale_taxonomy_suggestions(connection)
    row = get_taxonomy_suggestion(connection, suggestion_id)
    if row is None:
        raise ValueError(f"Taxonomy suggestion not found: {suggestion_id}")
    if row["status"] != "pending":
        raise ValueError(f"Taxonomy suggestion is not pending: {suggestion_id}")
    return row


def _require_current_active_taxonomy_suggestion(row: sqlite3.Row) -> None:
    if row["doc_id"] is None:
        raise ValueError("Document-bound suggestion required.")
    if row["document_status"] != "active":
        raise ValueError(f"Document is not active: {row['doc_id']}")
    if row["current_revision_id"] != row["revision_id"]:
        raise ValueError(f"Taxonomy suggestion is stale for document: {row['doc_id']}")


def _resolve_taxonomy_review(connection: sqlite3.Connection, suggestion_id: str, note: str) -> None:
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE review_items
        SET status = 'resolved',
            resolved_at = COALESCE(resolved_at, ?),
            resolution_note = COALESCE(?, resolution_note),
            resolved_by = COALESCE(resolved_by, 'taxonomy'),
            updated_at = ?
        WHERE type = 'taxonomy_suggestion'
          AND target_type = 'taxonomy_suggestion'
          AND target_id = ?
          AND status = 'pending'
        """,
        (now, note, now, suggestion_id),
    )


def _parse_payload(payload_json: object) -> dict[str, object]:
    if payload_json is None:
        return {}
    parsed = json.loads(str(payload_json))
    if not isinstance(parsed, dict):
        return {}
    return parsed
