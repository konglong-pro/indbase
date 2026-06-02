from pathlib import Path

import pytest
from typer.testing import CliRunner

from indbase_cli.main import app
from indbase_core.category_manager import suggest_category_assignments
from indbase_core.db import connect
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.profile import build_document_profile
from indbase_core.tag_candidates import list_tag_candidates, promote_tag_candidate
from indbase_core.tags import add_tag
from indbase_core.taxonomy_janitor import run_taxonomy_audit
from indbase_core.taxonomy_suggestions import accept_taxonomy_suggestion, list_taxonomy_suggestions
from indbase_core.vault import init_vault


def test_classify_suggest_requires_profile(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "note.md"
    source.write_text("# Note\nsome content\n", encoding="utf-8")
    init_vault(vault, category_template="indbase_default_v1")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        with pytest.raises(ValueError, match="profile build"):
            suggest_category_assignments(connection, doc_id=doc_id)


def test_category_suggest_and_accept_writes_provenance(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "ai-note.md"
    source.write_text(
        "# AI Research\n\nAI research uses LLM RAG and SQLite FTS database patterns.\n",
        encoding="utf-8",
    )
    init_vault(vault, category_template="indbase_default_v1")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        build_document_profile(connection, doc_id)
        run = suggest_category_assignments(connection, doc_id=doc_id, min_confidence=0.5)
        pending = list_taxonomy_suggestions(connection, suggestion_type="category_assign")
        assert run.suggested_documents == 1
        assert pending
        suggestion_id = pending[0]["suggestion_id"]
        accept_taxonomy_suggestion(connection, suggestion_id)
        document = connection.execute(
            """
            SELECT category_id, category_source, category_suggestion_id, category_updated_by
            FROM documents
            WHERE doc_id = ?
            """,
            (doc_id,),
        ).fetchone()
        fts = connection.execute("SELECT category FROM chunks_fts WHERE doc_id = ?", (doc_id,)).fetchone()

    assert document["category_id"] not in {None, "", "cat_uncategorized"}
    assert document["category_source"] == "accepted_suggestion"
    assert document["category_suggestion_id"] == suggestion_id
    assert document["category_updated_by"] == "taxonomy"
    assert fts is not None


def test_legacy_accept_routes_missing_tags_to_candidates(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "ai-research.md"
    source.write_text("# AI Research\nAI research uses LLM RAG vector database patterns.\n", encoding="utf-8")
    init_vault(vault, category_template="indbase_default_v1")
    run_m3_ingest_pipeline(vault, source)

    from indbase_core.categories import add_category
    from indbase_core.classification import accept_classification_suggestion, suggest_classifications

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_category(connection, "AI Research")
        suggest_classifications(connection, doc_id=connection.execute("SELECT doc_id FROM documents").fetchone()[0])
        suggestion_id = connection.execute(
            "SELECT suggestion_id FROM classification_suggestions"
        ).fetchone()[0]
        accept_classification_suggestion(connection, suggestion_id, apply_category=False)
        candidates = list_tag_candidates(connection, status="pending")
        formal_rag = connection.execute(
            "SELECT COUNT(*) AS count FROM tags WHERE normalized_name = 'rag'"
        ).fetchone()["count"]

    assert formal_rag == 0
    assert any(row["normalized_name"] == "rag" for row in candidates)


def test_promote_tag_candidate_creates_formal_tag(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault, category_template="indbase_default_v1")
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        now = "2026-05-20T00:00:00+00:00"
        connection.execute(
            """
            INSERT INTO tag_candidates(
              candidate_id, name, normalized_name, type,
              evidence_doc_ids_json, evidence_chunk_ids_json,
              occurrence_count, distinct_doc_count, confidence,
              status, created_by, created_at
            )
            VALUES (
              'tagcand_test01', 'hybrid-search', 'hybrid-search', 'method',
              '["doc_test"]', '["chunk_test"]',
              2, 2, 0.8, 'pending', 'test', ?
            )
            """,
            (now,),
        )
        connection.commit()
        result = promote_tag_candidate(connection, "tagcand_test01", tag_type="method")
        tag = connection.execute("SELECT tag_id, type FROM tags WHERE tag_id = ?", (result.tag_id,)).fetchone()
        candidate = connection.execute(
            "SELECT status, promoted_tag_id FROM tag_candidates WHERE candidate_id = 'tagcand_test01'"
        ).fetchone()

    assert tag["type"] == "method"
    assert candidate["status"] == "accepted"
    assert candidate["promoted_tag_id"] == result.tag_id


def test_janitor_audit_does_not_mutate_tags(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault, category_template="indbase_default_v1")
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        before = connection.execute("SELECT COUNT(*) AS count FROM tags").fetchone()["count"]
        report = run_taxonomy_audit(connection)
        after = connection.execute("SELECT COUNT(*) AS count FROM tags").fetchone()["count"]

    assert after == before
    assert report.suggestions_created >= 0
    assert isinstance(report.findings, tuple)


def test_cli_taxonomy_audit_json(tmp_path: Path) -> None:
    runner = CliRunner()
    vault = tmp_path / "vault"
    init_vault(vault, category_template="indbase_default_v1")
    result = runner.invoke(app, ["taxonomy", "audit", "--vault", str(vault), "--json"])
    assert result.exit_code == 0
    assert "findings" in result.output
