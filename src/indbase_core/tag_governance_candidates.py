"""Persist v0.3.2 governance-scoped tag candidates."""

from __future__ import annotations

import json
import sqlite3

from indbase_core.ids import new_prefixed_id
from indbase_core.tag_governance_eval import validate_candidate_type
from indbase_core.tag_governance_eval import TagGovernanceDecision
from indbase_core.tags import normalize_tag_name
from indbase_core.taxonomy import validate_tag_type
from indbase_core.time import utc_now_iso


def insert_tag_governance_candidate(
    connection: sqlite3.Connection,
    *,
    candidate_type: str,
    raw_name: str,
    tag_type: str,
    doc_id: str,
    revision_id: str,
    tagger_run_id: str,
    tagger_result_id: str,
    decision: TagGovernanceDecision,
    target_tag_id: str | None = None,
    confidence: float = 0.5,
    evidence_chunk_ids: list[str] | None = None,
    created_by: str = "tagger",
) -> str:
    clean_type = validate_candidate_type(candidate_type)
    clean_tag_type = validate_tag_type(tag_type)
    normalized = decision.resolution.normalized_name or normalize_tag_name(raw_name)
    now = utc_now_iso()
    evidence_docs = json.dumps([doc_id], ensure_ascii=False)
    evidence_chunks = json.dumps(evidence_chunk_ids or [], ensure_ascii=False)
    policy_json = (
        json.dumps(
            {
                "outcome": decision.admission.outcome if decision.admission else None,
                "reasons": list(decision.admission.reasons) if decision.admission else [],
                "warnings": list(decision.admission.warnings) if decision.admission else [],
            },
            ensure_ascii=False,
        )
        if decision.admission is not None
        else None
    )
    budget_json = (
        json.dumps(
            {
                "outcome": decision.budget.outcome,
                "reason": decision.budget.reason,
                "limits": decision.budget.limits,
            },
            ensure_ascii=False,
        )
        if decision.budget is not None
        else None
    )
    candidate_id = new_prefixed_id("tagcand")
    connection.execute(
        """
        INSERT INTO tag_candidates(
          candidate_id, name, normalized_name, type,
          evidence_doc_ids_json, evidence_chunk_ids_json,
          occurrence_count, distinct_doc_count, confidence,
          status, created_by, created_at, updated_at,
          candidate_type, raw_name, target_tag_id, proposed_name,
          doc_id, revision_id, tagger_run_id, tagger_result_id,
          resolution_status, admission_status, budget_status,
          policy_decision_json, budget_decision_json
        )
        VALUES (
          ?, ?, ?, ?, ?, ?, 1, 1, ?, 'pending', ?, ?, ?,
          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            candidate_id,
            raw_name,
            normalized,
            clean_tag_type,
            evidence_docs,
            evidence_chunks,
            confidence,
            created_by,
            now,
            now,
            clean_type,
            raw_name,
            target_tag_id,
            raw_name if clean_type == "propose_new" else None,
            doc_id,
            revision_id,
            tagger_run_id,
            tagger_result_id,
            decision.resolution.outcome,
            decision.admission.outcome if decision.admission else None,
            decision.budget.outcome if decision.budget else None,
            policy_json,
            budget_json,
        ),
    )
    return candidate_id
