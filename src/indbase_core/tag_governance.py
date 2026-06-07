"""v0.3.2 tag governance constants and audit helpers."""

from __future__ import annotations

import json
import sqlite3

from indbase_core.ids import new_prefixed_id
from indbase_core.time import utc_now_iso

POLICY_VERSION = "v032-policy-v1"
TAGGER_VERSION = "v032-deterministic-v1"
HARNESS_VERSION = "v032-harness-v1"

AUTO_ATTACH_THRESHOLD = 0.85
DEFAULT_PER_DOC_AUTO_ATTACH_LIMIT = 5
DEFAULT_PER_DOC_CANDIDATE_LIMIT = 5
DEFAULT_PER_RUN_NEW_TAG_PROPOSAL_LIMIT = 20
DEFAULT_PER_RUN_TOTAL_CANDIDATE_LIMIT = 200
DEFAULT_MIN_DOCS_FOR_NEW_TAG = 2
DEFAULT_FORMAL_TAGS_SOFT_LIMIT = 500

TAG_SCOPES: frozenset[str] = frozenset({"global", "category_bound"})

RESOLUTION_OUTCOMES: frozenset[str] = frozenset(
    {
        "canonical",
        "blocked",
        "deprecated",
        "merged",
        "archived",
        "propose_new",
    }
)

CANDIDATE_TYPES: frozenset[str] = frozenset(
    {
        "attach_existing",
        "propose_new",
        "alias_suggestion",
        "merge_suggestion",
        "remove_suggestion",
    }
)

ADMISSION_OUTCOMES: frozenset[str] = frozenset({"approved", "rejected"})

BUDGET_OUTCOMES: frozenset[str] = frozenset({"allowed", "denied"})

BLOCKLIST_MATCH_TYPES: frozenset[str] = frozenset({"exact", "contains"})

GOVERNANCE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "created",
        "promoted",
        "alias_added",
        "alias_removed",
        "merged",
        "deprecated",
        "archived",
        "restored",
        "scope_changed",
        "blocked",
        "unblocked",
        "policy_suggestion_created",
        "link_migration_started",
        "link_migration_finished",
    }
)


def record_tag_governance_event(
    connection: sqlite3.Connection,
    event_type: str,
    *,
    tag_id: str | None = None,
    candidate_id: str | None = None,
    doc_id: str | None = None,
    payload: dict[str, object] | None = None,
    created_by: str = "tag_governance",
) -> str:
    if event_type not in GOVERNANCE_EVENT_TYPES:
        raise ValueError(f"Invalid governance event type: {event_type}")
    event_id = new_prefixed_id("tagevt")
    connection.execute(
        """
        INSERT INTO tag_governance_events(
          event_id, tag_id, candidate_id, doc_id, event_type,
          payload_json, created_by, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            tag_id,
            candidate_id,
            doc_id,
            event_type,
            json.dumps(payload or {}, ensure_ascii=False),
            created_by,
            utc_now_iso(),
        ),
    )
    return event_id
