"""Accept/reject v0.3.2 tag candidates with feedback and document-scoped apply."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3

from indbase_core.tag_admission import evaluate_tag_admission
from indbase_core.tag_feedback import record_tag_feedback
from indbase_core.tag_governance import record_tag_governance_event
from indbase_core.tag_governance_eval import validate_candidate_type
from indbase_core.tag_resolution import resolve_tag_candidate
from indbase_core.tags import add_document_tag, add_tag, attach_document_tag_by_id
from indbase_core.taxonomy import validate_tag_type
from indbase_core.time import utc_now_iso


@dataclass(frozen=True)
class TagGovernanceReviewResult:
    candidate_id: str
    status: str
    candidate_type: str | None
    doc_id: str | None
    tag_id: str | None
    tag_name: str | None
    document_tag_changed: bool
    feedback_id: str
    governance_event_id: str | None


def get_tag_governance_candidate(
    connection: sqlite3.Connection,
    candidate_id: str,
) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT *
        FROM tag_candidates
        WHERE candidate_id = ?
        """,
        (candidate_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Tag candidate not found: {candidate_id}")
    return row


def accept_tag_governance_candidate(
    connection: sqlite3.Connection,
    candidate_id: str,
    *,
    tag_type: str | None = None,
    created_by: str = "manual",
) -> TagGovernanceReviewResult:
    row = get_tag_governance_candidate(connection, candidate_id)
    if str(row["status"]) != "pending":
        raise ValueError(f"Tag candidate is not pending: {candidate_id}")

    candidate_type = str(row["candidate_type"] or "").strip() or None
    doc_id = str(row["doc_id"]) if row["doc_id"] is not None else None
    revision_id = str(row["revision_id"]) if row["revision_id"] is not None else None

    if candidate_type is None and doc_id is None:
        return _accept_legacy_promote_only(
            connection,
            row,
            candidate_id=candidate_id,
            tag_type=tag_type,
            created_by=created_by,
        )

    if doc_id is None or revision_id is None:
        raise ValueError(
            f"Governance candidate {candidate_id} requires doc_id and revision_id for acceptance."
        )

    evidence_chunk_ids = _parse_json_list(row["evidence_chunk_ids_json"])
    confidence = float(row["confidence"] or 0.5)
    document_tag_changed = False
    tag_id: str | None = None
    tag_name = str(row["name"])
    governance_event_id: str | None = None

    if candidate_type in {None, "", "propose_new"}:
        clean_candidate_type = validate_candidate_type("propose_new")
        tag_type_clean = validate_tag_type(tag_type or str(row["type"]))
        proposed = str(row["proposed_name"] or row["raw_name"] or row["name"])
        admission = evaluate_tag_admission(
            connection,
            proposed,
            tag_type=tag_type_clean,
        )
        if admission.outcome != "approved":
            raise ValueError(
                f"New-tag proposal failed admission: {', '.join(admission.reasons)}"
            )
        tag_id = add_tag(
            connection,
            proposed,
            tag_type=tag_type_clean,
            created_by=created_by,
        )
        tag_name = proposed
        attach = attach_document_tag_by_id(
            connection,
            doc_id,
            tag_id,
            source="accepted_candidate",
            confidence=confidence,
            created_by=created_by,
            revision_id=revision_id,
            evidence_chunk_ids=evidence_chunk_ids,
            candidate_id=candidate_id,
        )
        document_tag_changed = attach.changed
        candidate_type = clean_candidate_type
    elif candidate_type == "attach_existing":
        clean_candidate_type = validate_candidate_type("attach_existing")
        target_tag_id = str(row["target_tag_id"] or "")
        if not target_tag_id:
            raise ValueError(f"Attach-existing candidate missing target_tag_id: {candidate_id}")
        target = connection.execute(
            """
            SELECT tag_id, name, status
            FROM tags
            WHERE tag_id = ?
              AND deleted_at IS NULL
            """,
            (target_tag_id,),
        ).fetchone()
        if target is None:
            raise ValueError(f"Target tag not found: {target_tag_id}")
        if str(target["status"]) != "active":
            raise ValueError(f"Target tag is not active: {target_tag_id}")
        resolution = resolve_tag_candidate(connection, str(row["raw_name"] or row["name"]))
        if resolution.outcome not in {"canonical"} or resolution.canonical_tag_id != target_tag_id:
            raise ValueError(
                f"Target tag {target_tag_id} is not the resolved canonical tag for this candidate."
            )
        tag_id = target_tag_id
        tag_name = str(target["name"])
        attach = attach_document_tag_by_id(
            connection,
            doc_id,
            tag_id,
            source="accepted_candidate",
            confidence=confidence,
            created_by=created_by,
            revision_id=revision_id,
            evidence_chunk_ids=evidence_chunk_ids,
            candidate_id=candidate_id,
        )
        document_tag_changed = attach.changed
        candidate_type = clean_candidate_type
    else:
        raise ValueError(f"Unsupported candidate type for acceptance: {candidate_type}")

    now = utc_now_iso()
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

    feedback_id = record_tag_feedback(
        connection,
        "accepted",
        doc_id=doc_id,
        revision_id=revision_id,
        tag_id=tag_id,
        candidate_id=candidate_id,
        new={
            "candidate_type": candidate_type,
            "document_tag_changed": document_tag_changed,
        },
        created_by=created_by,
    )
    if candidate_type == "propose_new":
        governance_event_id = record_tag_governance_event(
            connection,
            "promoted",
            tag_id=tag_id,
            candidate_id=candidate_id,
            doc_id=doc_id,
            payload={
                "candidate_type": candidate_type,
                "document_tag_changed": document_tag_changed,
                "feedback_id": feedback_id,
            },
            created_by=created_by,
        )
    connection.commit()

    return TagGovernanceReviewResult(
        candidate_id=candidate_id,
        status="accepted",
        candidate_type=candidate_type,
        doc_id=doc_id,
        tag_id=tag_id,
        tag_name=tag_name,
        document_tag_changed=document_tag_changed,
        feedback_id=feedback_id,
        governance_event_id=governance_event_id,
    )


def reject_tag_governance_candidate(
    connection: sqlite3.Connection,
    candidate_id: str,
    *,
    reason: str | None = None,
    created_by: str = "manual",
) -> TagGovernanceReviewResult:
    row = get_tag_governance_candidate(connection, candidate_id)
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
    doc_id = str(row["doc_id"]) if row["doc_id"] is not None else None
    revision_id = str(row["revision_id"]) if row["revision_id"] is not None else None
    feedback_id = record_tag_feedback(
        connection,
        "rejected",
        doc_id=doc_id,
        revision_id=revision_id,
        candidate_id=candidate_id,
        reason=reason,
        old={"status": "pending"},
        new={"status": "rejected"},
        created_by=created_by,
    )
    connection.commit()

    return TagGovernanceReviewResult(
        candidate_id=candidate_id,
        status="rejected",
        candidate_type=str(row["candidate_type"]) if row["candidate_type"] else None,
        doc_id=doc_id,
        tag_id=None,
        tag_name=str(row["name"]),
        document_tag_changed=False,
        feedback_id=feedback_id,
        governance_event_id=None,
    )


def _accept_legacy_promote_only(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    candidate_id: str,
    tag_type: str | None,
    created_by: str,
) -> TagGovernanceReviewResult:
    from indbase_core.tag_candidates import promote_tag_candidate

    promoted = promote_tag_candidate(
        connection,
        candidate_id,
        tag_type=tag_type,
        promoted_by=created_by,
    )
    feedback_row = connection.execute(
        """
        SELECT tag_feedback_id
        FROM tag_feedback
        WHERE candidate_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (candidate_id,),
    ).fetchone()
    event_row = connection.execute(
        """
        SELECT event_id
        FROM tag_governance_events
        WHERE candidate_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (candidate_id,),
    ).fetchone()
    return TagGovernanceReviewResult(
        candidate_id=candidate_id,
        status="accepted",
        candidate_type=None,
        doc_id=None,
        tag_id=promoted.tag_id,
        tag_name=promoted.tag_name,
        document_tag_changed=False,
        feedback_id=str(feedback_row["tag_feedback_id"]) if feedback_row else "",
        governance_event_id=str(event_row["event_id"]) if event_row else None,
    )


def _parse_json_list(raw: object) -> list[str]:
    if raw is None:
        return []
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if item]
