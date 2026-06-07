"""Layer E: v0.3.1 taxonomy real-corpus dogfood.

Uses repo-local project content by default (same staging as MVP/v0.2 close-out).
Set INDB_REAL_CORPUS to dogfood a private folder of supported files.

Ingest uses the deterministic swallow gate stub so CI and local runs do not require
the swallow SDK. Staged .md/.txt files receive TRUSTED_GATE_NEEDLE for promotion.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import os
from pathlib import Path

from gate_common import (
    TRUSTED_NEEDLE,
    configure_v02_vault,
    doctor_hard_metrics,
    install_deterministic_swallow_stub,
    write_gate_summary,
    ROOT,
)
from mvp_closeout_common import stage_real_corpus

from indbase_core.category_manager import suggest_category_assignments
from indbase_core.db import connect
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.profile import build_document_profile
from indbase_core.taxonomy_janitor import run_taxonomy_audit
from indbase_core.taxonomy_manager import analyze_all_profiled_documents
from indbase_core.vault import init_vault

_TAXONOMY_HARD_DOCTOR_CODES = {
    "feature_atom_missing_chunk",
    "feature_atom_wrong_revision",
    "profile_active_for_old_revision",
    "profile_active_for_archived_doc",
    "invalid_tag_type",
    "tag_alias_collision",
    "tag_candidate_missing_evidence",
    "promoted_candidate_without_tag",
    "taxonomy_suggestion_dangling_reference",
    "taxonomy_suggestion_invalid_category",
    "taxonomy_suggestion_invalid_tag",
    "document_tag_dangling_reference",
    "document_tag_inactive_tag",
}


def _inject_swallow_gate_needle(sources_root: Path) -> int:
    updated = 0
    for path in sources_root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.casefold() not in {".md", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if TRUSTED_NEEDLE in text:
            continue
        if len(text.strip()) < 80:
            text = text.rstrip() + "\n\n" + ("Supporting paragraph. " * 12)
        path.write_text(text.rstrip() + f"\n\nNeedle: {TRUSTED_NEEDLE}\n", encoding="utf-8")
        updated += 1
    return updated


def _min_revisions(source_mode: str) -> int:
    raw = os.environ.get("INDB_V031_DOGFOOD_MIN_REVISIONS")
    if raw:
        return max(1, int(raw))
    return 5 if source_mode == "external" else 15


def main() -> None:
    gate_root = ROOT / ".tmp" / f"v031-real-corpus-dogfood-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    gate_root.mkdir(parents=True)
    corpus = stage_real_corpus(gate_root, target_files=85)
    sources = Path(str(corpus["sources"]))
    needle_files = _inject_swallow_gate_needle(sources)

    install_deterministic_swallow_stub()
    vault = gate_root / "vault"
    init_vault(vault, category_template="indbase_default_v1")
    configure_v02_vault(vault, swallow_ingest=True, transition_output=False, min_markdown_chars=80)

    result = run_m3_ingest_pipeline(vault, sources, recursive=True)
    min_revisions = _min_revisions(str(corpus.get("source_mode") or "repo-local"))
    if result.written_revisions < min_revisions:
        raise RuntimeError(
            f"dogfood produced too few revisions ({result.written_revisions} < {min_revisions}): {result}"
        )

    profile_built = 0
    profile_skipped = 0
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        eligible = connection.execute(
            """
            SELECT doc_id
            FROM documents
            WHERE status = 'active'
              AND deleted_at IS NULL
              AND current_revision_id IS NOT NULL
              AND ingest_status = 'revisioned'
            ORDER BY created_at, doc_id
            """
        ).fetchall()
        for row in eligible:
            try:
                build_document_profile(connection, str(row["doc_id"]))
                profile_built += 1
            except ValueError:
                profile_skipped += 1

        analyze_run = analyze_all_profiled_documents(connection, limit=100)
        category_run = suggest_category_assignments(
            connection,
            all_uncategorized=True,
            min_confidence=0.55,
            limit=100,
        )
        janitor = run_taxonomy_audit(connection)

        counts = connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM document_profiles WHERE status = 'active') AS active_profiles,
              (SELECT COUNT(*) FROM feature_atoms WHERE status = 'active') AS active_features,
              (SELECT COUNT(*) FROM taxonomy_suggestions WHERE status = 'pending') AS pending_taxonomy_suggestions,
              (SELECT COUNT(*) FROM tag_candidates WHERE status = 'pending') AS pending_tag_candidates,
              (SELECT COUNT(*) FROM tags WHERE deleted_at IS NULL AND status = 'active'
                 AND (type IS NULL OR type = '')) AS untyped_active_tags
            """
        ).fetchone()

    doctor = doctor_hard_metrics(vault)
    taxonomy_hard = [code for code in doctor["codes"] if code in _TAXONOMY_HARD_DOCTOR_CODES]
    if doctor["critical_doctor_findings"]:
        raise RuntimeError(f"dogfood doctor hard findings: {doctor}")
    if taxonomy_hard:
        raise RuntimeError(f"dogfood taxonomy doctor hard codes: {taxonomy_hard}")
    if int(counts["untyped_active_tags"] or 0) != 0:
        raise RuntimeError(f"untyped active tags remain: {counts['untyped_active_tags']}")
    if profile_built < min(5, len(eligible)):
        raise RuntimeError(
            f"too few profiles built ({profile_built}) for {len(eligible)} eligible documents"
        )
    if int(counts["active_profiles"] or 0) < profile_built:
        raise RuntimeError(f"profile count mismatch: built={profile_built} active={counts['active_profiles']}")

    tag_suggestions = sum(item.tag_assign_suggestions for item in analyze_run.results)
    tag_candidates = sum(item.tag_candidates for item in analyze_run.results)

    summary = {
        "layer": "E",
        "gate": "v031_real_corpus_dogfood",
        "source_mode": corpus.get("source_mode"),
        "input_files": corpus.get("input_files"),
        "needle_injected_files": needle_files,
        "ingest_status": result.status,
        "written_revisions": result.written_revisions,
        "succeeded_items": result.succeeded_items,
        "failed_items": result.failed_items,
        "unsupported_items": result.unsupported_items,
        "duplicate_items": result.duplicate_items,
        "eligible_documents": len(eligible),
        "profiles_built": profile_built,
        "profiles_skipped": profile_skipped,
        "active_profiles": int(counts["active_profiles"] or 0),
        "active_features": int(counts["active_features"] or 0),
        "taxonomy_analyzed_documents": analyze_run.scanned_documents,
        "tag_assign_suggestions": tag_suggestions,
        "tag_candidates_created": tag_candidates,
        "category_suggestions": category_run.suggested_documents,
        "pending_taxonomy_suggestions": int(counts["pending_taxonomy_suggestions"] or 0),
        "pending_tag_candidates": int(counts["pending_tag_candidates"] or 0),
        "janitor_findings": len(janitor.findings),
        "janitor_suggestions_created": janitor.suggestions_created,
        "doctor": doctor,
        "untyped_active_tags": int(counts["untyped_active_tags"] or 0),
    }
    write_gate_summary(summary, gate_name="V031_REAL_CORPUS_DOGFOOD_GATE", gate_root=gate_root)


if __name__ == "__main__":
    main()
