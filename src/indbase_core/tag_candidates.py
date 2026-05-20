"""Tag candidate promotion and rejection."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3

from indbase_core.ids import new_prefixed_id
from indbase_core.tags import add_tag, normalize_tag_name
from indbase_core.taxonomy import validate_tag_type
from indbase_core.time import utc_now_iso


@dataclass(frozen=True)
class TagCandidateChange:
    candidate_id: str
    status: str
    tag_id: str | None = None
    tag_name: str | None = None


def list_tag_candidates(
    connection: sqlite3.Connection,
    *,
    status: str | None = "pending",
    limit: int = 50,
) -> list[sqlite3.Row]:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    clauses: list[str] = []
    params: list[object] = []
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(limit)
    return list(
        connection.execute(
            f"""
            SELECT candidate_id, name, normalized_name, type, confidence,
                   occurrence_count, distinct_doc_count, status, promoted_tag_id,
                   created_at, updated_at
            FROM tag_candidates
            {where}
            ORDER BY created_at DESC, candidate_id
            LIMIT ?
            """,
            params,
        )
    )


def list_pending_tag_names(connection: sqlite3.Connection, *, limit: int = 50) -> list[sqlite3.Row]:
    return list_tag_candidates(connection, status="pending", limit=limit)


def promote_tag_candidate(
    connection: sqlite3.Connection,
    candidate_id: str,
    *,
    tag_type: str | None = None,
    promoted_by: str = "manual",
) -> TagCandidateChange:
    row = _load_candidate(connection, candidate_id)
    if str(row["status"]) != "pending":
        raise ValueError(f"Tag candidate is not pending: {candidate_id}")

    clean_type = validate_tag_type(tag_type or str(row["type"]))
    type_override = clean_type != str(row["type"])
    tag_id = add_tag(
        connection,
        str(row["name"]),
        tag_type=clean_type,
        created_by=promoted_by,
    )
    now = utc_now_iso()
    payload = {
        "promoted_tag_id": tag_id,
        "type_override": type_override,
        "original_type": str(row["type"]),
        "promoted_type": clean_type,
    }
    connection.execute(
        """
        UPDATE tag_candidates
        SET status = 'accepted',
            promoted_tag_id = ?,
            updated_at = ?
        WHERE candidate_id = ?
        """,
        (tag_id, now, candidate_id),
    )
    connection.execute(
        """
        INSERT INTO tag_lifecycle_events(
          event_id, tag_id, event_type, payload_json, created_by, created_at
        )
        VALUES (?, ?, 'promoted_from_candidate', ?, ?, ?)
        """,
        (new_prefixed_id("tagevent"), tag_id, json.dumps(payload, ensure_ascii=False), promoted_by, now),
    )
    connection.commit()
    return TagCandidateChange(
        candidate_id=candidate_id,
        status="accepted",
        tag_id=tag_id,
        tag_name=str(row["name"]),
    )


def reject_tag_candidate(
    connection: sqlite3.Connection,
    candidate_id: str,
    *,
    reason: str | None = None,
) -> TagCandidateChange:
    row = _load_candidate(connection, candidate_id)
    if str(row["status"]) != "pending":
        raise ValueError(f"Tag candidate is not pending: {candidate_id}")
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE tag_candidates
        SET status = 'rejected', updated_at = ?
        WHERE candidate_id = ?
        """,
        (now, candidate_id),
    )
    connection.commit()
    return TagCandidateChange(candidate_id=candidate_id, status="rejected", tag_name=str(row["name"]))


def record_missing_tag_candidate(
    connection: sqlite3.Connection,
    *,
    doc_id: str,
    revision_id: str,
    tag_name: str,
    tag_type: str = "topic",
    chunk_ids: list[str] | None = None,
) -> str | None:
    """Route a missing legacy tag name to tag_candidates instead of formal tags."""
    clean_name = " ".join(tag_name.strip().split())
    if not clean_name:
        return None
    normalized = normalize_tag_name(clean_name)
    clean_type = validate_tag_type(tag_type)
    existing = connection.execute(
        """
        SELECT candidate_id, status
        FROM tag_candidates
        WHERE normalized_name = ?
          AND type = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (normalized, clean_type),
    ).fetchone()
    now = utc_now_iso()
    evidence_docs = json.dumps([doc_id], ensure_ascii=False)
    evidence_chunks = json.dumps(chunk_ids or [], ensure_ascii=False)
    if existing is None:
        candidate_id = new_prefixed_id("tagcand")
        connection.execute(
            """
            INSERT INTO tag_candidates(
              candidate_id, name, normalized_name, type,
              evidence_doc_ids_json, evidence_chunk_ids_json,
              occurrence_count, distinct_doc_count, confidence,
              status, created_by, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 1, 1, 0.5, 'pending', 'legacy_classification', ?, ?)
            """,
            (
                candidate_id,
                clean_name,
                normalized,
                clean_type,
                evidence_docs,
                evidence_chunks,
                now,
                now,
            ),
        )
        return candidate_id
    if str(existing["status"]) == "pending":
        connection.execute(
            """
            UPDATE tag_candidates
            SET occurrence_count = COALESCE(occurrence_count, 0) + 1,
                evidence_doc_ids_json = ?,
                evidence_chunk_ids_json = ?,
                updated_at = ?
            WHERE candidate_id = ?
            """,
            (evidence_docs, evidence_chunks, now, existing["candidate_id"]),
        )
        return str(existing["candidate_id"])
    return None


def _load_candidate(connection: sqlite3.Connection, candidate_id: str) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT candidate_id, name, normalized_name, type, status, promoted_tag_id
        FROM tag_candidates
        WHERE candidate_id = ?
        """,
        (candidate_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Tag candidate not found: {candidate_id}")
    return row
