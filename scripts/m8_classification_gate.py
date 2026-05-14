"""Run the M8.1 classification suggestion gate against a temporary vault."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import indbase_core.normalizers as normalizers
from indbase_core.categories import add_category
from indbase_core.classification import (
    accept_classification_suggestion,
    reject_classification_suggestion,
    suggest_classifications,
)
from indbase_core.db import connect
from indbase_core.doctor import run_doctor
from indbase_core.documents import archive_document, set_document_category
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.search import search_chunks
from indbase_core.vault import init_vault


ROOT = Path.cwd()


def main() -> None:
    root = ROOT / ".tmp" / f"m8-classification-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    root.mkdir(parents=True)
    original_markitdown = normalizers._run_markitdown_file
    try:
        summary = _run_gate(root)
    finally:
        normalizers._run_markitdown_file = original_markitdown

    hard_metrics = {
        "auto_metadata_mutations_before_accept": summary["auto_metadata_mutations_before_accept"],
        "manual_category_overwrites": summary["manual_category_overwrites"],
        "archived_document_suggestions": summary["archived_document_suggestions"],
        "source_shell_suggestions": summary["source_shell_suggestions"],
        "unresolved_classification_reviews_after_decisions": summary[
            "unresolved_classification_reviews_after_decisions"
        ],
        "critical_doctor_findings": summary["critical_doctor_findings"],
    }
    if summary["pending_suggestions_created"] < 3:
        raise RuntimeError(f"M8 did not create enough pending suggestions: {summary}")
    if summary["feedback_records"] < 3:
        raise RuntimeError(f"M8 did not record classification feedback: {summary}")
    if summary["accepted_tags_added"] <= 0:
        raise RuntimeError(f"M8 accept did not add suggested tags: {summary}")
    if any(value != 0 for value in hard_metrics.values()):
        raise RuntimeError(f"M8 hard metrics failed: {hard_metrics}")

    print(json.dumps(summary, sort_keys=True))
    print("M8_CLASSIFICATION_GATE=passed")
    print(f"DOGFOOD_ROOT={root}")


def _run_gate(root: Path) -> dict[str, int]:
    vault = root / "vault"
    init_vault(vault)
    sources = root / "sources"
    sources.mkdir(parents=True)

    accept_source = sources / "accept.md"
    manual_source = sources / "manual.md"
    reject_source = sources / "reject.md"
    archived_source = sources / "archived.md"
    shell_pdf = sources / "scan.pdf"
    accept_source.write_text(
        "# AI Research\nAI research uses LLM RAG vector database and SQLite FTS.\n",
        encoding="utf-8",
    )
    manual_source.write_text(
        "# Manual AI Research\nAI research uses LLM RAG vector database.\n",
        encoding="utf-8",
    )
    reject_source.write_text(
        "# Reject AI Research\nAI research uses LLM RAG vector database.\n",
        encoding="utf-8",
    )
    archived_source.write_text(
        "# Archived AI Research\nAI research uses LLM RAG vector database.\n",
        encoding="utf-8",
    )
    shell_pdf.write_bytes(b"%PDF image only")

    run_m3_ingest_pipeline(vault, accept_source)
    run_m3_ingest_pipeline(vault, manual_source)
    run_m3_ingest_pipeline(vault, reject_source)
    run_m3_ingest_pipeline(vault, archived_source)
    normalizers._run_markitdown_file = lambda _path: " "
    run_m3_ingest_pipeline(vault, shell_pdf)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        suggested_category = add_category(connection, "AI Research")
        manual_category = add_category(connection, "Manual Category")
        accept_doc = _doc_id_for_source(connection, accept_source)
        manual_doc = _doc_id_for_source(connection, manual_source)
        reject_doc = _doc_id_for_source(connection, reject_source)
        archived_doc = _doc_id_for_source(connection, archived_source)
        shell_doc = _doc_id_for_source(connection, shell_pdf)
        set_document_category(connection, manual_doc, manual_category)
        archive_document(connection, archived_doc)

        suggest_classifications(connection, doc_id=accept_doc)
        accept_suggestion = _pending_suggestion(connection, accept_doc)
        before_accept = connection.execute(
            "SELECT category_id FROM documents WHERE doc_id = ?",
            (accept_doc,),
        ).fetchone()["category_id"]
        before_accept_tags = _count(connection, "SELECT COUNT(*) AS count FROM document_tags WHERE doc_id = ?", (accept_doc,))
        accept_result = accept_classification_suggestion(connection, accept_suggestion, reason="gate accept")
        after_accept = connection.execute(
            "SELECT category_id FROM documents WHERE doc_id = ?",
            (accept_doc,),
        ).fetchone()["category_id"]

        suggest_classifications(connection, doc_id=manual_doc)
        manual_suggestion = _pending_suggestion(connection, manual_doc)
        accept_classification_suggestion(connection, manual_suggestion, reason="gate manual preserve")
        manual_after = connection.execute(
            "SELECT category_id FROM documents WHERE doc_id = ?",
            (manual_doc,),
        ).fetchone()["category_id"]

        suggest_classifications(connection, doc_id=reject_doc)
        reject_suggestion = _pending_suggestion(connection, reject_doc)
        reject_classification_suggestion(connection, reject_suggestion, reason="gate reject")
        reject_after = connection.execute(
            "SELECT category_id FROM documents WHERE doc_id = ?",
            (reject_doc,),
        ).fetchone()["category_id"]

        archived_run = suggest_classifications(connection, doc_id=archived_doc)
        shell_run = suggest_classifications(connection, doc_id=shell_doc)

        category_search = search_chunks(connection, "AI Research")
        tag_search = search_chunks(connection, "rag")
        doctor = run_doctor(vault)
        critical_doctor_findings = [
            finding
            for finding in doctor.findings
            if finding.severity in {"error", "critical"}
        ]
        summary = {
            "pending_suggestions_created": _count(
                connection,
                "SELECT COUNT(*) AS count FROM classification_suggestions WHERE status IN ('accepted', 'rejected')",
            ),
            "accepted_suggestions": _count(
                connection,
                "SELECT COUNT(*) AS count FROM classification_suggestions WHERE status = 'accepted'",
            ),
            "rejected_suggestions": _count(
                connection,
                "SELECT COUNT(*) AS count FROM classification_suggestions WHERE status = 'rejected'",
            ),
            "feedback_records": _count(connection, "SELECT COUNT(*) AS count FROM classification_feedback"),
            "review_items_created": _count(
                connection,
                "SELECT COUNT(*) AS count FROM review_items WHERE type = 'classification_suggestion'",
            ),
            "resolved_classification_reviews": _count(
                connection,
                """
                SELECT COUNT(*) AS count
                FROM review_items
                WHERE type = 'classification_suggestion'
                  AND status = 'resolved'
                """,
            ),
            "unresolved_classification_reviews_after_decisions": _count(
                connection,
                """
                SELECT COUNT(*) AS count
                FROM review_items
                WHERE type = 'classification_suggestion'
                  AND status = 'pending'
                """,
            ),
            "auto_metadata_mutations_before_accept": int(
                before_accept != "cat_uncategorized" or int(before_accept_tags) != 0
            ),
            "manual_category_overwrites": int(manual_after != manual_category),
            "reject_metadata_mutations": int(reject_after != "cat_uncategorized"),
            "accepted_category_applied": int(after_accept == suggested_category),
            "accepted_tags_added": len(accept_result.tags_added),
            "classification_feedback_for_accept": _count(
                connection,
                "SELECT COUNT(*) AS count FROM classification_feedback WHERE doc_id = ?",
                (accept_doc,),
            ),
            "classification_fts_category_results": category_search.result_count,
            "classification_fts_tag_results": tag_search.result_count,
            "archived_document_suggestions": archived_run.suggested_documents,
            "source_shell_suggestions": shell_run.suggested_documents,
            "critical_doctor_findings": len(critical_doctor_findings),
        }

    if summary["accepted_category_applied"] != 1:
        raise RuntimeError(f"M8 accept did not apply category explicitly: {summary}")
    if summary["reject_metadata_mutations"] != 0:
        raise RuntimeError(f"M8 reject mutated metadata: {summary}")
    if summary["classification_fts_category_results"] <= 0 or summary["classification_fts_tag_results"] <= 0:
        raise RuntimeError(f"M8 accept did not refresh FTS metadata: {summary}")
    return summary


def _doc_id_for_source(connection, source: Path) -> str:
    row = connection.execute(
        "SELECT doc_id FROM documents WHERE source_uri = ?",
        (str(source),),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"Document not found for source: {source}")
    return str(row["doc_id"])


def _pending_suggestion(connection, doc_id: str) -> str:
    row = connection.execute(
        """
        SELECT suggestion_id
        FROM classification_suggestions
        WHERE doc_id = ?
          AND status = 'pending'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (doc_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"Pending suggestion not found for {doc_id}")
    return str(row["suggestion_id"])


def _count(connection, sql: str, params: tuple[object, ...] = ()) -> int:
    return int(connection.execute(sql, params).fetchone()["count"] or 0)


if __name__ == "__main__":
    main()
