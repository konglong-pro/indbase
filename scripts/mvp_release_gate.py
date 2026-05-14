"""Run the aggregate MVP release gate.

This gate is intentionally an orchestrator. It does not add new feature
coverage; it verifies that the existing milestone gates still pass together
and reduces their summaries to the hard release metrics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path.cwd()


@dataclass(frozen=True)
class Gate:
    name: str
    script_name: str


GATES = (
    Gate("m3_dogfood_gate", "m3_dogfood_gate.py"),
    Gate("m4_tui_lite_gate", "m4_tui_lite_gate.py"),
    Gate("m5_catalog_review_gate", "m5_catalog_review_gate.py"),
    Gate("m63_pdf_ocr_hardening_gate", "m63_pdf_ocr_hardening_gate.py"),
    Gate("m71_embedding_index_gate", "m71_embedding_index_gate.py"),
    Gate("m72_hybrid_search_gate", "m72_hybrid_search_gate.py"),
    Gate("m8_classification_gate", "m8_classification_gate.py"),
    Gate("m8_classification_hardening_gate", "m81_classification_hardening_gate.py"),
    Gate("m91_translation_gate", "m91_translation_gate.py"),
    Gate("m92_translation_gate", "m92_translation_gate.py"),
    Gate("m93_translation_hardening_gate", "m93_translation_hardening_gate.py"),
    Gate("m10_candidate_cards_gate", "m10_candidate_cards_gate.py"),
    Gate("v01_release_candidate_gate", "v01_release_candidate_gate.py"),
    Gate("doctor_negative_gate", "doctor_negative_gate.py"),
    Gate("mvp_doctor_full_negative_gate", "mvp_doctor_full_negative_gate.py"),
    Gate("mvp_windows_path_unicode_gate", "mvp_windows_path_unicode_gate.py"),
    Gate("metadata_consistency_gate", "metadata_consistency_gate.py"),
)


HARD_ZERO_KEYS = (
    "critical_doctor_findings",
    "unexpected_errors",
    "index_integrity_errors",
    "fts_desync",
    "orphan_files",
    "orphan_records",
    "zero_chunk_current_revisions",
    "unsupported_documents_created",
    "source_shells_searchable",
    "embedded_archived_chunks",
    "embedded_old_revision_chunks",
    "hybrid_results_missing_source_fields",
    "translations_missing_source_binding",
    "candidate_cards_missing_sources",
    "accepted_notes_with_uncited_claims",
    "missing_generated_outputs_undetected",
)


def main() -> None:
    gate_root = ROOT / ".tmp" / f"mvp-release-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    gate_root.mkdir(parents=True)

    summaries: dict[str, dict[str, object]] = {}
    dogfood_roots: dict[str, str] = {}
    for gate in GATES:
        summary, dogfood_root = _run_gate_script(gate)
        summaries[gate.name] = summary
        if dogfood_root is not None:
            dogfood_roots[gate.name] = str(dogfood_root)

    aggregate = _aggregate_release_metrics(summaries)
    aggregate["gates_run"] = len(GATES)
    aggregate["mvp_release_gate_complete"] = 1

    hard_failures = {key: aggregate[key] for key in HARD_ZERO_KEYS if int(aggregate[key]) != 0}
    if hard_failures:
        raise RuntimeError(f"MVP release hard standards failed: {hard_failures}")

    (gate_root / "gate_summaries.json").write_text(
        json.dumps(
            {
                "hard_metrics": {key: aggregate[key] for key in HARD_ZERO_KEYS},
                "dogfood_roots": dogfood_roots,
                "summaries": summaries,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    print(json.dumps(aggregate, sort_keys=True))
    print("MVP_RELEASE_GATE=passed")
    print(f"DOGFOOD_ROOT={gate_root}")


def _run_gate_script(gate: Gate) -> tuple[dict[str, object], Path | None]:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / gate.script_name)],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{gate.script_name} failed with exit {completed.returncode}\n{completed.stdout}")

    summary: dict[str, object] | None = None
    dogfood_root: Path | None = None
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("{") and summary is None:
            summary = json.loads(line)
        if line.startswith("DOGFOOD_ROOT="):
            dogfood_root = Path(line.split("=", 1)[1])
    if summary is None:
        raise RuntimeError(f"{gate.script_name} did not emit a JSON summary\n{completed.stdout}")
    return summary, dogfood_root


def _aggregate_release_metrics(summaries: dict[str, dict[str, object]]) -> dict[str, int]:
    m63 = summaries["m63_pdf_ocr_hardening_gate"]
    m71 = summaries["m71_embedding_index_gate"]
    m72 = summaries["m72_hybrid_search_gate"]
    m91 = summaries["m91_translation_gate"]
    m92 = summaries["m92_translation_gate"]
    m10 = summaries["m10_candidate_cards_gate"]
    v01 = summaries["v01_release_candidate_gate"]
    full_doctor = summaries["mvp_doctor_full_negative_gate"]

    hybrid_results = _int(m72.get("hybrid_results"))
    hybrid_missing_source_fields = (
        max(0, hybrid_results - _int(m72.get("hybrid_results_with_doc_id")))
        + max(0, hybrid_results - _int(m72.get("hybrid_results_with_revision_id")))
        + max(0, hybrid_results - _int(m72.get("hybrid_results_with_chunk_id")))
        + max(0, hybrid_results - _int(m72.get("hybrid_results_with_snippet")))
    )

    missing_translation_output_undetected = 0
    if _int(m10.get("preflight_missing_translation_output_detected")) != 1:
        missing_translation_output_undetected = 1

    metrics = {
        "critical_doctor_findings": _sum_exact(summaries, "critical_doctor_findings"),
        "unexpected_errors": _sum_exact(summaries, "unexpected_errors"),
        "index_integrity_errors": _sum_exact(summaries, "index_integrity_errors"),
        "fts_desync": _sum_exact(summaries, "fts_desync"),
        "orphan_files": _int(v01.get("orphan_files")),
        "orphan_records": _int(v01.get("orphan_records")),
        "zero_chunk_current_revisions": _sum_exact(summaries, "zero_chunk_current_revisions"),
        "unsupported_documents_created": _sum_exact(summaries, "unsupported_documents_created"),
        "source_shells_searchable": _int(m63.get("pdf_failed_searchable_documents"))
        + _int(m10.get("preflight_source_shells_searchable")),
        "embedded_archived_chunks": _int(m71.get("embedded_archived_chunks")),
        "embedded_old_revision_chunks": _int(m71.get("embedded_old_revision_chunks")),
        "hybrid_results_missing_source_fields": hybrid_missing_source_fields,
        "translations_missing_source_binding": _int(m91.get("translation_missing_doc_revision_chunks"))
        + _int(m92.get("translation_missing_doc_revision_chunks"))
        + _int(m92.get("full_document_missing_current_chunk_ids")),
        "candidate_cards_missing_sources": _int(m10.get("extraction_claims_without_source_chunks"))
        + _int(m10.get("extraction_claims_with_missing_chunks"))
        + _int(m10.get("review_accepted_notes_missing_source_chunks")),
        "accepted_notes_with_uncited_claims": _int(m10.get("review_accepted_notes_with_uncited_claims")),
        "missing_generated_outputs_undetected": missing_translation_output_undetected
        + _int(m10.get("preflight_orphan_translation_records_undetected"))
        + _int(m10.get("hardening_orphan_candidate_cards_undetected"))
        + _int(m10.get("hardening_missing_accepted_note_undetected"))
        + _int(m10.get("hardening_accepted_card_without_sources_undetected"))
        + _int(m10.get("hardening_uncited_accepted_claims_undetected"))
        + _int(m10.get("hardening_orphan_atomic_notes_undetected"))
        + _int(full_doctor.get("missing_generated_outputs_undetected")),
    }
    return metrics


def _sum_exact(summaries: dict[str, dict[str, object]], key: str) -> int:
    return sum(_int(summary.get(key)) for summary in summaries.values())


def _int(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    return int(value)


if __name__ == "__main__":
    main()
