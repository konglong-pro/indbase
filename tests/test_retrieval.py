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
    parse_retrieval_query,
    resolve_explicit_filters,
    retrieve_chunks,
)
from indbase_core.tags import add_document_tag, add_tag
from indbase_core.vault import init_vault


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
    payload = __import__("json").loads(result.output)
    run_id = payload["retrieval_run_id"]
    show = runner.invoke(app, ["retrieval", "show", run_id, "--vault", str(vault), "--json"])
    assert show.exit_code == 0
    assert run_id in show.output
