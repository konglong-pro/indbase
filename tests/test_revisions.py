from pathlib import Path

from indbase_core.archive import archive_pending_sources
from indbase_core.conversion import convert_archived_sources
from indbase_core.db import connect
from indbase_core.ingest import plan_ingest_sources
from indbase_core.revisions import write_revisions_for_converted_sources
from indbase_core.vault import init_vault


def test_write_revision_creates_immutable_source_markdown_and_db_records(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "note.md"
    source.write_bytes(b"# Note\r\nBody\n")

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        plan = plan_ingest_sources(connection, source)
        archive_pending_sources(connection, vault, plan.ingest_id)
        conversion = convert_archived_sources(connection, vault, plan.ingest_id)

        result = write_revisions_for_converted_sources(connection, vault, plan.ingest_id)

        assert result.failed_items == 0
        assert len(result.written_revisions) == 1
        written = result.written_revisions[0]
        document = connection.execute(
            """
            SELECT current_revision_id, canonical_path, ingest_status, fts_status
            FROM documents
            WHERE doc_id = ?
            """,
            (written.doc_id,),
        ).fetchone()
        revision = connection.execute(
            """
            SELECT revision_id, doc_id, sequence, markdown_path, content_hash,
                   converter_name, converter_version, text_length, chunk_count
            FROM document_revisions
            WHERE revision_id = ?
            """,
            (written.revision_id,),
        ).fetchone()
        converter_run = connection.execute(
            "SELECT revision_id FROM converter_runs WHERE converter_run_id = ?",
            (conversion.converted_items[0].converter_run_id,),
        ).fetchone()
        ingest_item = connection.execute(
            "SELECT status FROM ingest_items WHERE ingest_item_id = ?",
            (written.ingest_item_id,),
        ).fetchone()
        chunk_count = connection.execute(
            "SELECT COUNT(*) AS count FROM chunks WHERE revision_id = ?",
            (written.revision_id,),
        ).fetchone()
    finally:
        connection.close()

    markdown_path = vault / written.markdown_path
    markdown = markdown_path.read_text(encoding="utf-8")

    assert written.sequence == 1
    assert written.revision_id == f"rev_{written.doc_id}_0001"
    assert written.markdown_path.endswith(f"note__{written.doc_id}__rev_0001.md")
    assert markdown_path.is_file()
    assert markdown.startswith("---\n")
    assert f'doc_id: "{written.doc_id}"' in markdown
    assert f'revision_id: "{written.revision_id}"' in markdown
    assert f'canonical_path: "{written.markdown_path}"' in markdown
    assert "chunk_count: 0" in markdown
    assert "fts_indexed: false" in markdown
    assert markdown.endswith("# Note\nBody\n")

    assert document["current_revision_id"] == written.revision_id
    assert document["canonical_path"] == written.markdown_path
    assert document["ingest_status"] == "revisioned"
    assert document["fts_status"] == "not_indexed"

    assert revision["revision_id"] == written.revision_id
    assert revision["doc_id"] == written.doc_id
    assert revision["sequence"] == 1
    assert revision["markdown_path"] == written.markdown_path
    assert revision["converter_name"] == "swallow"
    assert revision["converter_version"] == "test"
    assert revision["text_length"] == len("# Note\nBody\n")
    assert revision["chunk_count"] == 0

    assert converter_run["revision_id"] == written.revision_id
    assert ingest_item["status"] == "running"
    assert chunk_count["count"] == 0


def test_write_revision_detects_tampered_conversion_candidate(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "note.txt"
    source.write_text("stable body", encoding="utf-8")

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        plan = plan_ingest_sources(connection, source)
        archive_pending_sources(connection, vault, plan.ingest_id)
        conversion = convert_archived_sources(connection, vault, plan.ingest_id)
        candidate_path = vault / conversion.converted_items[0].candidate_path
        candidate_path.write_text("tampered\n", encoding="utf-8")

        result = write_revisions_for_converted_sources(connection, vault, plan.ingest_id)

        item = connection.execute(
            "SELECT status, error_id FROM ingest_items WHERE ingest_item_id = ?",
            (conversion.converted_items[0].ingest_item_id,),
        ).fetchone()
        document = connection.execute(
            "SELECT ingest_status, current_revision_id FROM documents WHERE doc_id = ?",
            (conversion.converted_items[0].doc_id,),
        ).fetchone()
        errors = connection.execute("SELECT COUNT(*) AS count FROM errors").fetchone()
        revisions = connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()
    finally:
        connection.close()

    assert result.failed_items == 1
    assert result.written_revisions == ()
    assert item["status"] == "failed"
    assert item["error_id"] is not None
    assert document["ingest_status"] == "failed"
    assert document["current_revision_id"] is None
    assert errors["count"] == 1
    assert revisions["count"] == 0
