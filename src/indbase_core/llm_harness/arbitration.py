"""Apply validated harness decisions to taxonomy suggestion tables only."""

from __future__ import annotations

import json
import sqlite3

from indbase_core.ids import new_prefixed_id
from indbase_core.reviews import create_review_item
from indbase_core.taxonomy import validate_tag_type
from indbase_core.taxonomy_suggestions import insert_category_assign_suggestion
from indbase_core.time import utc_now_iso


def apply_taxonomy_decision(
    connection: sqlite3.Connection,
    decision: dict[str, object],
    *,
    doc_id: str,
    revision_id: str,
) -> list[str]:
    """Persist harness output as reviewable taxonomy artifacts; never mutate formal taxonomy."""
    choice = str(decision["decision"])
    if choice == "category_assign":
        suggestion_id = insert_category_assign_suggestion(
            connection,
            doc_id=doc_id,
            revision_id=revision_id,
            category_id=str(decision["category_id"]),
            confidence=float(decision["confidence"]),
            reason=str(decision["reason"]),
            source="llm",
        )
        return [suggestion_id]
    if choice == "no_category":
        return []
    if choice == "ambiguous":
        return [_insert_review_only_suggestion(connection, doc_id, revision_id, decision)]
    if choice == "alias_to_existing":
        return [_insert_tag_assign_suggestion(connection, doc_id, revision_id, decision)]
    if choice == "new_tag_candidate":
        return [_insert_tag_candidate_from_llm(connection, doc_id, revision_id, decision)]
    if choice == "local_keyword":
        return []
    raise ValueError(f"Unsupported harness decision: {choice}")


def _insert_tag_assign_suggestion(
    connection: sqlite3.Connection,
    doc_id: str,
    revision_id: str,
    decision: dict[str, object],
) -> str:
    tag_id = str(decision["tag_id"])
    payload = {
        "tag_id": tag_id,
        "feature_text": decision.get("feature_text"),
        "quote": decision.get("quote"),
        "evidence_chunk_ids": decision.get("evidence_chunk_ids") or [],
        "needs_review": True,
    }
    suggestion_id = new_prefixed_id("taxsugg")
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO taxonomy_suggestions(
          suggestion_id, type, doc_id, revision_id, target_id, payload_json,
          confidence, status, source, created_at, updated_at
        )
        VALUES (?, 'tag_assign', ?, ?, ?, ?, ?, 'pending', 'llm', ?, ?)
        """,
        (
            suggestion_id,
            doc_id,
            revision_id,
            tag_id,
            json.dumps(payload, ensure_ascii=False),
            float(decision["confidence"]),
            now,
            now,
        ),
    )
    create_review_item(
        connection,
        review_type="taxonomy_suggestion",
        target_type="taxonomy_suggestion",
        target_id=suggestion_id,
        reason=f"LLM tag assignment suggestion for {doc_id}",
        priority=55,
    )
    return suggestion_id


def _insert_tag_candidate_from_llm(
    connection: sqlite3.Connection,
    doc_id: str,
    revision_id: str,
    decision: dict[str, object],
) -> str:
    name = str(decision["candidate_name"]).strip()
    normalized = " ".join(name.casefold().split())
    candidate_type = validate_tag_type(str(decision.get("candidate_type") or "topic"))
    evidence_chunks = [str(item) for item in (decision.get("evidence_chunk_ids") or [])]
    now = utc_now_iso()
    candidate_id = new_prefixed_id("tagcand")
    connection.execute(
        """
        INSERT INTO tag_candidates(
          candidate_id, name, normalized_name, type,
          evidence_doc_ids_json, evidence_chunk_ids_json,
          occurrence_count, distinct_doc_count, confidence,
          status, created_by, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?, 'pending', 'llm_harness', ?, ?)
        """,
        (
            candidate_id,
            name,
            normalized,
            candidate_type,
            json.dumps([doc_id], ensure_ascii=False),
            json.dumps(evidence_chunks, ensure_ascii=False),
            float(decision["confidence"]),
            now,
            now,
        ),
    )
    suggestion_id = new_prefixed_id("taxsugg")
    connection.execute(
        """
        INSERT INTO taxonomy_suggestions(
          suggestion_id, type, doc_id, revision_id, target_id, payload_json,
          confidence, status, source, created_at, updated_at
        )
        VALUES (?, 'tag_candidate', ?, ?, ?, ?, ?, 'pending', 'llm', ?, ?)
        """,
        (
            suggestion_id,
            doc_id,
            revision_id,
            candidate_id,
            json.dumps({"candidate_id": candidate_id, "name": name}, ensure_ascii=False),
            float(decision["confidence"]),
            now,
            now,
        ),
    )
    create_review_item(
        connection,
        review_type="taxonomy_suggestion",
        target_type="taxonomy_suggestion",
        target_id=suggestion_id,
        reason=f"LLM tag candidate for {doc_id}",
        priority=50,
    )
    return suggestion_id


def _insert_review_only_suggestion(
    connection: sqlite3.Connection,
    doc_id: str,
    revision_id: str,
    decision: dict[str, object],
) -> str:
    suggestion_id = new_prefixed_id("taxsugg")
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO taxonomy_suggestions(
          suggestion_id, type, doc_id, revision_id, target_id, payload_json,
          confidence, status, source, created_at, updated_at
        )
        VALUES (?, 'tag_assign', ?, ?, NULL, ?, ?, 'pending', 'llm', ?, ?)
        """,
        (
            suggestion_id,
            doc_id,
            revision_id,
            json.dumps(decision, ensure_ascii=False),
            float(decision["confidence"]),
            now,
            now,
        ),
    )
    create_review_item(
        connection,
        review_type="taxonomy_suggestion",
        target_type="taxonomy_suggestion",
        target_id=suggestion_id,
        reason=f"Ambiguous LLM taxonomy decision for {doc_id}",
        priority=45,
    )
    return suggestion_id
