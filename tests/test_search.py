from pathlib import Path

from indbase_core.chunker import chunk_current_revision
from indbase_core.db import connect
from indbase_core.documents import archive_document
from indbase_core.embeddings import rebuild_vector_index
from indbase_core.indexer import rebuild_fts_index
from indbase_core.ingest import run_m2_ingest_pipeline, run_m3_ingest_pipeline
from indbase_core.ocr import run_ocr_for_document
from indbase_core.search import SearchOptions, build_snippet, search_chunks
from indbase_core.time import utc_now_iso
from indbase_core.vault import init_vault


def _ingest_chunk_and_index(vault: Path, source: Path) -> None:
    run_m2_ingest_pipeline(vault, source)
    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        doc_id = connection.execute(
            "SELECT doc_id FROM documents WHERE normalized_source_uri LIKE ?",
            (f"%/{source.name}",),
        ).fetchone()["doc_id"]
        chunk_current_revision(connection, vault, doc_id)
        rebuild_fts_index(connection, vault)
    finally:
        connection.close()


def test_search_chunks_returns_current_source_snippets_without_persisting_citations(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "guide.md"
    source.write_text("# Guide\nLocal search returns source snippets.\n", encoding="utf-8")
    _ingest_chunk_and_index(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        result = search_chunks(connection, "source snippets")
        search_queries = connection.execute("SELECT COUNT(*) AS count FROM search_queries").fetchone()
        search_results = connection.execute("SELECT COUNT(*) AS count FROM search_results").fetchone()
        citations = connection.execute("SELECT COUNT(*) AS count FROM citations").fetchone()
    finally:
        connection.close()

    assert result.query_id is not None
    assert result.result_count == 1
    assert result.results[0].rank == 1
    assert result.results[0].doc_id.startswith("doc_")
    assert result.results[0].revision_id.startswith("rev_doc_")
    assert result.results[0].chunk_id.startswith("chunk_rev_doc_")
    assert result.results[0].source_path.endswith(".md")
    assert "source snippets" in result.results[0].snippet
    assert result.results[0].match_source == "fts"
    assert search_queries["count"] == 1
    assert search_results["count"] == 0
    assert citations["count"] == 0


def test_search_chunks_can_disable_query_logging(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "guide.md"
    source.write_text("# Guide\nSensitive local search term.\n", encoding="utf-8")
    _ingest_chunk_and_index(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        result = search_chunks(connection, "sensitive", options=SearchOptions(log_queries=False))
        search_queries = connection.execute("SELECT COUNT(*) AS count FROM search_queries").fetchone()
        search_results = connection.execute("SELECT COUNT(*) AS count FROM search_results").fetchone()
        citations = connection.execute("SELECT COUNT(*) AS count FROM citations").fetchone()
    finally:
        connection.close()

    assert result.query_id is None
    assert result.result_count == 1
    assert search_queries["count"] == 0
    assert search_results["count"] == 0
    assert citations["count"] == 0


def test_search_chunks_returns_cjk_source_snippets_with_identifiers(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    chinese = tmp_path / "中文.md"
    japanese = tmp_path / "日本語.md"
    chinese.write_text(
        "# 中文知识\n大语言模型可以进入知识数据库，但必须保留幻觉控制证据。\n",
        encoding="utf-8",
    )
    japanese.write_text(
        "# 日本語ノート\n大規模言語モデルを知識管理に使う場合は根拠を確認する。\n",
        encoding="utf-8",
    )
    _ingest_chunk_and_index(vault, chinese)
    _ingest_chunk_and_index(vault, japanese)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        chinese_model = search_chunks(connection, "大语言模型")
        chinese_hallucination = search_chunks(connection, "幻觉控制")
        japanese_model = search_chunks(connection, "大規模言語モデル")
    finally:
        connection.close()

    for result, expected_text in (
        (chinese_model, "大语言模型"),
        (chinese_hallucination, "幻觉控制"),
        (japanese_model, "大規模言語モデル"),
    ):
        assert result.result_count == 1
        hit = result.results[0]
        assert hit.doc_id.startswith("doc_")
        assert hit.revision_id.startswith("rev_doc_")
        assert hit.chunk_id.startswith("chunk_rev_doc_")
        assert expected_text in hit.snippet


def test_search_chunks_can_persist_search_results_when_enabled(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "guide.md"
    source.write_text("# Guide\nLocal search returns source snippets.\n", encoding="utf-8")
    _ingest_chunk_and_index(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        result = search_chunks(
            connection,
            "local",
            options=SearchOptions(persist_search_results=True),
        )
        persisted = connection.execute(
            "SELECT query_id, rank, doc_id, revision_id, chunk_id, snippet FROM search_results"
        ).fetchone()
        citations = connection.execute("SELECT COUNT(*) AS count FROM citations").fetchone()
    finally:
        connection.close()

    assert result.query_id is not None
    assert persisted["query_id"] == result.query_id
    assert persisted["rank"] == 1
    assert persisted["doc_id"] == result.results[0].doc_id
    assert persisted["revision_id"] == result.results[0].revision_id
    assert persisted["chunk_id"] == result.results[0].chunk_id
    assert "Local search" in persisted["snippet"]
    assert citations["count"] == 0


def test_search_chunks_uses_cjk_substring_fallback_without_fts_rows(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "knowledge.md"
    source.write_text("# 知识库\n这是个人知识数据库。\n", encoding="utf-8")
    run_m2_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        chunk_current_revision(connection, vault, doc_id)

        result = search_chunks(connection, "知识库")
        fts_rows = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()
    finally:
        connection.close()

    assert fts_rows["count"] == 0
    assert result.result_count == 1
    assert result.results[0].match_source == "cjk"
    assert "知识库" in result.results[0].snippet


def test_search_chunks_dedupes_fts_and_cjk_matches(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "knowledge.md"
    source.write_text("# 知识库\n这是个人知识数据库。\n", encoding="utf-8")
    _ingest_chunk_and_index(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        result = search_chunks(connection, "知识库")
    finally:
        connection.close()

    assert result.result_count == 1
    assert result.results[0].match_source == "fts+cjk"


def test_search_chunks_filters_archived_documents_even_when_fts_rows_remain(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "archive-me.md"
    source.write_text("# Archive\nHidden searchable text\n", encoding="utf-8")
    _ingest_chunk_and_index(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        connection.execute(
            "UPDATE documents SET status = 'archived', archived_at = ?, updated_at = ?",
            (utc_now_iso(), utc_now_iso()),
        )
        connection.commit()

        result = search_chunks(connection, "hidden")
        fts_rows = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()
    finally:
        connection.close()

    assert fts_rows["count"] == 1
    assert result.result_count == 0


def test_search_chunks_respects_top_k(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    for index in range(3):
        source = tmp_path / f"note-{index}.md"
        source.write_text(f"# Note {index}\nRepeated needle body {index}.\n", encoding="utf-8")
        _ingest_chunk_and_index(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        result = search_chunks(connection, "needle", options=SearchOptions(top_k=2))
    finally:
        connection.close()

    assert result.result_count == 2
    assert [row.rank for row in result.results] == [1, 2]


def test_vector_search_returns_chunk_bound_snippets(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "vector.md"
    source.write_text("# Vector\nvector search returns snippets.\n", encoding="utf-8")
    _ingest_chunk_and_index(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        rebuild_vector_index(connection)
        result = search_chunks(connection, "semantic-ish", options=SearchOptions(mode="vector"))
    finally:
        connection.close()

    assert result.result_count == 1
    hit = result.results[0]
    assert hit.doc_id.startswith("doc_")
    assert hit.revision_id.startswith("rev_doc_")
    assert hit.chunk_id.startswith("chunk_rev_doc_")
    assert hit.source_path.endswith(".md")
    assert "vector search returns snippets" in hit.snippet
    assert hit.match_source == "vector"


def test_hybrid_search_merges_fts_and_vector_contributions(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "hybrid.md"
    source.write_text("# Hybrid\nhybrid merge needle.\n", encoding="utf-8")
    _ingest_chunk_and_index(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        rebuild_vector_index(connection)
        result = search_chunks(connection, "hybrid merge needle", options=SearchOptions(mode="hybrid"))
        query_row = connection.execute("SELECT mode FROM search_queries WHERE query_id = ?", (result.query_id,)).fetchone()
    finally:
        connection.close()

    assert result.result_count == 1
    assert result.results[0].match_source == "fts+vector"
    assert "hybrid merge needle" in result.results[0].snippet
    assert query_row["mode"] == "hybrid"


def test_vector_search_filters_archived_source_shells_and_old_revisions(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    changing = tmp_path / "changing.md"
    archived = tmp_path / "archived.md"
    shell_pdf = tmp_path / "scan.pdf"
    changing.write_text("# Changing\nold vector stale content\n", encoding="utf-8")
    archived.write_text("# Archived\narchived vector content\n", encoding="utf-8")
    run_m3_ingest_pipeline(vault, changing)
    run_m3_ingest_pipeline(vault, archived)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        archived_doc = connection.execute("SELECT doc_id FROM documents WHERE title = 'archived'").fetchone()["doc_id"]
        archive_document(connection, archived_doc)
        first_rebuild = rebuild_vector_index(connection)
        old_revision = connection.execute("SELECT current_revision_id FROM documents WHERE title = 'changing'").fetchone()["current_revision_id"]
    finally:
        connection.close()

    changing.write_text("# Changing\nnew vector current content\n", encoding="utf-8")
    run_m3_ingest_pipeline(vault, changing)
    shell_pdf.write_bytes(b"%PDF image only")
    monkeypatch.setattr("indbase_core.normalizers._run_markitdown_file", lambda _path: " ")
    run_m3_ingest_pipeline(vault, shell_pdf)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        result = search_chunks(connection, "anything", options=SearchOptions(mode="vector"))
        old_rows = [row for row in result.results if row.revision_id == old_revision]
        archived_rows = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM embeddings e
            JOIN documents d ON d.doc_id = e.doc_id
            WHERE d.status = 'archived'
            """
        ).fetchone()
        shell_rows = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM embeddings e
            JOIN documents d ON d.doc_id = e.doc_id
            WHERE d.current_revision_id IS NULL
            """
        ).fetchone()
    finally:
        connection.close()

    assert first_rebuild.embedded_chunks > 0
    assert result.result_count == 0
    assert old_rows == []
    assert archived_rows["count"] == 0
    assert shell_rows["count"] == 0


def test_hybrid_search_respects_forced_ocr_current_revision(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF text")
    monkeypatch.setattr("indbase_core.normalizers._run_markitdown_file", lambda _path: "# Paper\nold pdf vector text\n")
    run_m3_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        row = connection.execute("SELECT doc_id, original_path, current_revision_id FROM documents").fetchone()
        old_revision = row["current_revision_id"]
        rebuild_vector_index(connection)
        (vault / row["original_path"]).with_name("original.pdf.ocr.txt").write_text("ocr current vector text", encoding="utf-8")
        ocr = run_ocr_for_document(connection, vault, row["doc_id"], force=True)
        rebuild_vector_index(connection)
        result = search_chunks(connection, "ocr current vector", options=SearchOptions(mode="hybrid"))
    finally:
        connection.close()

    assert ocr.status == "succeeded"
    assert result.result_count >= 1
    assert result.results[0].revision_id == ocr.revision_id
    assert all(row.revision_id != old_revision for row in result.results)
    assert "ocr current vector" in result.results[0].snippet


def test_build_snippet_centers_first_matching_term() -> None:
    text = "alpha " * 40 + "needle " + "omega " * 40

    snippet = build_snippet(text, "needle", max_chars=80)

    assert snippet.startswith("...")
    assert "needle" in snippet
    assert snippet.endswith("...")
