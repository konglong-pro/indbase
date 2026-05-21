"""v0.3.3 deterministic retrieval evaluation release gate."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from gate_common import TRUSTED_NEEDLE, configure_v02_vault, doctor_hard_metrics, install_deterministic_swallow_stub, write_gate_summary, ROOT
from indbase_core.db import connect
from indbase_core.documents import set_document_category
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.profile import build_document_profile
from indbase_core.retrieval import retrieve_chunks
from indbase_core.retrieval_evaluation import (
    READINESS_POLICY_VERSION,
    assess_answer_readiness,
    import_eval_cases_from_jsonl,
    run_eval_suite,
    upsert_eval_case,
)
from indbase_core.tags import add_document_tag, add_tag
from indbase_core.vault import init_vault

FIXTURE = ROOT / "tests" / "fixtures" / "v033_retrieval_eval" / "v033_core.jsonl"


def _ingest(vault: Path, path: Path) -> str:
    result = run_m3_ingest_pipeline(vault, path)
    if result.written_revisions < 1:
        raise RuntimeError(f"ingest failed for {path}: {result}")
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        return connection.execute(
            "SELECT doc_id FROM documents WHERE normalized_source_uri LIKE ?",
            (f"%{path.name}",),
        ).fetchone()["doc_id"]


def main() -> None:
    root = ROOT / ".tmp" / f"v033-retrieval-eval-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    root.mkdir(parents=True)
    install_deterministic_swallow_stub()
    vault = root / "vault"
    init_vault(vault, category_template="academic")

    primary = root / "primary.md"
    primary.write_text(
        "# AI Research\n\n"
        + "\n\n".join(
            f"## Section {index}\n\n"
            f"Paragraph {index} covers artificial intelligence LLM RAG retrieval augmented generation "
            f"SQLite FTS hybrid search evidence."
            for index in range(12)
        )
        + f"\n\nNeedle: {TRUSTED_NEEDLE}\n",
        encoding="utf-8",
    )
    configure_v02_vault(vault, swallow_ingest=True, transition_output=False, min_markdown_chars=80)
    doc_primary = _ingest(vault, primary)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_tag(connection, "rag", tag_type="method")
        add_document_tag(connection, doc_primary, "rag")
        set_document_category(connection, doc_primary, "cat_computer_science")
        build_document_profile(connection, doc_primary)
        connection.commit()

        imported = import_eval_cases_from_jsonl(connection, FIXTURE, source="fixture")
        if imported.rejected:
            raise RuntimeError(f"fixture import rejected rows: {imported.errors}")
        upsert_eval_case(
            connection,
            suite="v033_core",
            name="primary doc hit",
            query_text="retrieval hybrid search",
            options={"mode": "hybrid", "top_k": 8},
            expectations={
                "expected_doc_ids": [doc_primary],
                "min_result_count": 2,
                "expected_readiness": "ready",
            },
            notes="Gate doc binding check.",
            source="fixture",
        )
        connection.commit()

        eval_result = run_eval_suite(connection, suite="v033_core")
        sparse = retrieve_chunks(connection, "retrieval hybrid search", top_k=1, per_doc_limit=1, mode="hybrid")
        sparse_ready = assess_answer_readiness(connection, sparse.retrieval_run_id)
        rich = retrieve_chunks(connection, "retrieval hybrid search", top_k=8, per_doc_limit=3, mode="hybrid")
        rich_ready = assess_answer_readiness(connection, rich.retrieval_run_id)
        failed_run = connection.execute(
            """
            SELECT retrieval_run_id
            FROM retrieval_runs
            WHERE status = 'failed'
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        failed_ready = (
            assess_answer_readiness(connection, str(failed_run["retrieval_run_id"]))
            if failed_run is not None
            else None
        )
        citations = connection.execute("SELECT COUNT(*) AS count FROM citations").fetchone()["count"]
        feature_atoms = connection.execute("SELECT COUNT(*) AS count FROM feature_atoms").fetchone()["count"]

    doctor = doctor_hard_metrics(vault)
    summary = {
        "fixture_imported": imported.imported + imported.updated,
        "eval_run_status": eval_result.status,
        "passed_count": eval_result.passed_count,
        "failed_count": eval_result.failed_count,
        "error_count": eval_result.error_count,
        "linked_retrieval_runs": all(item.retrieval_run_id for item in eval_result.results if item.status != "error"),
        "ready_report": rich_ready.verdict == "ready",
        "needs_more_evidence_report": sparse_ready.verdict == "needs_more_evidence",
        "not_ready_report": failed_ready is not None and failed_ready.verdict == "not_ready",
        "policy_version": READINESS_POLICY_VERSION,
        "citations_created": int(citations),
        "doctor_hard_findings": doctor["critical_doctor_findings"],
    }
    if summary["citations_created"] != 0:
        raise RuntimeError(f"eval wrote forbidden tables: {summary}")
    if doctor["critical_doctor_findings"]:
        raise RuntimeError(f"doctor hard findings: {doctor}")
    if summary["passed_count"] < 2:
        raise RuntimeError(f"expected passing cases: {summary}")
    if summary["failed_count"] < 1:
        raise RuntimeError(f"expected failing case: {summary}")
    if not summary["ready_report"] or not summary["needs_more_evidence_report"] or not summary["not_ready_report"]:
        raise RuntimeError(f"readiness verdict coverage incomplete: {summary}")
    if int(feature_atoms) < 1:
        raise RuntimeError(f"expected existing feature atoms from profile build: {summary}")

    write_gate_summary(summary, gate_name="V033_RETRIEVAL_EVAL_RELEASE_GATE", gate_root=root)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"V033_RETRIEVAL_EVAL_RELEASE_GATE=failed: {exc}", file=sys.stderr)
        raise
