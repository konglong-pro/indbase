"""Run the aggregate M10 candidate cards completion gate."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import indbase_core.normalizers as normalizers
import m10_candidate_card_preflight_gate
import m101_candidate_extraction_gate
import m102_card_review_gate
import m103_card_hardening_gate


ROOT = Path.cwd()


def main() -> None:
    root = ROOT / ".tmp" / f"m10-candidate-cards-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    root.mkdir(parents=True)
    summary = _run_gate(root)

    hard_zero_keys = [
        "preflight_duplicate_translation_overwrites",
        "preflight_duplicate_translation_output_collisions",
        "preflight_multiple_target_output_collisions",
        "preflight_selected_full_output_collisions",
        "preflight_partial_translation_success_records",
        "preflight_partial_translation_output_files",
        "preflight_partial_failed_errors_missing",
        "preflight_missing_translation_output_source_corruption",
        "preflight_orphan_translation_records_undetected",
        "preflight_archived_docs_in_default_search",
        "preflight_source_shells_searchable",
        "preflight_old_revision_default_results",
        "preflight_translations_mutating_source",
        "extraction_atomic_notes_written_before_accept",
        "extraction_claims_without_source_chunks",
        "extraction_claims_with_missing_chunks",
        "extraction_claims_without_quotes",
        "extraction_source_shell_cards",
        "extraction_archived_doc_cards",
        "extraction_old_revision_cards_default",
        "extraction_no_chunk_cards",
        "extraction_old_revision_candidates_default_listed",
        "review_rejected_cards_written_to_atomic",
        "review_accepted_cards_missing_note",
        "review_accepted_notes_missing_source_chunks",
        "review_accepted_notes_with_uncited_claims",
        "review_accepted_cards_reaccepted",
        "review_accepted_cards_rejected",
        "review_rejected_cards_accepted",
        "hardening_orphan_candidate_cards_undetected",
        "hardening_missing_accepted_note_undetected",
        "hardening_accepted_card_without_sources_undetected",
        "hardening_uncited_accepted_claims_undetected",
        "hardening_orphan_atomic_notes_undetected",
    ]
    hard_zero = {key: summary[key] for key in hard_zero_keys}
    if any(value != 0 for value in hard_zero.values()):
        raise RuntimeError(f"M10 candidate cards hard zero metrics failed: {hard_zero}")

    positives = {
        "preflight_candidate_preflight_passed": summary["preflight_candidate_preflight_passed"] == 1,
        "preflight_active_current_documents": summary["preflight_active_current_documents"] > 0,
        "preflight_current_chunks": summary["preflight_current_chunks"] > 0,
        "extraction_candidate_cards_created": summary["extraction_candidate_cards_created"] > 0,
        "extraction_candidate_sources_created": summary["extraction_candidate_sources_created"] > 0,
        "extraction_stale_candidate_cards_viewable": summary["extraction_stale_candidate_cards_viewable"] == 1,
        "review_accepted_cards_written": summary["review_accepted_cards_written"] > 0,
        "review_candidate_sources_preserved_after_reject": summary["review_candidate_sources_preserved_after_reject"] == 1,
        "hardening_orphan_candidate_cards_detected": summary["hardening_orphan_candidate_cards_detected"] > 0,
        "hardening_missing_accepted_note_detected": summary["hardening_missing_accepted_note_detected"] == 1,
        "hardening_accepted_card_without_sources_detected": summary["hardening_accepted_card_without_sources_detected"] == 1,
        "hardening_uncited_accepted_claims_detected": summary["hardening_uncited_accepted_claims_detected"] == 1,
        "hardening_orphan_atomic_notes_detected": summary["hardening_orphan_atomic_notes_detected"] == 1,
    }
    if not all(positives.values()):
        raise RuntimeError(f"M10 candidate cards positive metrics failed: {positives}; summary={summary}")

    summary["m10_candidate_cards_complete"] = 1
    print(json.dumps(summary, sort_keys=True))
    print("M10_CANDIDATE_CARDS_GATE=passed")
    print(f"DOGFOOD_ROOT={root}")


def _run_gate(root: Path) -> dict[str, int]:
    preflight = _run_with_markitdown_restore(
        lambda: m10_candidate_card_preflight_gate._run_gate(root / "preflight")
    )
    extraction = _run_with_markitdown_restore(
        lambda: m101_candidate_extraction_gate._run_gate(root / "extraction")
    )
    review = m102_card_review_gate._run_gate(root / "review")
    hardening = m103_card_hardening_gate._run_gate(root / "hardening")
    return {
        **_prefix("preflight", preflight),
        **_prefix("extraction", extraction),
        **_prefix("review", review),
        **_prefix("hardening", hardening),
    }


def _run_with_markitdown_restore(action) -> dict[str, int]:
    original_markitdown = normalizers._run_markitdown_file
    try:
        return action()
    finally:
        normalizers._run_markitdown_file = original_markitdown


def _prefix(prefix: str, values: dict[str, int]) -> dict[str, int]:
    return {f"{prefix}_{key}": value for key, value in values.items()}


if __name__ == "__main__":
    main()
