"""Run the M8.1 classification hardening gate against a temporary vault."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import indbase_core.normalizers as normalizers
from indbase_core.categories import add_category
from indbase_core.classification import (
    accept_classification_suggestion,
    list_classification_suggestions,
    reject_classification_suggestion,
    suggest_classifications,
)
from indbase_core.db import connect
from indbase_core.documents import archive_document, set_document_category
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.ocr import run_ocr_for_document
from indbase_core.search import search_chunks
from indbase_core.vault import init_vault


ROOT = Path.cwd()


def main() -> None:
    root = ROOT / ".tmp" / f"m81-classification-hardening-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    root.mkdir(parents=True)
    original_markitdown = normalizers._run_markitdown_file
    try:
        summary = _run_gate(root)
    finally:
        normalizers._run_markitdown_file = original_markitdown

    hard_zero = {
        "stale_suggestions_after_reingest": summary["stale_suggestions_after_reingest"],
        "duplicate_pending_suggestions": summary["duplicate_pending_suggestions"],
        "accepted_rejected_suggestions": summary["accepted_rejected_suggestions"],
        "rejected_accepted_suggestions": summary["rejected_accepted_suggestions"],
        "archived_doc_suggestions_default": summary["archived_doc_suggestions_default"],
        "archived_doc_accepts_default": summary["archived_doc_accepts_default"],
        "source_shell_suggestions": summary["source_shell_suggestions"],
        "tag_duplicates_created": summary["tag_duplicates_created"],
        "below_threshold_suggestions_created": summary["below_threshold_suggestions_created"],
        "feedback_missing_revision_id": summary["feedback_missing_revision_id"],
        "unexpected_metadata_mutations": summary["unexpected_metadata_mutations"],
    }
    if any(value != 0 for value in hard_zero.values()):
        raise RuntimeError(f"M8.1 hard zero metrics failed: {hard_zero}")
    if summary["ocr_success_classifiable"] != 1:
        raise RuntimeError(f"OCR-success document was not classifiable: {summary}")
    if summary["force_category_overwrites_recorded"] != 1:
        raise RuntimeError(f"Forced category overwrite was not recorded: {summary}")
    if summary["threshold_equal_suggestions_created"] != 1:
        raise RuntimeError(f"Confidence threshold equality was not accepted: {summary}")
    if summary["reject_fts_pollution"] != 0:
        raise RuntimeError(f"Reject polluted FTS metadata: {summary}")

    print(json.dumps(summary, sort_keys=True))
    print("M81_CLASSIFICATION_HARDENING_GATE=passed")
    print(f"DOGFOOD_ROOT={root}")


def _run_gate(root: Path) -> dict[str, int]:
    summary: dict[str, int] = {}
    summary.update(_reingest_stale_gate(root / "reingest"))
    summary.update(_force_and_terminal_gate(root / "force-terminal"))
    summary.update(_duplicate_gate(root / "duplicate"))
    summary.update(_archived_gate(root / "archived"))
    summary.update(_ocr_shell_gate(root / "ocr-shell"))
    summary.update(_tag_dedupe_gate(root / "tag-dedupe"))
    summary.update(_confidence_gate(root / "confidence"))
    summary.update(_reject_fts_gate(root / "reject-fts"))
    return summary


def _reingest_stale_gate(root: Path) -> dict[str, int]:
    vault, source, doc_id = _vault_with_doc(root, "# AI Research\nAI research uses LLM RAG vector database patterns v1.\n")
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_category(connection, "AI Research")
        suggest_classifications(connection, doc_id=doc_id)
        old = connection.execute("SELECT suggestion_id, revision_id FROM classification_suggestions").fetchone()

    source.write_text("# AI Research\nAI research uses LLM RAG vector database patterns v2 changed.\n", encoding="utf-8")
    run_m3_ingest_pipeline(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        list_classification_suggestions(connection)
        old_status = connection.execute(
            "SELECT status FROM classification_suggestions WHERE suggestion_id = ?",
            (old["suggestion_id"],),
        ).fetchone()["status"]
        stale_visible = _count(
            connection,
            """
            SELECT COUNT(*) AS count
            FROM classification_suggestions cs
            JOIN documents d ON d.doc_id = cs.doc_id
            WHERE cs.status = 'pending'
              AND cs.revision_id != d.current_revision_id
            """,
        )
        suggest_classifications(connection, doc_id=doc_id)
        current_pending = _count(
            connection,
            """
            SELECT COUNT(*) AS count
            FROM classification_suggestions cs
            JOIN documents d ON d.doc_id = cs.doc_id
            WHERE cs.status = 'pending'
              AND cs.revision_id = d.current_revision_id
            """,
        )
    return {
        "stale_suggestions_after_reingest": stale_visible,
        "stale_suggestions_marked": int(old_status == "stale"),
        "new_suggestions_after_reingest": current_pending,
    }


def _force_and_terminal_gate(root: Path) -> dict[str, int]:
    vault, _source, doc_id = _vault_with_doc(root, "# AI Research\nAI research uses LLM RAG vector database patterns.\n")
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        suggested = add_category(connection, "AI Research")
        manual = add_category(connection, "Manual Category")
        set_document_category(connection, doc_id, manual)
        suggest_classifications(connection, doc_id=doc_id)
        preserve_id = _pending_suggestion(connection, doc_id)
        preserve = accept_classification_suggestion(connection, preserve_id)

        accepted_rejected = 0
        try:
            reject_classification_suggestion(connection, preserve_id)
            accepted_rejected = 1
        except ValueError:
            pass

        suggest_classifications(connection, doc_id=doc_id, force=True)
        force_id = _pending_suggestion(connection, doc_id)
        forced = accept_classification_suggestion(connection, force_id, force_category=True)

        suggest_classifications(connection, doc_id=doc_id, force=True)
        reject_id = _pending_suggestion(connection, doc_id)
        reject_classification_suggestion(connection, reject_id)
        rejected_accepted = 0
        try:
            accept_classification_suggestion(connection, reject_id)
            rejected_accepted = 1
        except ValueError:
            pass

        document = connection.execute("SELECT category_id FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
        forced_feedback = _count(
            connection,
            """
            SELECT COUNT(*) AS count
            FROM classification_feedback
            WHERE suggestion_id = ?
              AND revision_id IS NOT NULL
              AND action = 'accepted_forced'
              AND forced_category = 1
              AND old_category_id = ?
              AND new_category_id = ?
            """,
            (force_id, manual, suggested),
        )
        missing_revision = _count(
            connection,
            """
            SELECT COUNT(*) AS count
            FROM classification_feedback
            WHERE revision_id IS NULL
               OR suggestion_id IS NULL
               OR action IS NULL
            """,
        )
    return {
        "accepted_rejected_suggestions": accepted_rejected,
        "rejected_accepted_suggestions": rejected_accepted,
        "force_category_overwrites_recorded": int(
            preserve.category_changed is False
            and forced.category_changed is True
            and document["category_id"] == suggested
            and forced_feedback == 1
        ),
        "feedback_missing_revision_id": missing_revision,
    }


def _duplicate_gate(root: Path) -> dict[str, int]:
    vault, _source, doc_id = _vault_with_doc(root, "# AI Research\nAI research uses LLM RAG vector database patterns.\n")
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_category(connection, "AI Research")
        suggest_classifications(connection, doc_id=doc_id)
        suggest_classifications(connection, doc_id=doc_id)
        duplicates = _count(
            connection,
            """
            SELECT COUNT(*) AS count
            FROM (
              SELECT doc_id, revision_id, model, prompt_version, COUNT(*) AS pending_count
              FROM classification_suggestions
              WHERE status = 'pending'
              GROUP BY doc_id, revision_id, model, prompt_version
              HAVING pending_count > 1
            )
            """,
        )
    return {"duplicate_pending_suggestions": duplicates}


def _archived_gate(root: Path) -> dict[str, int]:
    vault, _source, doc_id = _vault_with_doc(root, "# AI Research\nAI research uses LLM RAG vector database patterns.\n")
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_category(connection, "AI Research")
        suggest_classifications(connection, doc_id=doc_id)
        suggestion_id = _pending_suggestion(connection, doc_id)
        archive_document(connection, doc_id)
        visible = len(list_classification_suggestions(connection))
        accepted = 0
        try:
            accept_classification_suggestion(connection, suggestion_id)
            accepted = 1
        except ValueError:
            pass
    return {
        "archived_doc_suggestions_default": visible,
        "archived_doc_accepts_default": accepted,
    }


def _ocr_shell_gate(root: Path) -> dict[str, int]:
    vault = root / "vault"
    source = root / "scan.pdf"
    root.mkdir(parents=True)
    source.write_bytes(b"%PDF image only")
    init_vault(vault)
    normalizers._run_markitdown_file = lambda _path: " "
    run_m3_ingest_pipeline(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_category(connection, "AI Research")
        row = connection.execute("SELECT doc_id, original_path FROM documents").fetchone()
        shell = suggest_classifications(connection, doc_id=row["doc_id"])
        (vault / row["original_path"]).with_name("original.pdf.ocr.txt").write_text(
            "AI research uses LLM RAG vector database patterns.",
            encoding="utf-8",
        )
        ocr = run_ocr_for_document(connection, vault, row["doc_id"])
        after_ocr = suggest_classifications(connection, doc_id=row["doc_id"])
    return {
        "source_shell_suggestions": shell.suggested_documents,
        "ocr_success_classifiable": int(ocr.status == "succeeded" and after_ocr.suggested_documents == 1),
    }


def _tag_dedupe_gate(root: Path) -> dict[str, int]:
    vault, _source, doc_id = _vault_with_doc(root, "# AI Research\nAI research uses LLM RAG vector database patterns.\n")
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_category(connection, "AI Research")
        suggest_classifications(connection, doc_id=doc_id)
        suggestion_id = _pending_suggestion(connection, doc_id)
        connection.execute(
            "UPDATE classification_suggestions SET suggested_tags_json = ? WHERE suggestion_id = ?",
            (json.dumps(["LLM", "llm", "large language model"]), suggestion_id),
        )
        accept_classification_suggestion(connection, suggestion_id)
        duplicates = _count(
            connection,
            """
            SELECT COUNT(*) AS count
            FROM (
              SELECT normalized_name, COUNT(*) AS tag_count
              FROM tags
              GROUP BY normalized_name
              HAVING tag_count > 1
            )
            """,
        )
        doc_duplicates = _count(
            connection,
            """
            SELECT COUNT(*) AS count
            FROM (
              SELECT doc_id, tag_id, COUNT(*) AS link_count
              FROM document_tags
              WHERE deleted_at IS NULL
              GROUP BY doc_id, tag_id
              HAVING link_count > 1
            )
            """,
        )
    return {"tag_duplicates_created": duplicates + doc_duplicates}


def _confidence_gate(root: Path) -> dict[str, int]:
    vault, _source, _doc_id = _vault_with_doc(root, "# Threshold\nrag\n")
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        below = suggest_classifications(connection, min_confidence=0.701)
        equal = suggest_classifications(connection, min_confidence=0.7)
    return {
        "below_threshold_suggestions_created": below.suggested_documents,
        "threshold_equal_suggestions_created": equal.suggested_documents,
    }


def _reject_fts_gate(root: Path) -> dict[str, int]:
    vault, _source, doc_id = _vault_with_doc(root, "# AI Research\nAI research uses LLM RAG vector database patterns.\n")
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        category = add_category(connection, "RejectOnlyCategory")
        suggest_classifications(connection, doc_id=doc_id)
        suggestion_id = _pending_suggestion(connection, doc_id)
        before_category = connection.execute("SELECT category_id FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()[
            "category_id"
        ]
        connection.execute(
            """
            UPDATE classification_suggestions
            SET suggested_category_id = ?, suggested_tags_json = ?
            WHERE suggestion_id = ?
            """,
            (category, json.dumps(["reject-only-tag"]), suggestion_id),
        )
        reject_classification_suggestion(connection, suggestion_id)
        after_category = connection.execute("SELECT category_id FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()[
            "category_id"
        ]
        category_search = search_chunks(connection, "RejectOnlyCategory")
        tag_search = search_chunks(connection, "reject-only-tag")
    return {
        "reject_fts_pollution": category_search.result_count + tag_search.result_count,
        "unexpected_metadata_mutations": int(after_category != before_category),
    }


def _vault_with_doc(root: Path, text: str) -> tuple[Path, Path, str]:
    vault = root / "vault"
    source = root / "doc.md"
    root.mkdir(parents=True)
    source.write_text(text, encoding="utf-8")
    init_vault(vault)
    run_m3_ingest_pipeline(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
    return vault, source, str(doc_id)


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
