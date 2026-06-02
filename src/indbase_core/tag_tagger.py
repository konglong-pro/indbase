"""Deterministic local tagger for v0.3.2."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3

from indbase_core.feature_extraction import extract_feature_drafts, load_known_tag_phrases
from indbase_core.ids import new_prefixed_id
from indbase_core.tag_governance import (
    AUTO_ATTACH_THRESHOLD,
    HARNESS_VERSION,
    POLICY_VERSION,
    TAGGER_VERSION,
)
from indbase_core.tag_governance_candidates import insert_tag_governance_candidate
from indbase_core.tag_governance_eval import TagGovernanceDecision, evaluate_tag_governance
from indbase_core.tag_resolution import is_auto_attach_eligible, resolve_tag_candidate
from indbase_core.tag_volume_budget import (
    TagBudgetCounters,
    TagVolumeBudget,
    check_auto_attach_budget,
    check_candidate_budget,
    default_budget as volume_default_budget,
    record_auto_attach,
    record_candidate,
)
from indbase_core.tags import add_document_tag, list_document_tags, normalize_tag_name
from indbase_core.time import utc_now_iso

MIN_ATTACH_CANDIDATE_CONFIDENCE = 0.6
VALID_TRIGGERS = frozenset(
    {"manual", "post_ingest", "feedback_eval", "fixture_gate", "propagation_review"}
)


@dataclass(frozen=True)
class TagTaggerRunResult:
    tagger_run_id: str
    scanned_documents: int
    auto_attached_count: int
    candidate_count: int
    new_tag_proposal_count: int
    blocked_candidate_count: int
    preserved_manual_count: int
    error_count: int
    status: str


@dataclass(frozen=True)
class TagTaggerDocumentResult:
    tagger_result_id: str
    doc_id: str
    outcome: str
    auto_attached_tag_ids: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    blocked_count: int


def run_deterministic_tagger(
    connection: sqlite3.Connection,
    *,
    trigger: str = "manual",
    doc_id: str | None = None,
    budget: TagVolumeBudget | None = None,
    auto_attach_threshold: float = AUTO_ATTACH_THRESHOLD,
) -> TagTaggerRunResult:
    """Run the deterministic tagger over active current documents."""
    if trigger not in VALID_TRIGGERS:
        raise ValueError(f"Invalid trigger: {trigger}")
    if not 0.0 <= auto_attach_threshold <= 1.0:
        raise ValueError("auto_attach_threshold must be between 0 and 1")

    active_budget = budget or volume_default_budget()
    counters = TagBudgetCounters()
    run_id = new_prefixed_id("tagrun")
    now = utc_now_iso()

    connection.execute(
        """
        INSERT INTO tagger_runs(
          tagger_run_id, trigger, tagger_version, harness_version, policy_version,
          auto_attach_threshold, per_doc_auto_attach_limit, per_doc_candidate_limit,
          per_run_new_tag_proposal_limit, per_run_total_candidate_limit,
          scanned_documents, auto_attached_count, candidate_count,
          new_tag_proposal_count, blocked_candidate_count, preserved_manual_count,
          error_count, status, created_at, finished_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0, 0, 'running', ?, NULL)
        """,
        (
            run_id,
            trigger,
            TAGGER_VERSION,
            HARNESS_VERSION,
            POLICY_VERSION,
            auto_attach_threshold,
            active_budget.per_doc_auto_attach_limit,
            active_budget.per_doc_candidate_limit,
            active_budget.per_run_new_tag_proposal_limit,
            active_budget.per_run_total_candidate_limit,
            now,
        ),
    )
    connection.commit()

    documents = _load_documents(connection, doc_id=doc_id)
    known_phrases = load_known_tag_phrases(connection)

    scanned = 0
    auto_attached = 0
    candidates = 0
    new_proposals = 0
    blocked = 0
    preserved_manual = 0
    errors = 0

    for document in documents:
        scanned += 1
        try:
            doc_result = _tag_document(
                connection,
                document=document,
                run_id=run_id,
                known_phrases=known_phrases,
                budget=active_budget,
                counters=counters,
                auto_attach_threshold=auto_attach_threshold,
            )
            auto_attached += len(doc_result.auto_attached_tag_ids)
            candidates += len(doc_result.candidate_ids)
            for candidate_id in doc_result.candidate_ids:
                row = connection.execute(
                    "SELECT candidate_type FROM tag_candidates WHERE candidate_id = ?",
                    (candidate_id,),
                ).fetchone()
                if row is not None and str(row["candidate_type"]) == "propose_new":
                    new_proposals += 1
            blocked += doc_result.blocked_count
            if doc_result.outcome == "preserved_manual":
                preserved_manual += 1
        except Exception as exc:  # noqa: BLE001 - record per-document errors on run
            errors += 1
            _insert_tagger_result(
                connection,
                run_id=run_id,
                doc_id=str(document["doc_id"]),
                revision_id=str(document["current_revision_id"]),
                outcome="error",
                auto_attached_tag_ids=[],
                candidate_ids=[],
                blocked_candidates=[{"error": str(exc)}],
                evidence={},
                warnings=[],
                error={"message": str(exc)},
            )

    status = "succeeded"
    if errors and scanned == errors:
        status = "failed"
    elif errors or blocked:
        status = "completed_with_warnings"

    finished = utc_now_iso()
    connection.execute(
        """
        UPDATE tagger_runs
        SET scanned_documents = ?,
            auto_attached_count = ?,
            candidate_count = ?,
            new_tag_proposal_count = ?,
            blocked_candidate_count = ?,
            preserved_manual_count = ?,
            error_count = ?,
            status = ?,
            finished_at = ?
        WHERE tagger_run_id = ?
        """,
        (
            scanned,
            auto_attached,
            candidates,
            new_proposals,
            blocked,
            preserved_manual,
            errors,
            status,
            finished,
            run_id,
        ),
    )
    connection.commit()

    return TagTaggerRunResult(
        tagger_run_id=run_id,
        scanned_documents=scanned,
        auto_attached_count=auto_attached,
        candidate_count=candidates,
        new_tag_proposal_count=new_proposals,
        blocked_candidate_count=blocked,
        error_count=errors,
        preserved_manual_count=preserved_manual,
        status=status,
    )


def _tag_document(
    connection: sqlite3.Connection,
    *,
    document: sqlite3.Row,
    run_id: str,
    known_phrases: tuple[str, ...],
    budget: TagVolumeBudget,
    counters: TagBudgetCounters,
    auto_attach_threshold: float,
) -> TagTaggerDocumentResult:
    doc_id = str(document["doc_id"])
    revision_id = str(document["current_revision_id"])
    category_id = str(document["category_id"] or "") or None

    attached = {
        normalize_tag_name(str(row["name"]))
        for row in list_document_tags(connection, doc_id)
    }
    manual_sources = {
        normalize_tag_name(str(row["name"]))
        for row in connection.execute(
            """
            SELECT t.normalized_name
            FROM document_tags dt
            JOIN tags t ON t.tag_id = dt.tag_id
            WHERE dt.doc_id = ?
              AND dt.deleted_at IS NULL
              AND dt.status = 'active'
              AND dt.source = 'manual'
            """,
            (doc_id,),
        )
    }

    chunks = list(
        connection.execute(
            """
            SELECT chunk_id, text, heading_path_json
            FROM chunks
            WHERE doc_id = ?
              AND revision_id = ?
              AND is_current = 1
              AND deleted_at IS NULL
            ORDER BY sequence
            """,
            (doc_id, revision_id),
        )
    )
    drafts = extract_feature_drafts(
        connection,
        doc_id=doc_id,
        revision_id=revision_id,
        chunks=chunks,
        known_phrases=known_phrases,
    )

    result_id = new_prefixed_id("tagres")
    auto_attached_ids: list[str] = []
    candidate_ids: list[str] = []
    blocked_candidates: list[dict[str, object]] = []
    warnings: list[str] = []
    evidence: dict[str, object] = {"draft_count": len(drafts)}

    for draft in drafts:
        if draft.normalized_text in attached:
            continue

        resolution = resolve_tag_candidate(
            connection,
            draft.text,
            doc_category_id=category_id,
        )

        if resolution.outcome in {"blocked", "deprecated", "archived", "merged"}:
            blocked_candidates.append(
                {
                    "raw": draft.text,
                    "outcome": resolution.outcome,
                    "reasons": list(resolution.reasons),
                }
            )
            continue

        if is_auto_attach_eligible(resolution):
            if draft.confidence >= auto_attach_threshold:
                auto_budget = check_auto_attach_budget(budget, counters, doc_id=doc_id)
                if auto_budget.outcome == "allowed":
                    change = add_document_tag(
                        connection,
                        doc_id,
                        draft.text,
                        source="auto",
                        confidence=draft.confidence,
                        created_by="tagger",
                        revision_id=revision_id,
                        evidence_chunk_ids=[draft.chunk_id],
                    )
                    if change.changed:
                        record_auto_attach(counters, doc_id=doc_id)
                        auto_attached_ids.append(change.tag_id)
                        attached.add(draft.normalized_text)
                    continue
                warnings.append(auto_budget.reason)

            if draft.confidence >= MIN_ATTACH_CANDIDATE_CONFIDENCE:
                decision = TagGovernanceDecision(
                    resolution=resolution,
                    admission=None,
                    budget=None,
                    candidate_type="attach_existing",
                    allowed=True,
                    reasons=resolution.reasons,
                )
                candidate_budget = check_candidate_budget(
                    budget,
                    counters,
                    doc_id=doc_id,
                    is_new_tag_proposal=False,
                )
                if candidate_budget.outcome == "allowed":
                    candidate_id = insert_tag_governance_candidate(
                        connection,
                        candidate_type="attach_existing",
                        raw_name=draft.text,
                        tag_type=draft.feature_type,
                        doc_id=doc_id,
                        revision_id=revision_id,
                        tagger_run_id=run_id,
                        tagger_result_id=result_id,
                        decision=decision,
                        target_tag_id=resolution.canonical_tag_id,
                        confidence=draft.confidence,
                        evidence_chunk_ids=[draft.chunk_id],
                    )
                    record_candidate(counters, doc_id=doc_id, is_new_tag_proposal=False)
                    candidate_ids.append(candidate_id)
                else:
                    blocked_candidates.append(
                        {
                            "raw": draft.text,
                            "outcome": "budget_denied",
                            "reason": candidate_budget.reason,
                        }
                    )
            continue

        decision = evaluate_tag_governance(
            connection,
            draft.text,
            doc_id=doc_id,
            doc_category_id=category_id,
            budget=budget,
            counters=counters,
            tag_type=draft.feature_type,
        )
        if decision.candidate_type == "propose_new" and decision.allowed:
            candidate_id = insert_tag_governance_candidate(
                connection,
                candidate_type="propose_new",
                raw_name=draft.text,
                tag_type=draft.feature_type,
                doc_id=doc_id,
                revision_id=revision_id,
                tagger_run_id=run_id,
                tagger_result_id=result_id,
                decision=decision,
                confidence=draft.confidence,
                evidence_chunk_ids=[draft.chunk_id],
            )
            record_candidate(counters, doc_id=doc_id, is_new_tag_proposal=True)
            candidate_ids.append(candidate_id)
        else:
            blocked_candidates.append(
                {
                    "raw": draft.text,
                    "outcome": decision.resolution.outcome,
                    "reasons": list(decision.reasons),
                }
            )

    if auto_attached_ids and not candidate_ids:
        outcome = "auto_attached"
    elif candidate_ids:
        outcome = "candidates_created"
    elif manual_sources and not drafts:
        outcome = "preserved_manual"
    elif not drafts and not auto_attached_ids:
        outcome = "no_candidates"
    elif manual_sources:
        outcome = "preserved_manual"
    else:
        outcome = "no_candidates"
    if warnings:
        outcome = "completed_with_warnings" if outcome != "auto_attached" else outcome

    _insert_tagger_result(
        connection,
        run_id=run_id,
        doc_id=doc_id,
        revision_id=revision_id,
        outcome=outcome,
        auto_attached_tag_ids=auto_attached_ids,
        candidate_ids=candidate_ids,
        blocked_candidates=blocked_candidates,
        evidence=evidence,
        warnings=warnings,
        error=None,
        result_id=result_id,
    )

    return TagTaggerDocumentResult(
        tagger_result_id=result_id,
        doc_id=doc_id,
        outcome=outcome,
        auto_attached_tag_ids=tuple(auto_attached_ids),
        candidate_ids=tuple(candidate_ids),
        blocked_count=len(blocked_candidates),
    )


def _insert_tagger_result(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    doc_id: str,
    revision_id: str,
    outcome: str,
    auto_attached_tag_ids: list[str],
    candidate_ids: list[str],
    blocked_candidates: list[dict[str, object]],
    evidence: dict[str, object],
    warnings: list[str],
    error: dict[str, object] | None,
    result_id: str | None = None,
) -> str:
    tagger_result_id = result_id or new_prefixed_id("tagres")
    connection.execute(
        """
        INSERT INTO tagger_results(
          tagger_result_id, tagger_run_id, doc_id, revision_id, outcome,
          auto_attached_tag_ids_json, candidate_ids_json, blocked_candidates_json,
          evidence_json, warnings_json, error_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tagger_result_id,
            run_id,
            doc_id,
            revision_id,
            outcome,
            json.dumps(auto_attached_tag_ids, ensure_ascii=False),
            json.dumps(candidate_ids, ensure_ascii=False),
            json.dumps(blocked_candidates, ensure_ascii=False),
            json.dumps(evidence, ensure_ascii=False),
            json.dumps(warnings, ensure_ascii=False),
            json.dumps(error, ensure_ascii=False) if error else None,
            utc_now_iso(),
        ),
    )
    connection.commit()
    return tagger_result_id


def _load_documents(connection: sqlite3.Connection, *, doc_id: str | None) -> list[sqlite3.Row]:
    if doc_id is not None:
        row = connection.execute(
            """
            SELECT d.doc_id, d.current_revision_id, d.category_id, d.title
            FROM documents d
            WHERE d.doc_id = ?
              AND d.deleted_at IS NULL
              AND d.status = 'active'
              AND d.current_revision_id IS NOT NULL
            """,
            (doc_id,),
        ).fetchone()
        return [row] if row is not None else []
    return list(
        connection.execute(
            """
            SELECT d.doc_id, d.current_revision_id, d.category_id, d.title
            FROM documents d
            WHERE d.deleted_at IS NULL
              AND d.status = 'active'
              AND d.current_revision_id IS NOT NULL
              AND d.ingest_status = 'revisioned'
            ORDER BY d.created_at
            """
        )
    )
