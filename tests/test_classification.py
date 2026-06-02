import json

import pytest
from typer.testing import CliRunner

from indbase_cli.main import app
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
from indbase_core.tags import add_tag, list_document_tags
from indbase_core.vault import init_vault


def test_classification_suggest_creates_review_without_mutating_metadata(tmp_path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "ai-research.md"
    source.write_text("# AI Research\nAI research uses LLM RAG vector database patterns.\n", encoding="utf-8")
    init_vault(vault, category_template="indbase_default_v1")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        result = suggest_classifications(connection)
        suggestion = connection.execute("SELECT * FROM classification_suggestions").fetchone()
        review = connection.execute(
            "SELECT type, target_type, target_id, status FROM review_items WHERE type = 'classification_suggestion'"
        ).fetchone()
        document = connection.execute(
            "SELECT category_id, classification_status FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        tag_count = connection.execute("SELECT COUNT(*) AS count FROM document_tags").fetchone()["count"]

    assert result.scanned_documents == 1
    assert result.suggested_documents == 1
    assert result.review_items == 1
    assert suggestion["doc_id"] == doc_id
    assert suggestion["suggested_category_id"] == "cat_computer_science"
    assert suggestion["needs_user_confirmation"] == 1
    assert suggestion["status"] == "pending"
    assert "rag" in json.loads(suggestion["suggested_tags_json"])
    assert review["target_id"] == suggestion["suggestion_id"]
    assert review["status"] == "pending"
    assert document["category_id"] == "cat_uncategorized"
    assert document["classification_status"] == "suggested"
    assert tag_count == 0


def test_classification_accept_applies_explicit_metadata_and_records_feedback(tmp_path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "ai-research.md"
    source.write_text("# AI Research\nAI research uses LLM RAG vector database patterns.\n", encoding="utf-8")
    init_vault(vault, category_template="indbase_default_v1")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        category_id = "cat_computer_science"
        suggest_classifications(connection)
        suggestion_id = connection.execute("SELECT suggestion_id FROM classification_suggestions").fetchone()[0]
        add_tag(connection, "rag", tag_type="method")
        result = accept_classification_suggestion(connection, suggestion_id, reason="accepted in test")
        doc_id = result.doc_id
        document = connection.execute(
            "SELECT category_id, classification_status FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        tags = [row["name"] for row in list_document_tags(connection, doc_id)]
        feedback = connection.execute("SELECT * FROM classification_feedback").fetchone()
        suggestion = connection.execute(
            "SELECT revision_id, status, needs_user_confirmation FROM classification_suggestions WHERE suggestion_id = ?",
            (suggestion_id,),
        ).fetchone()
        review = connection.execute(
            "SELECT status, resolved_by FROM review_items WHERE target_id = ?",
            (suggestion_id,),
        ).fetchone()
        fts_row = connection.execute("SELECT tags, category FROM chunks_fts WHERE doc_id = ?", (doc_id,)).fetchone()

    assert result.category_changed is True
    assert "rag" in result.tags_added
    assert document["category_id"] == category_id
    assert document["classification_status"] == "accepted"
    assert "rag" in tags
    assert feedback["doc_id"] == doc_id
    assert feedback["suggestion_id"] == suggestion_id
    assert feedback["revision_id"] == suggestion["revision_id"]
    assert feedback["action"] == "accepted"
    assert feedback["new_category_id"] == category_id
    assert feedback["forced_category"] == 0
    assert suggestion["status"] == "accepted"
    assert suggestion["needs_user_confirmation"] == 0
    assert review["status"] == "resolved"
    assert review["resolved_by"] == "classification"
    assert "rag" in fts_row["tags"]
    assert "computer science" in fts_row["category"].casefold()


def test_classification_accept_preserves_existing_manual_category_by_default(tmp_path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "ai-research.md"
    source.write_text("# AI Research\nAI research uses LLM RAG vector database patterns.\n", encoding="utf-8")
    init_vault(vault, category_template="indbase_default_v1")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        suggested_category_id = "cat_computer_science"
        manual_category_id = add_category(connection, "Manual Category")
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        set_document_category(connection, doc_id, manual_category_id)
        suggest_classifications(connection)
        suggestion_id = connection.execute("SELECT suggestion_id FROM classification_suggestions").fetchone()[0]
        result = accept_classification_suggestion(connection, suggestion_id)
        document = connection.execute("SELECT category_id FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
        feedback = connection.execute("SELECT old_category_id, new_category_id FROM classification_feedback").fetchone()

    assert suggested_category_id != manual_category_id
    assert result.category_changed is False
    assert document["category_id"] == manual_category_id
    assert feedback["old_category_id"] == manual_category_id
    assert feedback["new_category_id"] == manual_category_id


def test_classification_reject_records_feedback_without_metadata_changes(tmp_path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "ai-research.md"
    source.write_text("# AI Research\nAI research uses LLM RAG vector database patterns.\n", encoding="utf-8")
    init_vault(vault, category_template="indbase_default_v1")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_category(connection, "AI Research")
        suggest_classifications(connection)
        suggestion_id = connection.execute("SELECT suggestion_id FROM classification_suggestions").fetchone()[0]
        result = reject_classification_suggestion(connection, suggestion_id, reason="not useful")
        document = connection.execute(
            "SELECT category_id, classification_status FROM documents WHERE doc_id = ?",
            (result.doc_id,),
        ).fetchone()
        feedback = connection.execute("SELECT reason FROM classification_feedback").fetchone()
        pending = list_classification_suggestions(connection)

    assert result.status == "rejected"
    assert result.category_changed is False
    assert result.tags_added == ()
    assert document["category_id"] == "cat_uncategorized"
    assert document["classification_status"] == "rejected"
    assert feedback["reason"] == "not useful"
    assert pending == []


def test_cli_classification_suggest_list_accept_json(tmp_path) -> None:
    runner = CliRunner()
    vault = tmp_path / "vault"
    source = tmp_path / "ai-research.md"
    source.write_text("# AI Research\nAI research uses LLM RAG vector database patterns.\n", encoding="utf-8")

    assert runner.invoke(app, ["init", str(vault)]).exit_code == 0
    assert runner.invoke(app, ["catalog", "add", "AI Research", "--vault", str(vault)]).exit_code == 0
    assert runner.invoke(app, ["ingest", str(source), "--vault", str(vault)]).exit_code == 0

    suggest = runner.invoke(app, ["classify", "suggest", "--vault", str(vault), "--legacy", "--json"])
    listed = runner.invoke(app, ["classify", "list", "--vault", str(vault), "--legacy", "--json"])
    suggestion_id = json.loads(listed.output)["suggestions"][0]["suggestion_id"]
    accepted = runner.invoke(
        app,
        ["classify", "accept", suggestion_id, "--vault", str(vault), "--legacy", "--json"],
    )

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        feedback_count = connection.execute("SELECT COUNT(*) AS count FROM classification_feedback").fetchone()["count"]

    assert suggest.exit_code == 0
    assert json.loads(suggest.output)["suggested_documents"] == 1
    assert listed.exit_code == 0
    assert json.loads(listed.output)["suggestions"][0]["status"] == "pending"
    assert accepted.exit_code == 0
    assert json.loads(accepted.output)["status"] == "accepted"
    assert feedback_count == 1


def test_classification_stales_old_suggestion_after_changed_reingest(tmp_path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "ai-research.md"
    source.write_text("# AI Research\nAI research uses LLM RAG vector database patterns v1.\n", encoding="utf-8")
    init_vault(vault, category_template="indbase_default_v1")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_category(connection, "AI Research")
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        suggest_classifications(connection, doc_id=doc_id)
        v1 = connection.execute("SELECT suggestion_id, revision_id FROM classification_suggestions").fetchone()

    source.write_text("# AI Research\nAI research uses LLM RAG vector database patterns v2 changed.\n", encoding="utf-8")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        pending = list_classification_suggestions(connection)
        stale = connection.execute(
            "SELECT status FROM classification_suggestions WHERE suggestion_id = ?",
            (v1["suggestion_id"],),
        ).fetchone()
        result = suggest_classifications(connection, doc_id=doc_id)
        rows = connection.execute(
            "SELECT revision_id, status FROM classification_suggestions ORDER BY created_at"
        ).fetchall()

    assert pending == []
    assert stale["status"] == "stale"
    assert result.suggested_documents == 1
    assert rows[0]["revision_id"] == v1["revision_id"]
    assert rows[0]["status"] == "stale"
    assert rows[1]["status"] == "pending"


def test_classification_force_category_overwrites_and_records_feedback(tmp_path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "ai-research.md"
    source.write_text("# AI Research\nAI research uses LLM RAG vector database patterns.\n", encoding="utf-8")
    init_vault(vault, category_template="indbase_default_v1")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        suggested_category_id = "cat_computer_science"
        manual_category_id = add_category(connection, "Manual Category")
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        set_document_category(connection, doc_id, manual_category_id)
        suggest_classifications(connection, doc_id=doc_id)
        first_suggestion = connection.execute("SELECT suggestion_id FROM classification_suggestions").fetchone()[0]
        preserve = accept_classification_suggestion(connection, first_suggestion)
        suggest_classifications(connection, doc_id=doc_id, force=True)
        forced_suggestion = connection.execute(
            """
            SELECT suggestion_id
            FROM classification_suggestions
            WHERE status = 'pending'
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()[0]
        forced = accept_classification_suggestion(connection, forced_suggestion, force_category=True)
        document = connection.execute("SELECT category_id FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
        feedback = connection.execute(
            """
            SELECT action, old_category_id, new_category_id, forced_category
            FROM classification_feedback
            WHERE suggestion_id = ?
            """,
            (forced_suggestion,),
        ).fetchone()

    assert preserve.category_changed is False
    assert forced.category_changed is True
    assert document["category_id"] == suggested_category_id
    assert feedback["action"] == "accepted_forced"
    assert feedback["old_category_id"] == manual_category_id
    assert feedback["new_category_id"] == suggested_category_id
    assert feedback["forced_category"] == 1


def test_classification_does_not_create_duplicate_pending_suggestions(tmp_path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "ai-research.md"
    source.write_text("# AI Research\nAI research uses LLM RAG vector database patterns.\n", encoding="utf-8")
    init_vault(vault, category_template="indbase_default_v1")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_category(connection, "AI Research")
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        first = suggest_classifications(connection, doc_id=doc_id)
        second = suggest_classifications(connection, doc_id=doc_id)
        duplicates = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM (
              SELECT doc_id, revision_id, COUNT(*) AS pending_count
              FROM classification_suggestions
              WHERE status = 'pending'
              GROUP BY doc_id, revision_id, model, prompt_version
              HAVING pending_count > 1
            )
            """
        ).fetchone()["count"]

    assert first.suggested_documents == 1
    assert second.suggested_documents == 0
    assert second.skipped_documents == 1
    assert duplicates == 0


def test_classification_pending_suggestion_on_archived_doc_is_hidden_and_not_accepted(tmp_path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "ai-research.md"
    source.write_text("# AI Research\nAI research uses LLM RAG vector database patterns.\n", encoding="utf-8")
    init_vault(vault, category_template="indbase_default_v1")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_category(connection, "AI Research")
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        suggest_classifications(connection, doc_id=doc_id)
        suggestion_id = connection.execute("SELECT suggestion_id FROM classification_suggestions").fetchone()[0]
        archive_document(connection, doc_id)
        default_list = list_classification_suggestions(connection)
        all_list = list_classification_suggestions(connection, status=None, active_current_only=False)
        with pytest.raises(ValueError, match="Document is not active"):
            accept_classification_suggestion(connection, suggestion_id)

    assert default_list == []
    assert len(all_list) == 1
    assert all_list[0]["status"] == "pending"


def test_classification_allows_pdf_shell_after_ocr_success(tmp_path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF image only")
    init_vault(vault, category_template="indbase_default_v1")
    monkeypatch.setattr(normalizers, "_run_markitdown_file", lambda _path: " ")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_category(connection, "AI Research")
        row = connection.execute("SELECT doc_id, original_path FROM documents").fetchone()
        shell_result = suggest_classifications(connection, doc_id=row["doc_id"])
        (vault / row["original_path"]).with_name("original.pdf.ocr.txt").write_text(
            "AI research uses LLM RAG vector database patterns.",
            encoding="utf-8",
        )
        ocr = run_ocr_for_document(connection, vault, row["doc_id"])
        ocr_result = suggest_classifications(connection, doc_id=row["doc_id"])
        suggestion = connection.execute("SELECT status FROM classification_suggestions").fetchone()

    assert shell_result.scanned_documents == 0
    assert shell_result.suggested_documents == 0
    assert ocr.status == "succeeded"
    assert ocr_result.suggested_documents == 1
    assert suggestion["status"] == "pending"


def test_classification_terminal_suggestions_cannot_be_reused(tmp_path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "ai-research.md"
    source.write_text("# AI Research\nAI research uses LLM RAG vector database patterns.\n", encoding="utf-8")
    init_vault(vault, category_template="indbase_default_v1")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_category(connection, "AI Research")
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        suggest_classifications(connection, doc_id=doc_id)
        rejected_id = connection.execute("SELECT suggestion_id FROM classification_suggestions").fetchone()[0]
        reject_classification_suggestion(connection, rejected_id)
        with pytest.raises(ValueError, match="not pending"):
            accept_classification_suggestion(connection, rejected_id)
        suggest_classifications(connection, doc_id=doc_id, force=True)
        accepted_id = connection.execute(
            "SELECT suggestion_id FROM classification_suggestions WHERE status = 'pending' LIMIT 1"
        ).fetchone()[0]
        accept_classification_suggestion(connection, accepted_id)
        with pytest.raises(ValueError, match="not pending"):
            accept_classification_suggestion(connection, accepted_id)
        feedback_count = connection.execute("SELECT COUNT(*) AS count FROM classification_feedback").fetchone()["count"]

    assert feedback_count == 2


def test_classification_accept_deduplicates_normalized_tags(tmp_path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "ai-research.md"
    source.write_text("# AI Research\nAI research uses LLM RAG vector database patterns.\n", encoding="utf-8")
    init_vault(vault, category_template="indbase_default_v1")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_category(connection, "AI Research")
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        suggest_classifications(connection, doc_id=doc_id)
        suggestion_id = connection.execute("SELECT suggestion_id FROM classification_suggestions").fetchone()[0]
        connection.execute(
            "UPDATE classification_suggestions SET suggested_tags_json = ? WHERE suggestion_id = ?",
            (json.dumps(["LLM", "llm", "large language model"]), suggestion_id),
        )
        add_tag(connection, "LLM", tag_type="topic")
        add_tag(connection, "large language model", tag_type="topic")
        result = accept_classification_suggestion(connection, suggestion_id)
        llm_tags = connection.execute(
            "SELECT COUNT(*) AS count FROM tags WHERE normalized_name = 'llm'"
        ).fetchone()["count"]
        llm_doc_tags = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM document_tags dt
            JOIN tags t ON t.tag_id = dt.tag_id
            WHERE dt.doc_id = ?
              AND t.normalized_name = 'llm'
              AND dt.deleted_at IS NULL
            """,
            (doc_id,),
        ).fetchone()["count"]

    assert result.tags_added == ("LLM", "large language model")
    assert llm_tags == 1
    assert llm_doc_tags == 1


def test_classification_confidence_gate_uses_greater_than_or_equal_threshold(tmp_path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "rag.md"
    source.write_text("# Threshold\nrag\n", encoding="utf-8")
    init_vault(vault, category_template="indbase_default_v1")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        above_threshold = suggest_classifications(connection, min_confidence=0.701)
        at_threshold = suggest_classifications(connection, min_confidence=0.7)
        suggestion = connection.execute("SELECT confidence FROM classification_suggestions").fetchone()

    assert above_threshold.suggested_documents == 0
    assert at_threshold.suggested_documents == 1
    assert suggestion["confidence"] == 0.7


def test_classification_reject_does_not_pollute_fts_metadata(tmp_path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "ai-research.md"
    source.write_text("# AI Research\nAI research uses LLM RAG vector database patterns.\n", encoding="utf-8")
    init_vault(vault, category_template="indbase_default_v1")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        category_id = add_category(connection, "RejectOnlyCategory")
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        suggest_classifications(connection, doc_id=doc_id)
        suggestion_id = connection.execute("SELECT suggestion_id FROM classification_suggestions").fetchone()[0]
        connection.execute(
            """
            UPDATE classification_suggestions
            SET suggested_category_id = ?, suggested_tags_json = ?
            WHERE suggestion_id = ?
            """,
            (category_id, json.dumps(["reject-only-tag"]), suggestion_id),
        )
        reject_classification_suggestion(connection, suggestion_id)
        category_search = search_chunks(connection, "RejectOnlyCategory")
        tag_search = search_chunks(connection, "reject-only-tag")

    assert category_search.result_count == 0
    assert tag_search.result_count == 0
