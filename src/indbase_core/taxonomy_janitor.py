"""Deterministic taxonomy janitor audits (suggestions only, no mutations)."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from indbase_core.ids import new_prefixed_id
from indbase_core.reviews import create_review_item
from indbase_core.tags import normalize_tag_name
from indbase_core.taxonomy_suggestions import mark_stale_taxonomy_suggestions
from indbase_core.time import utc_now_iso


@dataclass(frozen=True)
class JanitorFinding:
    code: str
    severity: str
    message: str
    suggestion_id: str | None = None


@dataclass(frozen=True)
class TaxonomyAuditReport:
    findings: tuple[JanitorFinding, ...]
    suggestions_created: int


def run_taxonomy_audit(connection: sqlite3.Connection) -> TaxonomyAuditReport:
    mark_stale_taxonomy_suggestions(connection)
    findings: list[JanitorFinding] = []
    suggestions_created = 0

    duplicate_tags = connection.execute(
        """
        SELECT normalized_name, COUNT(*) AS count
        FROM tags
        WHERE deleted_at IS NULL
          AND status = 'active'
        GROUP BY normalized_name
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    for row in duplicate_tags:
        findings.append(
            JanitorFinding(
                code="duplicate_active_tag",
                severity="error",
                message=f"Duplicate active tags for normalized name {row['normalized_name']} ({row['count']}).",
            )
        )
        suggestions_created += _maybe_create_global_suggestion(
            connection,
            suggestion_type="tag_merge",
            payload={
                "normalized_name": row["normalized_name"],
                "reason": "duplicate_active_tags",
            },
            confidence=0.9,
        )

    alias_collisions = connection.execute(
        """
        SELECT normalized_alias, COUNT(*) AS count
        FROM tag_aliases
        WHERE deleted_at IS NULL
        GROUP BY normalized_alias
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    for row in alias_collisions:
        findings.append(
            JanitorFinding(
                code="alias_collision",
                severity="error",
                message=f"Alias collision for {row['normalized_alias']} ({row['count']} rows).",
            )
        )

    candidate_conflicts = connection.execute(
        """
        SELECT tc.candidate_id, tc.name, tc.normalized_name, tc.type
        FROM tag_candidates tc
        JOIN tags t ON t.normalized_name = tc.normalized_name AND t.type = tc.type
        WHERE tc.status = 'pending'
          AND t.deleted_at IS NULL
          AND t.status = 'active'
        """
    ).fetchall()
    for row in candidate_conflicts:
        findings.append(
            JanitorFinding(
                code="candidate_conflicts_active_tag",
                severity="warning",
                message=f"Pending candidate {row['candidate_id']} conflicts with active tag {row['name']}.",
            )
        )

    promoted_without_tag = connection.execute(
        """
        SELECT candidate_id
        FROM tag_candidates
        WHERE status = 'accepted'
          AND (promoted_tag_id IS NULL OR promoted_tag_id = '')
        """
    ).fetchall()
    for row in promoted_without_tag:
        findings.append(
            JanitorFinding(
                code="promoted_candidate_without_tag",
                severity="error",
                message=f"Accepted candidate {row['candidate_id']} has no promoted tag.",
            )
        )

    inactive_assignments = connection.execute(
        """
        SELECT dt.doc_id, dt.tag_id, t.status
        FROM document_tags dt
        JOIN tags t ON t.tag_id = dt.tag_id
        WHERE dt.deleted_at IS NULL
          AND dt.status = 'active'
          AND t.status != 'active'
        LIMIT 50
        """
    ).fetchall()
    for row in inactive_assignments:
        findings.append(
            JanitorFinding(
                code="assigned_inactive_tag",
                severity="error",
                message=f"Document {row['doc_id']} assigned inactive tag {row['tag_id']} (status={row['status']}).",
            )
        )

    stale_suggestions = connection.execute(
        """
        SELECT suggestion_id, doc_id
        FROM taxonomy_suggestions
        WHERE status = 'stale'
        LIMIT 50
        """
    ).fetchall()
    for row in stale_suggestions:
        findings.append(
            JanitorFinding(
                code="stale_document_bound_suggestion",
                severity="warning",
                message=f"Stale taxonomy suggestion {row['suggestion_id']} for doc {row['doc_id']}.",
                suggestion_id=str(row["suggestion_id"]),
            )
        )

    empty_categories = connection.execute(
        """
        SELECT c.category_id, c.name
        FROM categories c
        LEFT JOIN documents d ON d.category_id = c.category_id
          AND d.deleted_at IS NULL
          AND d.status = 'active'
        WHERE c.is_active = 1
          AND c.deleted_at IS NULL
          AND c.category_id != 'cat_uncategorized'
        GROUP BY c.category_id
        HAVING COUNT(d.doc_id) = 0
        """
    ).fetchall()
    for row in empty_categories:
        findings.append(
            JanitorFinding(
                code="empty_active_category",
                severity="warning",
                message=f"Active category {row['category_id']} ({row['name']}) has no active documents.",
            )
        )

    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    old_low_frequency = connection.execute(
        """
        SELECT candidate_id, name, distinct_doc_count, created_at
        FROM tag_candidates
        WHERE status = 'pending'
          AND COALESCE(distinct_doc_count, 0) < 2
          AND created_at < ?
        """,
        (cutoff,),
    ).fetchall()
    for row in old_low_frequency:
        findings.append(
            JanitorFinding(
                code="low_frequency_pending_candidate",
                severity="warning",
                message=(
                    f"Pending candidate {row['candidate_id']} ({row['name']}) has "
                    f"distinct_doc_count={row['distinct_doc_count']} and age > 30 days."
                ),
            )
        )

    connection.commit()
    return TaxonomyAuditReport(findings=tuple(findings), suggestions_created=suggestions_created)


def _maybe_create_global_suggestion(
    connection: sqlite3.Connection,
    *,
    suggestion_type: str,
    payload: dict[str, object],
    confidence: float,
) -> int:
    normalized_key = normalize_tag_name(str(payload.get("normalized_name", suggestion_type)))
    existing = connection.execute(
        """
        SELECT suggestion_id
        FROM taxonomy_suggestions
        WHERE type = ?
          AND status = 'pending'
          AND target_id = ?
        LIMIT 1
        """,
        (suggestion_type, normalized_key),
    ).fetchone()
    if existing is not None:
        return 0
    suggestion_id = new_prefixed_id("taxsugg")
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO taxonomy_suggestions(
          suggestion_id, type, doc_id, revision_id, target_id, payload_json,
          confidence, status, source, created_at, updated_at
        )
        VALUES (?, ?, NULL, NULL, ?, ?, ?, 'pending', 'janitor', ?, ?)
        """,
        (
            suggestion_id,
            suggestion_type,
            normalized_key,
            json.dumps(payload, ensure_ascii=False),
            confidence,
            now,
            now,
        ),
    )
    create_review_item(
        connection,
        review_type="taxonomy_suggestion",
        target_type="taxonomy_suggestion",
        target_id=suggestion_id,
        reason=f"Janitor finding: {payload.get('reason', suggestion_type)}",
        priority=40,
    )
    return 1
