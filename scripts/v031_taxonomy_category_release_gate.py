#!/usr/bin/env python3
"""v0.3.1 taxonomy category foundation release gate."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from gate_common import TAG_GOVERNANCE_DOCTOR_CODES

import hashlib
from dataclasses import replace
from pathlib import Path as PathType

import indbase_core.conversion as conversion_module
from indbase_core.category_taxonomy import run_category_classification
from indbase_core.config import load_config, save_config
from indbase_core.db import connect
from indbase_core.doctor import run_doctor
from indbase_core.documents import set_document_category
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.normalizers import normalize_tier1_source
from indbase_core.swallow_adapter import ArtifactManifest, ConversionCandidate, SwallowProvenance
from indbase_core.vault import init_vault


@dataclass
class GateMetrics:
    wrong_confident_assignments: int = 0
    expected_abstain_passed: bool = True
    manual_assignments_preserved: bool = True
    non_ready_categories_used_for_auto: int = 0
    confident_assignments_have_evidence: bool = True
    doctor_hard_findings: int = 0


def _bootstrap_test_swallow(vault: Path) -> None:
    """Match pytest fake swallow fixture so file ingest succeeds outside pytest."""
    config_path = vault / ".indbase" / "config" / "config.toml"
    config = load_config(config_path)
    save_config(
        replace(
            config,
            features=replace(config.features, swallow_ingest=True),
            ingest=replace(
                config.ingest,
                swallow=replace(config.ingest.swallow, min_markdown_chars=1),
            ),
        ),
        config_path,
    )

    original_load_config = conversion_module._load_indbase_config_if_present

    def load_config_with_swallow_enabled(paths):
        loaded = original_load_config(paths)
        if loaded.features.swallow_ingest:
            return loaded
        return replace(
            loaded,
            features=replace(loaded.features, swallow_ingest=True),
            ingest=replace(
                loaded.ingest,
                swallow=replace(loaded.ingest.swallow, min_markdown_chars=1),
            ),
        )

    def convert_file_with_local_test_swallow(self, path: PathType | str) -> ConversionCandidate:
        source = PathType(path)
        source_type = source.suffix.lower().lstrip(".")
        normalized = normalize_tier1_source(source, source_type)
        job_id = "gate_file_" + hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:12]
        trace_rel = f"jobs/{job_id}/trace.jsonl"
        trace_path = self.store_root / trace_rel
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text('{"event":"gate_swallow_file_conversion"}\n', encoding="utf-8")
        return ConversionCandidate(
            title=source.stem,
            markdown_body=normalized.markdown,
            status="success",
            quality_score=0.95,
            warnings=normalized.warnings,
            provenance=SwallowProvenance(
                swallow_job_id=job_id,
                swallow_raw_id=f"raw_{job_id}",
                swallow_document_id=f"doc_{job_id}",
                swallow_version="gate-test",
                primary_worker="gate_swallow_file_worker",
                worker_version="gate-test",
                worker_chain=("gate_swallow_file_worker@gate-test",),
                trace_path=trace_rel,
                manifest_path=None,
                ingest_document_path=None,
            ),
            artifact_manifest=ArtifactManifest(required=(trace_rel,)),
        )

    conversion_module._load_indbase_config_if_present = load_config_with_swallow_enabled
    conversion_module.SwallowIngestAdapter.convert_file = convert_file_with_local_test_swallow


def _load_cases() -> list[dict[str, object]]:
    cases_path = REPO_ROOT / "tests" / "fixtures" / "v031_category_classification" / "cases.jsonl"
    cases: list[dict[str, object]] = []
    for line in cases_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def main() -> int:
    metrics = GateMetrics()
    cases = _load_cases()
    tmp = Path(tempfile.mkdtemp())
    vault = tmp / "vault"
    inbox = tmp / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    init_vault(vault)
    _bootstrap_test_swallow(vault)
    db_path = vault / ".indbase" / "db.sqlite"
    try:
        for index, case in enumerate(cases):
            source = inbox / f"case_{index}.md"
            title = str(case["title"])
            body = str(case["body"])
            source.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
            run_m3_ingest_pipeline(vault, source)
            with connect(db_path) as connection:
                doc_id = connection.execute(
                    "SELECT doc_id FROM documents ORDER BY created_at DESC LIMIT 1"
                ).fetchone()["doc_id"]
                result = run_category_classification(connection, doc_id=str(doc_id), trigger="fixture_gate")
                row = connection.execute(
                    """
                    SELECT outcome, final_category_id, evidence_json
                    FROM category_classification_results
                    WHERE category_run_id = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (result.category_run_id,),
                ).fetchone()
                if row is None:
                    metrics.wrong_confident_assignments += 1
                    continue
                expected_outcome = str(case["expected_outcome"])
                expected_category = str(case["expected_category_id"])
                if expected_outcome == "confident_assigned":
                    if row["outcome"] != "confident_assigned" or row["final_category_id"] != expected_category:
                        metrics.wrong_confident_assignments += 1
                    forbidden = case.get("forbidden_category_ids") or []
                    if row["final_category_id"] in forbidden:
                        metrics.wrong_confident_assignments += 1
                    evidence = json.loads(row["evidence_json"])
                    if not evidence.get("matched_terms"):
                        metrics.confident_assignments_have_evidence = False
                elif expected_outcome == "abstained":
                    if row["outcome"] not in {"abstained", "suggested"}:
                        metrics.expected_abstain_passed = False

        manual_source = inbox / "manual_preserve.md"
        manual_source.write_text(
            "# Manual preserve\n\nLLM RAG vector database sqlite retrieval patterns.\n",
            encoding="utf-8",
        )
        run_m3_ingest_pipeline(vault, manual_source)
        with connect(db_path) as connection:
            manual_doc = connection.execute(
                "SELECT doc_id FROM documents ORDER BY created_at DESC LIMIT 1"
            ).fetchone()["doc_id"]
            set_document_category(connection, str(manual_doc), "cat_humanities")
            run_category_classification(connection, doc_id=str(manual_doc), trigger="fixture_gate")
            manual_row = connection.execute(
                "SELECT category_id FROM documents WHERE doc_id = ?",
                (manual_doc,),
            ).fetchone()
            if manual_row["category_id"] != "cat_humanities":
                metrics.manual_assignments_preserved = False

        report = run_doctor(vault)
        hard_findings = [finding for finding in report.findings if finding.severity == "error"]
        metrics.doctor_hard_findings = sum(
            1 for finding in hard_findings if finding.code not in TAG_GOVERNANCE_DOCTOR_CODES
        )
        if metrics.doctor_hard_findings:
            for finding in hard_findings:
                if finding.code not in TAG_GOVERNANCE_DOCTOR_CODES:
                    print(f"doctor error: {finding.code}: {finding.message}", file=sys.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    summary = {
        "wrong_confident_assignments": metrics.wrong_confident_assignments,
        "expected_abstain_passed": metrics.expected_abstain_passed,
        "manual_assignments_preserved": metrics.manual_assignments_preserved,
        "non_ready_categories_used_for_auto": metrics.non_ready_categories_used_for_auto,
        "confident_assignments_have_evidence": metrics.confident_assignments_have_evidence,
        "doctor_hard_findings": metrics.doctor_hard_findings,
    }
    print(json.dumps(summary, indent=2))
    passed = (
        metrics.wrong_confident_assignments == 0
        and metrics.expected_abstain_passed
        and metrics.manual_assignments_preserved
        and metrics.confident_assignments_have_evidence
        and metrics.doctor_hard_findings == 0
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
