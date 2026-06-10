"""Compose resolution, admission, and budget for one raw candidate."""

from __future__ import annotations

from dataclasses import dataclass

import sqlite3

from indbase_core.tag_admission import TagAdmissionResult, evaluate_tag_admission
from indbase_core.tag_governance import CANDIDATE_TYPES
from indbase_core.tag_resolution import TagResolutionResult, is_auto_attach_eligible, resolve_tag_candidate
from indbase_core.tag_volume_budget import (
    TagBudgetCounters,
    TagBudgetDecision,
    TagVolumeBudget,
    check_candidate_budget,
)


@dataclass(frozen=True)
class TagGovernanceDecision:
    resolution: TagResolutionResult
    admission: TagAdmissionResult | None
    budget: TagBudgetDecision | None
    candidate_type: str | None
    allowed: bool
    reasons: tuple[str, ...]


def evaluate_tag_governance(
    connection: sqlite3.Connection,
    raw_name: str,
    *,
    doc_id: str,
    doc_category_id: str | None = None,
    budget: TagVolumeBudget | None = None,
    counters: TagBudgetCounters | None = None,
    tag_type: str = "topic",
) -> TagGovernanceDecision:
    """Run resolution, admission (for new tags), and candidate budget checks."""
    resolution = resolve_tag_candidate(
        connection,
        raw_name,
        doc_category_id=doc_category_id,
    )
    admission: TagAdmissionResult | None = None
    budget_decision: TagBudgetDecision | None = None
    candidate_type: str | None = None
    reasons: list[str] = list(resolution.reasons)

    if resolution.outcome == "blocked":
        return TagGovernanceDecision(
            resolution=resolution,
            admission=None,
            budget=None,
            candidate_type=None,
            allowed=False,
            reasons=tuple(reasons),
        )

    if is_auto_attach_eligible(resolution):
        candidate_type = "attach_existing"
        return TagGovernanceDecision(
            resolution=resolution,
            admission=None,
            budget=None,
            candidate_type=candidate_type,
            allowed=True,
            reasons=tuple(reasons),
        )

    if resolution.outcome in {"deprecated", "archived", "merged"}:
        return TagGovernanceDecision(
            resolution=resolution,
            admission=None,
            budget=None,
            candidate_type=None,
            allowed=False,
            reasons=tuple(reasons),
        )

    if resolution.outcome != "propose_new":
        return TagGovernanceDecision(
            resolution=resolution,
            admission=None,
            budget=None,
            candidate_type=None,
            allowed=False,
            reasons=tuple(reasons + ("unexpected_resolution",)),
        )

    admission = evaluate_tag_admission(
        connection,
        resolution.raw_name,
        normalized_name=resolution.normalized_name,
        tag_type=tag_type,
    )
    if admission.outcome == "rejected":
        reasons.extend(admission.reasons)
        return TagGovernanceDecision(
            resolution=resolution,
            admission=admission,
            budget=None,
            candidate_type=None,
            allowed=False,
            reasons=tuple(reasons),
        )

    candidate_type = "propose_new"
    active_budget = budget or TagVolumeBudget()
    active_counters = counters or TagBudgetCounters()
    budget_decision = check_candidate_budget(
        active_budget,
        active_counters,
        doc_id=doc_id,
        is_new_tag_proposal=True,
    )
    if budget_decision.outcome == "denied":
        reasons.append(budget_decision.reason)
        return TagGovernanceDecision(
            resolution=resolution,
            admission=admission,
            budget=budget_decision,
            candidate_type=candidate_type,
            allowed=False,
            reasons=tuple(reasons),
        )

    return TagGovernanceDecision(
        resolution=resolution,
        admission=admission,
        budget=budget_decision,
        candidate_type=candidate_type,
        allowed=True,
        reasons=tuple(reasons),
    )


def validate_candidate_type(candidate_type: str) -> str:
    clean = candidate_type.strip().lower()
    if clean not in CANDIDATE_TYPES:
        allowed = ", ".join(sorted(CANDIDATE_TYPES))
        raise ValueError(f"Invalid candidate_type {candidate_type!r}; expected one of: {allowed}")
    return clean
