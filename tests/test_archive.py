from pathlib import Path

from indbase_core.archive import archive_pending_sources
from indbase_core.db import connect
from indbase_core.ingest import plan_ingest_sources
from indbase_core.vault import init_vault


def test_archive_pending_supported_source_preserves_original_and_writes_records(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "note.md"
    source.write_bytes(b"# Note\nBody\n")

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        plan = plan_ingest_sources(connection, source)
        result = archive_pending_sources(connection, vault, plan.ingest_id)

        assert result.failed_items == 0
        assert len(result.archived_items) == 1
        archived = result.archived_items[0]

        document = connection.execute(
            """
            SELECT doc_id, current_revision_id, title, filename_slug, status, source_type,
                   source_uri, normalized_source_uri, source_hash, canonical_path,
                   original_path, category_id, ingest_status, fts_status
            FROM documents
            WHERE doc_id = ?
            """,
            (archived.doc_id,),
        ).fetchone()
        source_file = connection.execute(
            """
            SELECT source_file_id, doc_id, source_uri, normalized_source_uri,
                   original_filename, original_ext, size_bytes, source_hash, original_path
            FROM source_files
            WHERE source_file_id = ?
            """,
            (archived.source_file_id,),
        ).fetchone()
        ingest_item = connection.execute(
            "SELECT doc_id, status FROM ingest_items WHERE ingest_item_id = ?",
            (archived.ingest_item_id,),
        ).fetchone()
        revision_count = connection.execute(
            "SELECT COUNT(*) AS count FROM document_revisions WHERE doc_id = ?",
            (archived.doc_id,),
        ).fetchone()
    finally:
        connection.close()

    archived_original = vault / archived.original_path
    assert archived_original.is_file()
    assert archived_original.read_bytes() == b"# Note\nBody\n"
    assert source.read_bytes() == b"# Note\nBody\n"

    assert document["doc_id"] == archived.doc_id
    assert document["current_revision_id"] is None
    assert document["title"] == "note"
    assert document["filename_slug"] == "note"
    assert document["status"] == "active"
    assert document["source_type"] == "md"
    assert document["source_uri"] == str(source)
    assert document["normalized_source_uri"].endswith("/note.md")
    assert document["canonical_path"] is None
    assert document["original_path"] == archived.original_path
    assert document["category_id"] == "cat_uncategorized"
    assert document["ingest_status"] == "archived"
    assert document["fts_status"] == "not_indexed"

    assert source_file["source_file_id"] == archived.source_file_id
    assert source_file["doc_id"] == archived.doc_id
    assert source_file["original_filename"] == "note.md"
    assert source_file["original_ext"] == "md"
    assert source_file["size_bytes"] == len(b"# Note\nBody\n")
    assert source_file["source_hash"] == document["source_hash"]
    assert source_file["original_path"] == archived.original_path

    assert ingest_item["doc_id"] == archived.doc_id
    assert ingest_item["status"] == "running"
    assert revision_count["count"] == 0


def test_archive_pending_sources_skips_unsupported_items(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "image.png"
    source.write_bytes(b"png")

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        plan = plan_ingest_sources(connection, source)
        result = archive_pending_sources(connection, vault, plan.ingest_id)
        document_count = connection.execute("SELECT COUNT(*) AS count FROM documents").fetchone()
        source_file_count = connection.execute("SELECT COUNT(*) AS count FROM source_files").fetchone()
        revision_count = connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()
        chunk_count = connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()
        fts_count = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()
        item = connection.execute(
            "SELECT status, doc_id FROM ingest_items WHERE ingest_id = ?",
            (plan.ingest_id,),
        ).fetchone()
        review_count = connection.execute("SELECT COUNT(*) AS count FROM review_items").fetchone()
    finally:
        connection.close()

    assert result.archived_items == ()
    assert result.failed_items == 0
    assert document_count["count"] == 0
    assert source_file_count["count"] == 0
    assert revision_count["count"] == 0
    assert chunk_count["count"] == 0
    assert fts_count["count"] == 0
    assert item["status"] == "unsupported"
    assert item["doc_id"] is None
    assert review_count["count"] == 1


def test_archive_folder_only_archives_supported_pending_items(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "note.txt").write_bytes(b"hello")
    (sources / "image.png").write_bytes(b"png")

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        plan = plan_ingest_sources(connection, sources)
        result = archive_pending_sources(connection, vault, plan.ingest_id)
        statuses = [
            row["status"]
            for row in connection.execute(
                "SELECT status FROM ingest_items WHERE ingest_id = ? ORDER BY source_uri",
                (plan.ingest_id,),
            )
        ]
        run = connection.execute(
            "SELECT status, failed_items, unsupported_items FROM ingest_runs WHERE ingest_id = ?",
            (plan.ingest_id,),
        ).fetchone()
    finally:
        connection.close()

    assert len(result.archived_items) == 1
    assert result.failed_items == 0
    assert statuses == ["unsupported", "running"]
    assert run["status"] == "running"
    assert run["failed_items"] == 0
    assert run["unsupported_items"] == 1
