"""Review queue helpers."""

from __future__ import annotations

import sqlite3

from indbase_core.ids import new_prefixed_id
from indbase_core.time import utc_now_iso


REVIEW_COLUMNS = """
review_id, type, target_type, target_id, priority, reason,
status, created_at, updated_at, resolved_at, resolution_note, resolved_by
"""


def create_review_item(
    connection: sqlite3.Connection,
    *,
    review_type: str,
    target_type: str,
    target_id: str,
    reason: str,
    priority: int = 50,
) -> str:
    review_id = new_prefixed_id("review")
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO review_items(
          review_id, type, target_type, target_id, priority, reason, status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
        """,
        (review_id, review_type, target_type, target_id, priority, reason, now, now),
    )
    return review_id


def list_review_items(
    connection: sqlite3.Connection,
    *,
    status: str | None = "pending",
    review_type: str | None = None,
    target_type: str | None = None,
    limit: int = 20,
) -> list[sqlite3.Row]:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    clauses: list[str] = []
    values: list[object] = []
    if status is not None:
        clauses.append("status = ?")
        values.append(status)
    if review_type:
        clauses.append("type = ?")
        values.append(review_type)
    if target_type:
        clauses.append("target_type = ?")
        values.append(target_type)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    values.append(limit)
    return list(
        connection.execute(
            f"""
            SELECT {REVIEW_COLUMNS}
            FROM review_items
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            values,
        )
    )


def get_review_item(connection: sqlite3.Connection, review_id: str) -> sqlite3.Row | None:
    return connection.execute(
        f"""
        SELECT {REVIEW_COLUMNS}
        FROM review_items
        WHERE review_id = ?
        """,
        (review_id,),
    ).fetchone()


def resolve_review_item(
    connection: sqlite3.Connection,
    review_id: str,
    *,
    note: str | None = None,
    resolved_by: str | None = None,
) -> sqlite3.Row:
    row = get_review_item(connection, review_id)
    if row is None:
        raise ValueError(f"Review item not found: {review_id}")
    if row["status"] == "resolved":
        return row

    now = utc_now_iso()
    connection.execute(
        """
        UPDATE review_items
        SET status = 'resolved',
            resolved_at = COALESCE(resolved_at, ?),
            resolution_note = COALESCE(?, resolution_note),
            resolved_by = COALESCE(?, resolved_by),
            updated_at = ?
        WHERE review_id = ?
        """,
        (now, note, resolved_by, now, review_id),
    )
    connection.commit()
    resolved = get_review_item(connection, review_id)
    if resolved is None:
        raise ValueError(f"Review item not found after resolve: {review_id}")
    return resolved


def resolve_review_items(
    connection: sqlite3.Connection,
    review_ids: list[str],
    *,
    note: str | None = None,
    resolved_by: str | None = None,
) -> list[sqlite3.Row]:
    if not review_ids:
        raise ValueError("At least one review_id is required.")
    resolved: list[sqlite3.Row] = []
    for review_id in review_ids:
        resolved.append(resolve_review_item(connection, review_id, note=note, resolved_by=resolved_by))
    return resolved
