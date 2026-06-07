"""Tag feedback audit records for v0.3.2 governance."""

from __future__ import annotations

import json
import sqlite3

from indbase_core.ids import new_prefixed_id
from indbase_core.time import utc_now_iso

TAG_FEEDBACK_ACTIONS: frozenset[str] = frozenset(
    {
        "accepted",
        "rejected",
        "manual_created",
        "manual_removed",
        "merged",
        "deprecated",
        "alias_added",
        "alias_removed",
        "blocked",
        "unblocked",
        "policy_suggestion_created",
        "fixture_candidate_created",
    }
)


def record_tag_feedback(
    connection: sqlite3.Connection,
    action: str,
    *,
    doc_id: str | None = None,
    revision_id: str | None = None,
    tag_id: str | None = None,
    candidate_id: str | None = None,
    reason: str | None = None,
    old: dict[str, object] | None = None,
    new: dict[str, object] | None = None,
    created_by: str = "tag_governance",
) -> str:
    if action not in TAG_FEEDBACK_ACTIONS:
        raise ValueError(f"Invalid tag feedback action: {action}")
    feedback_id = new_prefixed_id("tagfb")
    connection.execute(
        """
        INSERT INTO tag_feedback(
          tag_feedback_id, doc_id, revision_id, tag_id, candidate_id,
          action, reason, old_json, new_json, created_by, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            feedback_id,
            doc_id,
            revision_id,
            tag_id,
            candidate_id,
            action,
            reason,
            json.dumps(old, ensure_ascii=False) if old is not None else None,
            json.dumps(new, ensure_ascii=False) if new is not None else None,
            created_by,
            utc_now_iso(),
        ),
    )
    return feedback_id
