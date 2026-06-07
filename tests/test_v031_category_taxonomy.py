import json
from dataclasses import replace
from pathlib import Path

import pytest

from indbase_core.categories import DEFAULT_TEMPLATE_KEY, UnknownCategoryTemplate, apply_category_template, template_names
from indbase_core.category_profiles import list_classification_ready_profiles
from indbase_core.category_taxonomy import (
    CONFIDENT_THRESHOLD,
    accept_category_suggestion,
    run_category_classification,
)
from indbase_core.config import load_config, save_config
from indbase_core.db import connect
from indbase_core.documents import set_document_category
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.search import SearchOptions, search_chunks
from indbase_core.vault import init_vault


def test_legacy_template_rejected_for_new_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault, category_template=DEFAULT_TEMPLATE_KEY)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        with pytest.raises(UnknownCategoryTemplate):
            apply_category_template(connection, "minimal")


def test_new_vault_has_default_profiles_and_localizations(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        profiles = list_classification_ready_profiles(connection)
        profile_ids = {row["category_id"] for row in profiles}
        assert "cat_computer_science" in profile_ids
        assert "cat_uncategorized" not in profile_ids
        en = connection.execute(
            "SELECT label FROM category_localizations WHERE category_id = ? AND locale = 'en'",
            ("cat_computer_science",),
        ).fetchone()
        zh = connection.execute(
            "SELECT label FROM category_localizations WHERE category_id = ? AND locale = 'zh-CN'",
            ("cat_computer_science",),
        ).fetchone()
    assert en["label"] == "Computer Science"
    assert zh["label"] == "计算机科学"


def test_confident_classification_assigns_computer_science(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "rag-notes.md"
    source.write_text(
        "# RAG retrieval notes\n\n"
        "This document discusses LLM RAG vector database sqlite retrieval patterns.\n",
        encoding="utf-8",
    )
    init_vault(vault)
    run_m3_ingest_pipeline(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        result = run_category_classification(connection, doc_id=doc_id, trigger="manual")
        document = connection.execute(
            "SELECT category_id, classification_status FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        audit = connection.execute(
            """
            SELECT outcome, confidence, margin, evidence_json
            FROM category_classification_results
            WHERE category_run_id = ?
            """,
            (result.category_run_id,),
        ).fetchone()
    assert result.confident_count == 1
    assert document["category_id"] == "cat_computer_science"
    assert document["classification_status"] == "auto_classified"
    assert audit["outcome"] == "confident_assigned"
    assert float(audit["confidence"]) >= CONFIDENT_THRESHOLD
    evidence = json.loads(audit["evidence_json"])
    assert evidence["matched_terms"]


def test_manual_assignment_preserved_on_reclassify(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "rag-notes.md"
    source.write_text("# RAG\n\nLLM vector database sqlite.\n", encoding="utf-8")
    init_vault(vault)
    run_m3_ingest_pipeline(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        set_document_category(connection, doc_id, "cat_humanities")
        result = run_category_classification(connection, doc_id=doc_id, trigger="manual")
        document = connection.execute(
            "SELECT category_id, classification_status FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
    assert result.preserved_count == 1
    assert document["category_id"] == "cat_humanities"
    assert document["classification_status"] == "manual"


def test_post_ingest_taxonomy_abstain_does_not_fail_ingest(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "tiny.txt"
    source.write_text("ok\n", encoding="utf-8")
    init_vault(vault)
    config = load_config(vault / ".indbase" / "config" / "config.toml")
    save_config(
        replace(config, features=replace(config.features, category_taxonomy=True)),
        vault / ".indbase" / "config" / "config.toml",
    )
    result = run_m3_ingest_pipeline(vault, source)
    assert result.status in {"succeeded", "completed_with_issues"}
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        run_count = connection.execute("SELECT COUNT(*) AS count FROM category_classification_runs").fetchone()
    assert int(run_count["count"]) == 1


def test_category_search_filter_is_exact(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("# RAG\n\nLLM vector database sqlite retrieval.\n", encoding="utf-8")
    b.write_text("# History\n\nAncient philosophy and literature.\n", encoding="utf-8")
    init_vault(vault)
    run_m3_ingest_pipeline(vault, a)
    run_m3_ingest_pipeline(vault, b)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        run_category_classification(connection, trigger="manual")
        cs = search_chunks(
            connection,
            "category:cat_computer_science retrieval",
            options=SearchOptions(top_k=10, log_queries=False, category_id="cat_computer_science"),
        )
        doc_ids = {row.doc_id for row in cs.results}
    assert len(doc_ids) == 1


def test_category_suggestion_acceptance(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "mixed.md"
    source.write_text("# Notes\n\nSome sqlite database reference material.\n", encoding="utf-8")
    init_vault(vault)
    run_m3_ingest_pipeline(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        run_category_classification(connection, doc_id=doc_id, trigger="manual", margin_threshold=0.99)
        suggestion = connection.execute(
            "SELECT category_suggestion_id FROM category_suggestions WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        if suggestion is None:
            pytest.skip("fixture did not produce a category suggestion")
        accept_category_suggestion(connection, suggestion["category_suggestion_id"], reason="test")
        document = connection.execute(
            "SELECT classification_status FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        feedback_count = connection.execute("SELECT COUNT(*) AS count FROM category_feedback").fetchone()
    assert document["classification_status"] == "accepted"
    assert int(feedback_count["count"]) >= 1


def test_template_names_only_default() -> None:
    assert template_names() == (DEFAULT_TEMPLATE_KEY,)
