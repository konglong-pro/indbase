import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from indbase_cli.main import app
from indbase_core.chunker import chunk_current_revision
from indbase_core.db import connect
from indbase_core.documents import archive_document, set_document_category
from indbase_core.indexer import rebuild_fts_index
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.profile import build_document_profile
from indbase_core.retrieval import (
    ExplicitFilter,
    MAX_TAXONOMY_BOOST,
    parse_retrieval_query,
    resolve_explicit_filters,
    retrieve_chunks,
)
from indbase_core.tags import add_document_tag, add_tag
from indbase_core.time import utc_now_iso
from indbase_core.vault import init_vault

RETRIEVE_JSON_KEYS = frozenset(
    {
        "retrieval_run_id",
        "query_text",
        "normalized_query_text",
        "linked_search_query_id",
        "status",
        "warnings",
        "top_k",
        "candidate_k",
        "per_doc_limit",
        "base_mode",
        "result_count",
        "items",
    }
)
RETRIEVE_ITEM_JSON_KEYS = frozenset(
    {
        "retrieval_item_id",
        "rank",
        "doc_id",
        "revision_id",
        "chunk_id",
        "title",
        "source_path",
        "quote",
        "snippet",
        "base_score",
        "taxonomy_score",
        "final_score",
        "match_source",
        "reasons",
    }
)
RETRIEVAL_LIST_JSON_KEYS = frozenset({"runs"})
RETRIEVAL_LIST_RUN_KEYS = frozenset(
    {
        "retrieval_run_id",
        "query_text",
        "normalized_query_text",
        "base_mode",
        "result_count",
        "status",
        "created_at",
    }
)
RETRIEVAL_SHOW_JSON_KEYS = frozenset({"run", "items"})
RETRIEVAL_SHOW_RUN_KEYS = frozenset(
    {
        "retrieval_run_id",
        "query_text",
        "normalized_query_text",
        "planner_version",
        "base_mode",
        "linked_search_query_id",
        "filters_json",
        "planner_json",
        "warnings_json",
        "top_k",
        "candidate_k",
        "per_doc_limit",
        "result_count",
        "status",
        "created_at",
        "finished_at",
    }
)
RETRIEVAL_SHOW_ITEM_KEYS = frozenset(
    {
        "retrieval_item_id",
        "retrieval_run_id",
        "rank",
        "doc_id",
        "revision_id",
        "chunk_id",
        "title",
        "source_path",
        "quote",
        "snippet",
        "base_score",
        "taxonomy_score",
        "final_score",
        "match_source",
        "reasons_json",
        "created_at",
    }
)


def _ingest_index(vault: Path, source: Path) -> str:
    run_m3_ingest_pipeline(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc_id = connection.execute(
            "SELECT doc_id FROM documents WHERE normalized_source_uri LIKE ?",
            (f"%{source.name}",),
        ).fetchone()["doc_id"]
        chunk_current_revision(connection, vault, doc_id)
        rebuild_fts_index(connection, vault)
    return doc_id


def test_parse_explicit_filters_and_ambiguity(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault, category_template="minimal")
    parsed = parse_retrieval_query('tag:rag category:工作 hybrid search')
    assert parsed.normalized_query_text == "hybrid search"
    assert len(parsed.explicit_filters) == 2

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_tag(connection, "rag", tag_type="method")
        resolved = resolve_explicit_filters(connection, parsed.explicit_filters[:1])
        assert resolved.tags[0].tag_id.startswith("tag_")
        resolved_cat = resolve_explicit_filters(connection, (parsed.explicit_filters[1],))
        assert resolved_cat.categories[0].category_id == "cat_work"


def test_retrieve_persists_exact_quotes_without_side_effects(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault, category_template="academic")
    source = tmp_path / "ai-note.md"
    source.write_text(
        "# AI Research\n\nAI research uses LLM RAG retrieval augmented generation and SQLite FTS.\n",
        encoding="utf-8",
    )
    doc_id = _ingest_index(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_tag(connection, "rag", tag_type="method")
        add_document_tag(connection, doc_id, "rag")
        build_document_profile(connection, doc_id)
        result = retrieve_chunks(connection, "RAG retrieval", top_k=5, mode="hybrid")
        citations = connection.execute("SELECT COUNT(*) AS count FROM citations").fetchone()["count"]
        search_results = connection.execute("SELECT COUNT(*) AS count FROM search_results").fetchone()["count"]
        chunk_count = connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()["count"]
        for item in result.items:
            row = connection.execute(
                "SELECT text FROM chunks WHERE chunk_id = ?",
                (item.chunk_id,),
            ).fetchone()
            assert item.quote in row["text"]

    assert result.status == "succeeded"
    assert result.result_count >= 1
    assert result.retrieval_run_id.startswith("retrrun_")
    assert result.items[0].retrieval_item_id.startswith("retritem_")
    assert citations == 0
    assert search_results == 0
    assert chunk_count >= 1


def test_explicit_tag_filter_hard_limits_results(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault, category_template="minimal")
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("# A\nshared retrieval keyword alpha\n", encoding="utf-8")
    b.write_text("# B\nshared retrieval keyword beta\n", encoding="utf-8")
    _ingest_index(vault, a)
    doc_b = _ingest_index(vault, b)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_tag(connection, "only-b", tag_type="topic")
        add_document_tag(connection, doc_b, "only-b")
        result = retrieve_chunks(connection, "retrieval tag:only-b", top_k=10, mode="fts")
        assert all(item.doc_id == doc_b for item in result.items)


def test_per_doc_limit_enforced(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "long.md"
    source.write_text(
        "# Long\n" + "\n\n".join(f"retrieval keyword section {index}" for index in range(20)),
        encoding="utf-8",
    )
    _ingest_index(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        result = retrieve_chunks(connection, "retrieval keyword", top_k=10, per_doc_limit=2, mode="fts")
        doc_counts: dict[str, int] = {}
        for item in result.items:
            doc_counts[item.doc_id] = doc_counts.get(item.doc_id, 0) + 1
        assert all(count <= 2 for count in doc_counts.values())


def test_archived_document_excluded(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "archived.md"
    source.write_text("# Archived\nretrieval archived document unique phrase\n", encoding="utf-8")
    doc_id = _ingest_index(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        archive_document(connection, doc_id)
        result = retrieve_chunks(connection, "archived document unique", top_k=5, mode="fts")
        assert all(item.doc_id != doc_id for item in result.items)


def test_cli_retrieve_and_retrieval_show_json(tmp_path: Path) -> None:
    runner = CliRunner()
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "cli.md"
    source.write_text("# CLI\nretrieval cli acceptance phrase\n", encoding="utf-8")
    _ingest_index(vault, source)
    result = runner.invoke(
        app,
        ["retrieve", "cli acceptance", "--vault", str(vault), "--mode", "fts", "--json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert set(payload) == RETRIEVE_JSON_KEYS
    assert payload["items"]
    assert set(payload["items"][0]) == RETRIEVE_ITEM_JSON_KEYS
    run_id = payload["retrieval_run_id"]
    show = runner.invoke(app, ["retrieval", "show", run_id, "--vault", str(vault), "--json"])
    assert show.exit_code == 0
    show_payload = json.loads(show.output)
    assert set(show_payload) == RETRIEVAL_SHOW_JSON_KEYS
    assert set(show_payload["run"]) == RETRIEVAL_SHOW_RUN_KEYS
    assert set(show_payload["items"][0]) == RETRIEVAL_SHOW_ITEM_KEYS
    listed = runner.invoke(app, ["retrieval", "list", "--vault", str(vault), "--json"])
    list_payload = json.loads(listed.output)
    assert set(list_payload) == RETRIEVAL_LIST_JSON_KEYS
    assert set(list_payload["runs"][0]) == RETRIEVAL_LIST_RUN_KEYS


def test_ranking_scores_decompose_and_reasons_are_explainable(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault, category_template="academic")
    source = tmp_path / "ranked.md"
    source.write_text(
        "# Ranked Doc\n\n"
        "Hybrid search retrieval augmented generation uses SQLite FTS for evidence ranking.\n",
        encoding="utf-8",
    )
    doc_id = _ingest_index(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_tag(connection, "rag", tag_type="method")
        add_document_tag(connection, doc_id, "rag")
        set_document_category(connection, doc_id, "cat_computer_science")
        build_document_profile(connection, doc_id)
        first = retrieve_chunks(connection, "hybrid search retrieval", top_k=5, mode="hybrid")
        second = retrieve_chunks(connection, "hybrid search retrieval", top_k=5, mode="hybrid")

    assert first.status == "succeeded" and first.items
    signatures = [
        (item.chunk_id, item.base_score, item.taxonomy_score, item.final_score, item.reasons)
        for item in first.items
    ]
    assert signatures == [
        (item.chunk_id, item.base_score, item.taxonomy_score, item.final_score, item.reasons)
        for item in second.items
    ]

    previous_score = None
    for item in first.items:
        assert item.reasons[0].startswith("base_")
        assert item.final_score == round(item.base_score + item.taxonomy_score, 6)
        assert 0.0 <= item.taxonomy_score <= MAX_TAXONOMY_BOOST
        if previous_score is not None:
            assert item.final_score <= previous_score
        previous_score = item.final_score


def test_filter_only_category_query_fails_without_search_text(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault, category_template="academic")
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        parsed = parse_retrieval_query("category:计算机科学")
        assert parsed.normalized_query_text == ""
        result = retrieve_chunks(connection, "category:计算机科学", top_k=5, mode="fts")
    assert result.status == "failed"
    assert result.result_count == 0
    assert result.items == ()


def test_unassigned_tag_filter_fails_while_baseline_succeeds(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "shared.md"
    source.write_text("# Shared\nshared retrieval keyword for filter contrast\n", encoding="utf-8")
    _ingest_index(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_tag(connection, "lonely", tag_type="topic")
        baseline = retrieve_chunks(connection, "shared retrieval keyword", top_k=5, mode="fts")
        filtered = retrieve_chunks(connection, "shared retrieval keyword tag:lonely", top_k=5, mode="fts")
    assert baseline.status == "succeeded"
    assert baseline.result_count >= 1
    assert filtered.status == "failed"
    assert filtered.result_count == 0


def test_category_filter_with_free_text_respects_hard_filter(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault, category_template="academic")
    cs = tmp_path / "cs.md"
    other = tmp_path / "other.md"
    cs.write_text("# CS\nhybrid search retrieval in computer science lane\n", encoding="utf-8")
    other.write_text("# Other\nhybrid search retrieval in humanities lane\n", encoding="utf-8")
    doc_cs = _ingest_index(vault, cs)
    doc_other = _ingest_index(vault, other)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        set_document_category(connection, doc_cs, "cat_computer_science")
        result = retrieve_chunks(
            connection,
            "category:计算机科学 hybrid search",
            top_k=10,
            mode="fts",
        )
    assert result.status == "succeeded"
    assert result.items
    assert all(item.doc_id == doc_cs for item in result.items)
    assert all(item.doc_id != doc_other for item in result.items)


def test_ambiguous_category_filter_raises_actionable_error(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault, category_template="minimal")
    now = utc_now_iso()
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        connection.execute(
            """
            INSERT INTO categories(
              category_id, name, description, parent_id, sort_order,
              is_active, is_system, created_at, updated_at
            )
            VALUES ('cat_dup_a', '工作', NULL, NULL, 99, 1, 0, ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO categories(
              category_id, name, description, parent_id, sort_order,
              is_active, is_system, created_at, updated_at
            )
            VALUES ('cat_dup_b', '工作', NULL, NULL, 100, 1, 0, ?, ?)
            """,
            (now, now),
        )
        connection.commit()
        with pytest.raises(ValueError, match="Ambiguous category filter"):
            resolve_explicit_filters(connection, (ExplicitFilter(kind="category", raw_value="工作"),))


def test_quote_prefers_query_term_window_over_title_fallback(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "quote.md"
    source.write_text(
        "# Title Only Heading\n\n"
        "Supporting paragraph with needle keyword for quote quality checks.\n",
        encoding="utf-8",
    )
    _ingest_index(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        result = retrieve_chunks(connection, "needle keyword", top_k=3, mode="fts")
    assert result.items
    item = result.items[0]
    assert "needle keyword" in item.quote.casefold()
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        chunk_text = connection.execute(
            "SELECT text FROM chunks WHERE chunk_id = ?",
            (item.chunk_id,),
        ).fetchone()["text"]
    assert item.quote in chunk_text
    assert item.snippet


def test_quote_fallback_still_substring_when_terms_missing(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "fallback.md"
    source.write_text("# Fallback\nzzqxxy constant body text only\n", encoding="utf-8")
    _ingest_index(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        result = retrieve_chunks(connection, "nonexistentqueryterm", top_k=3, mode="fts")
    if result.items:
        item = result.items[0]
        row = connection.execute(
            "SELECT text FROM chunks WHERE chunk_id = ?",
            (item.chunk_id,),
        ).fetchone()
        assert item.quote in row["text"]
        assert item.quote.strip()


def test_cli_retrieve_json_exit_code_reflects_failed_filter_run(tmp_path: Path) -> None:
    runner = CliRunner()
    vault = tmp_path / "vault"
    init_vault(vault, category_template="academic")
    result = runner.invoke(
        app,
        ["retrieve", "category:计算机科学", "--vault", str(vault), "--mode", "fts", "--json"],
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert set(payload) == RETRIEVE_JSON_KEYS
    assert payload["status"] == "failed"
    assert payload["result_count"] == 0
