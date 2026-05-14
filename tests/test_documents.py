from pathlib import Path

from indbase_core.chunker import chunk_current_revision
from indbase_core.db import connect
from indbase_core.documents import archive_document, restore_document
from indbase_core.indexer import rebuild_fts_index
from indbase_core.ingest import run_m2_ingest_pipeline
from indbase_core.search import search_chunks
from indbase_core.vault import init_vault


def test_archive_and_restore_document_affect_search_without_deleting_fts(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "note.md"
    source.write_text("# Note\nSearchable body\n", encoding="utf-8")
    run_m2_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        chunk_current_revision(connection, vault, doc_id)
        rebuild_fts_index(connection, vault)

        before = search_chunks(connection, "searchable")
        archived = archive_document(connection, doc_id)
        after_archive = search_chunks(connection, "searchable")
        fts_after_archive = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()
        chunks_after_archive = connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()
        document_after_archive = connection.execute(
            "SELECT status, archived_at FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()

        restored = restore_document(connection, doc_id)
        after_restore = search_chunks(connection, "searchable")
        fts_after_restore = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()
        document_after_restore = connection.execute(
            "SELECT status, archived_at FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
    finally:
        connection.close()

    assert before.result_count == 1
    assert archived.previous_status == "active"
    assert archived.status == "archived"
    assert archived.archived_at is not None
    assert archived.changed is True
    assert after_archive.result_count == 0
    assert fts_after_archive["count"] == 1
    assert chunks_after_archive["count"] == 1
    assert document_after_archive["status"] == "archived"
    assert document_after_archive["archived_at"] is not None

    assert restored.previous_status == "archived"
    assert restored.status == "active"
    assert restored.archived_at is None
    assert restored.changed is True
    assert after_restore.result_count == 1
    assert fts_after_restore["count"] == 1
    assert document_after_restore["status"] == "active"
    assert document_after_restore["archived_at"] is None


def test_archive_and_restore_document_are_idempotent(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "note.md"
    source.write_text("# Note\nBody\n", encoding="utf-8")
    run_m2_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        first_archive = archive_document(connection, doc_id)
        second_archive = archive_document(connection, doc_id)
        first_restore = restore_document(connection, doc_id)
        second_restore = restore_document(connection, doc_id)
    finally:
        connection.close()

    assert first_archive.changed is True
    assert second_archive.changed is False
    assert second_archive.archived_at == first_archive.archived_at
    assert first_restore.changed is True
    assert second_restore.changed is False
