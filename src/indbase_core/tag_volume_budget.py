"""Tag volume budget checks for v0.3.2."""

from __future__ import annotations

from dataclasses import dataclass, field

from indbase_core.tag_governance import (
    DEFAULT_FORMAL_TAGS_SOFT_LIMIT,
    DEFAULT_PER_DOC_AUTO_ATTACH_LIMIT,
    DEFAULT_PER_DOC_CANDIDATE_LIMIT,
    DEFAULT_PER_RUN_NEW_TAG_PROPOSAL_LIMIT,
    DEFAULT_PER_RUN_TOTAL_CANDIDATE_LIMIT,
)


@dataclass(frozen=True)
class TagVolumeBudget:
    per_doc_auto_attach_limit: int = DEFAULT_PER_DOC_AUTO_ATTACH_LIMIT
    per_doc_candidate_limit: int = DEFAULT_PER_DOC_CANDIDATE_LIMIT
    per_run_new_tag_proposal_limit: int = DEFAULT_PER_RUN_NEW_TAG_PROPOSAL_LIMIT
    per_run_total_candidate_limit: int = DEFAULT_PER_RUN_TOTAL_CANDIDATE_LIMIT
    formal_tags_soft_limit: int = DEFAULT_FORMAL_TAGS_SOFT_LIMIT


@dataclass
class TagBudgetCounters:
    auto_attached_by_doc: dict[str, int] = field(default_factory=dict)
    candidates_by_doc: dict[str, int] = field(default_factory=dict)
    new_tag_proposals_run: int = 0
    total_candidates_run: int = 0


@dataclass(frozen=True)
class TagBudgetDecision:
    outcome: str
    reason: str
    limits: dict[str, int]


def default_budget() -> TagVolumeBudget:
    return TagVolumeBudget()


def check_auto_attach_budget(
    budget: TagVolumeBudget,
    counters: TagBudgetCounters,
    *,
    doc_id: str,
) -> TagBudgetDecision:
    used = counters.auto_attached_by_doc.get(doc_id, 0)
    if used >= budget.per_doc_auto_attach_limit:
        return TagBudgetDecision(
            outcome="denied",
            reason="per_doc_auto_attach_limit",
            limits={
                "limit": budget.per_doc_auto_attach_limit,
                "used": used,
            },
        )
    return TagBudgetDecision(
        outcome="allowed",
        reason="within_budget",
        limits={"limit": budget.per_doc_auto_attach_limit, "used": used},
    )


def check_candidate_budget(
    budget: TagVolumeBudget,
    counters: TagBudgetCounters,
    *,
    doc_id: str,
    is_new_tag_proposal: bool,
) -> TagBudgetDecision:
    doc_used = counters.candidates_by_doc.get(doc_id, 0)
    if doc_used >= budget.per_doc_candidate_limit:
        return TagBudgetDecision(
            outcome="denied",
            reason="per_doc_candidate_limit",
            limits={"limit": budget.per_doc_candidate_limit, "used": doc_used},
        )
    if counters.total_candidates_run >= budget.per_run_total_candidate_limit:
        return TagBudgetDecision(
            outcome="denied",
            reason="per_run_total_candidate_limit",
            limits={
                "limit": budget.per_run_total_candidate_limit,
                "used": counters.total_candidates_run,
            },
        )
    if is_new_tag_proposal and counters.new_tag_proposals_run >= budget.per_run_new_tag_proposal_limit:
        return TagBudgetDecision(
            outcome="denied",
            reason="per_run_new_tag_proposal_limit",
            limits={
                "limit": budget.per_run_new_tag_proposal_limit,
                "used": counters.new_tag_proposals_run,
            },
        )
    return TagBudgetDecision(
        outcome="allowed",
        reason="within_budget",
        limits={
            "per_doc_limit": budget.per_doc_candidate_limit,
            "per_doc_used": doc_used,
            "per_run_total_used": counters.total_candidates_run,
        },
    )


def record_auto_attach(counters: TagBudgetCounters, *, doc_id: str) -> None:
    counters.auto_attached_by_doc[doc_id] = counters.auto_attached_by_doc.get(doc_id, 0) + 1


def record_candidate(
    counters: TagBudgetCounters,
    *,
    doc_id: str,
    is_new_tag_proposal: bool,
) -> None:
    counters.candidates_by_doc[doc_id] = counters.candidates_by_doc.get(doc_id, 0) + 1
    counters.total_candidates_run += 1
    if is_new_tag_proposal:
        counters.new_tag_proposals_run += 1
