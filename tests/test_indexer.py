from pathlib import Path

import indbase_core.normalizers as normalizers
from indbase_core.chunker import chunk_current_revision, chunk_id_for_revision
from indbase_core.conversion import hash_markdown
from indbase_core.db import connect
from indbase_core.indexer import rebuild_fts_index
from indbase_core.ingest import run_m2_ingest_pipeline
from indbase_core.time import utc_now_iso
from indbase_core.vault import init_vault


def test_rebuild_fts_index_indexes_active_current_chunks_with_cjk_bigrams(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "knowledge.md"
    source.write_text("# 知识库\n这是个人知识数据库。\n\n## Search\nlocal search works\n", encoding="utf-8")
    run_m2_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        doc = connection.execute("SELECT doc_id FROM documents").fetchone()
        chunk_current_revision(connection, vault, doc["doc_id"])

        result = rebuild_fts_index(connection, vault)
        document = connection.execute("SELECT fts_status FROM documents WHERE doc_id = ?", (doc["doc_id"],)).fetchone()
        fts_rows = list(
            connection.execute(
                "SELECT chunk_id, doc_id, revision_id, title, text FROM chunks_fts ORDER BY chunk_id"
            )
        )
        cjk_hit = connection.execute(
            "SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH ?",
            ("知识",),
        ).fetchone()
        english_hit = connection.execute(
            "SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH ?",
            ("search",),
        ).fetchone()
    finally:
        connection.close()

    assert result.active_documents == 1
    assert result.indexed_documents == 1
    assert result.failed_documents == 0
    assert result.indexed_chunks == 2
    assert document["fts_status"] == "indexed"
    assert len(fts_rows) == 2
    assert fts_rows[0]["doc_id"] == doc["doc_id"]
    assert "知识" in fts_rows[0]["text"]
    assert cjk_hit is not None
    assert english_hit is not None


def test_rebuild_fts_index_is_idempotent(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "note.md"
    source.write_text("# Note\nBody\n", encoding="utf-8")
    run_m2_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        chunk_current_revision(connection, vault, doc_id)

        first = rebuild_fts_index(connection, vault)
        second = rebuild_fts_index(connection, vault)
        fts_count = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()
        errors = connection.execute("SELECT COUNT(*) AS count FROM errors WHERE component = 'fts_indexer'").fetchone()
    finally:
        connection.close()

    assert first.indexed_chunks == 1
    assert second.indexed_chunks == 1
    assert fts_count["count"] == 1
    assert errors["count"] == 0


def test_rebuild_fts_index_excludes_archived_documents_and_old_revisions(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    active_source = tmp_path / "active.md"
    archived_source = tmp_path / "archived.md"
    active_source.write_text("# Active\nCurrent text\n", encoding="utf-8")
    archived_source.write_text("# Archived\nHidden text\n", encoding="utf-8")
    run_m2_ingest_pipeline(vault, active_source)
    run_m2_ingest_pipeline(vault, archived_source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        docs = list(connection.execute("SELECT doc_id, title, current_revision_id FROM documents ORDER BY title"))
        active_doc = next(row for row in docs if row["title"] == "active")
        archived_doc = next(row for row in docs if row["title"] == "archived")
        chunk_current_revision(connection, vault, active_doc["doc_id"])
        chunk_current_revision(connection, vault, archived_doc["doc_id"])
        connection.execute(
            "UPDATE documents SET status = 'archived', archived_at = ?, updated_at = ? WHERE doc_id = ?",
            (utc_now_iso(), utc_now_iso(), archived_doc["doc_id"]),
        )

        old_revision_id = f"rev_{active_doc['doc_id']}_0002"
        now = utc_now_iso()
        connection.execute(
            """
            INSERT INTO document_revisions(
              revision_id, doc_id, sequence, markdown_path, content_hash,
              converter_name, converter_version, chunk_strategy, text_length,
              chunk_count, created_at, updated_at
            )
            VALUES (?, ?, 2, 'sources/old.md', ?, 'test', 'test', 'test', 8, 1, ?, ?)
            """,
            (old_revision_id, active_doc["doc_id"], hash_markdown("old text"), now, now),
        )
        connection.execute(
            """
            INSERT INTO chunks(
              chunk_id, doc_id, revision_id, sequence, heading_path_json,
              text, start_offset, end_offset, source_page, language,
              token_count, content_hash, is_current, created_at, updated_at
            )
            VALUES (?, ?, ?, 1, '[]', 'Old text', 0, 8, NULL, NULL, 2, ?, 1, ?, ?)
            """,
            (
                chunk_id_for_revision(old_revision_id, 1),
                active_doc["doc_id"],
                old_revision_id,
                hash_markdown("Old text"),
                now,
                now,
            ),
        )
        connection.commit()

        result = rebuild_fts_index(connection, vault)
        fts_rows = list(connection.execute("SELECT doc_id, revision_id, text FROM chunks_fts"))
        hidden_hit = connection.execute(
            "SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH ?",
            ("hidden",),
        ).fetchone()
        old_hit = connection.execute(
            "SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH ?",
            ("old",),
        ).fetchone()
    finally:
        connection.close()

    assert result.active_documents == 1
    assert result.indexed_documents == 1
    assert result.indexed_chunks == 1
    assert len(fts_rows) == 1
    assert fts_rows[0]["doc_id"] == active_doc["doc_id"]
    assert fts_rows[0]["revision_id"] == active_doc["current_revision_id"]
    assert hidden_hit is None
    assert old_hit is None


def test_rebuild_fts_index_records_error_and_review_for_missing_chunks(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "note.md"
    source.write_text("# Note\nBody\n", encoding="utf-8")
    run_m2_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        result = rebuild_fts_index(connection, vault)
        document = connection.execute("SELECT doc_id, fts_status FROM documents").fetchone()
        error = connection.execute(
            "SELECT error_type, payload_json FROM errors WHERE component = 'fts_indexer'"
        ).fetchone()
        review = connection.execute(
            "SELECT type, target_type, target_id, reason FROM review_items WHERE type = 'indexing_failed'"
        ).fetchone()
        fts_count = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()
    finally:
        connection.close()

    assert result.active_documents == 1
    assert result.indexed_documents == 0
    assert result.failed_documents == 1
    assert result.failures[0].reason == "missing_current_chunks"
    assert document["fts_status"] == "failed"
    assert error["error_type"] == "missing_current_chunks"
    assert document["doc_id"] in error["payload_json"]
    assert review["target_type"] == "document"
    assert review["target_id"] == document["doc_id"]
    assert "missing_current_chunks" in review["reason"]
    assert fts_count["count"] == 0


def test_rebuild_fts_index_records_missing_markdown_before_indexing_chunks(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "note.md"
    source.write_text("# Note\nBody\n", encoding="utf-8")
    run_m2_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        doc = connection.execute("SELECT doc_id, canonical_path FROM documents").fetchone()
        chunk_current_revision(connection, vault, doc["doc_id"])
        (vault / doc["canonical_path"]).unlink()

        result = rebuild_fts_index(connection, vault)
        document = connection.execute("SELECT fts_status FROM documents WHERE doc_id = ?", (doc["doc_id"],)).fetchone()
        error = connection.execute(
            "SELECT error_type FROM errors WHERE component = 'fts_indexer'"
        ).fetchone()
        fts_count = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()
    finally:
        connection.close()

    assert result.failed_documents == 1
    assert result.failures[0].reason == "missing_revision_markdown"
    assert document["fts_status"] == "failed"
    assert error["error_type"] == "missing_revision_markdown"
    assert fts_count["count"] == 0


def test_rebuild_fts_index_skips_failed_documents_without_revisions(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "deck.pptx"
    source.write_bytes(b"placeholder office bytes")

    def fail_markitdown(_path: Path) -> str:
        raise RuntimeError("markitdown boom")

    monkeypatch.setattr(normalizers, "_run_markitdown_file", fail_markitdown)
    run_m2_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        result = rebuild_fts_index(connection, vault)
        document = connection.execute(
            "SELECT ingest_status, fts_status FROM documents"
        ).fetchone()
        index_errors = connection.execute(
            "SELECT COUNT(*) AS count FROM errors WHERE component = 'fts_indexer'"
        ).fetchone()
        index_reviews = connection.execute(
            "SELECT COUNT(*) AS count FROM review_items WHERE type = 'indexing_failed'"
        ).fetchone()
    finally:
        connection.close()

    assert result.active_documents == 0
    assert result.indexed_documents == 0
    assert result.failed_documents == 0
    assert document["ingest_status"] == "failed"
    assert document["fts_status"] == "not_indexed"
    assert index_errors["count"] == 0
    assert index_reviews["count"] == 0
