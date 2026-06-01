from pathlib import Path

import pytest

import indbase_core.normalizers as normalizers
from indbase_core.db import connect
from indbase_core.ingest import M3_PIPELINE_CHECKPOINT_STAGES, run_m2_ingest_pipeline, run_m3_ingest_pipeline
from indbase_core.search import search_chunks
from indbase_core.vault import init_vault


class CheckpointCancelled(Exception):
    """SDK-compatible cancellation marker for pipeline tests (no consoler import)."""

    def __init__(self, checkpoint: str) -> None:
        self.checkpoint = checkpoint
        super().__init__(f"cancelled at {checkpoint}")


def test_m2_ingest_pipeline_writes_revision_without_search_indexing(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "note.md"
    source.write_text("# Note\nBody\n", encoding="utf-8")

    result = run_m2_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        run = connection.execute(
            """
            SELECT status, total_items, succeeded_items, failed_items,
                   unsupported_items, duplicate_items, task_id
            FROM ingest_runs
            WHERE ingest_id = ?
            """,
            (result.ingest_id,),
        ).fetchone()
        item = connection.execute(
            "SELECT status, finished_at FROM ingest_items WHERE ingest_id = ?",
            (result.ingest_id,),
        ).fetchone()
        document = connection.execute(
            """
            SELECT doc_id, current_revision_id, canonical_path, ingest_status, fts_status
            FROM documents
            """
        ).fetchone()
        revision_count = connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()
        chunk_count = connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()
        fts_count = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()
        task = connection.execute(
            "SELECT status, result_json FROM tasks WHERE task_id = ?",
            (result.task_id,),
        ).fetchone()
        event_types = [
            row["event_type"]
            for row in connection.execute(
                "SELECT event_type FROM task_events WHERE task_id = ? ORDER BY created_at",
                (result.task_id,),
            )
        ]
    finally:
        connection.close()

    assert result.status == "succeeded"
    assert result.total_items == 1
    assert result.succeeded_items == 1
    assert result.failed_items == 0
    assert result.unsupported_items == 0
    assert result.duplicate_items == 0
    assert result.written_revisions == 1
    assert result.searchable is False

    assert run["status"] == "succeeded"
    assert run["task_id"] == result.task_id
    assert run["succeeded_items"] == 1
    assert item["status"] == "succeeded"
    assert item["finished_at"] is not None
    assert document["current_revision_id"] is not None
    assert document["canonical_path"].endswith("__rev_0001.md")
    assert document["ingest_status"] == "revisioned"
    assert document["fts_status"] == "not_indexed"
    assert (vault / document["canonical_path"]).is_file()
    assert revision_count["count"] == 1
    assert chunk_count["count"] == 0
    assert fts_count["count"] == 0
    assert task["status"] == "succeeded"
    assert '"searchable": false' in task["result_json"]
    assert event_types == [
        "ingest_started",
        "sources_inspected",
        "originals_archived",
        "sources_converted",
        "revisions_written",
    ]


def test_m2_ingest_pipeline_folder_keeps_unsupported_visible(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "note.txt").write_text("hello", encoding="utf-8")
    (sources / "image.png").write_bytes(b"png")

    result = run_m2_ingest_pipeline(vault, sources)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        statuses = [
            row["status"]
            for row in connection.execute(
                "SELECT status FROM ingest_items WHERE ingest_id = ? ORDER BY source_uri",
                (result.ingest_id,),
            )
        ]
        review = connection.execute(
            "SELECT type, status FROM review_items WHERE type = 'unsupported_source'"
        ).fetchone()
        documents = connection.execute("SELECT COUNT(*) AS count FROM documents").fetchone()
        revisions = connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()
    finally:
        connection.close()

    assert result.status == "completed_with_issues"
    assert result.total_items == 2
    assert result.succeeded_items == 1
    assert result.unsupported_items == 1
    assert result.failed_items == 0
    assert result.review_items_count == 1
    assert statuses == ["unsupported", "succeeded"]
    assert review["status"] == "pending"
    assert documents["count"] == 1
    assert revisions["count"] == 1


def test_m2_ingest_pipeline_tier2_conversion_failure_is_queryable(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "deck.pptx"
    source.write_bytes(b"placeholder office bytes")

    def fail_markitdown(_path: Path) -> str:
        raise RuntimeError("markitdown boom")

    monkeypatch.setattr(normalizers, "_run_markitdown_file", fail_markitdown)

    result = run_m2_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        run = connection.execute(
            """
            SELECT status, failed_items, review_items_count
            FROM ingest_runs
            WHERE ingest_id = ?
            """,
            (result.ingest_id,),
        ).fetchone()
        item = connection.execute(
            "SELECT status, error_id, finished_at FROM ingest_items WHERE ingest_id = ?",
            (result.ingest_id,),
        ).fetchone()
        converter_run = connection.execute(
            "SELECT converter_run_id, status FROM converter_runs"
        ).fetchone()
        review = connection.execute(
            "SELECT type, target_type, target_id FROM review_items"
        ).fetchone()
        errors = connection.execute("SELECT COUNT(*) AS count FROM errors").fetchone()
        revisions = connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()
    finally:
        connection.close()

    assert result.status == "completed_with_issues"
    assert result.failed_items == 1
    assert result.review_items_count == 1
    assert result.written_revisions == 0
    assert run["status"] == "completed_with_issues"
    assert run["failed_items"] == 1
    assert run["review_items_count"] == 1
    assert item["status"] == "failed"
    assert item["error_id"] is not None
    assert item["finished_at"] is not None
    assert converter_run["status"] == "failed"
    assert review["type"] == "conversion_low_quality"
    assert review["target_type"] == "converter_run"
    assert review["target_id"] == converter_run["converter_run_id"]
    assert errors["count"] == 1
    assert revisions["count"] == 0


def test_m2_ingest_pipeline_exact_duplicate_reuses_existing_doc(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "same.md"
    source.write_text("# Same\nBody\n", encoding="utf-8")

    first = run_m2_ingest_pipeline(vault, source)
    second = run_m2_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        documents = connection.execute("SELECT COUNT(*) AS count FROM documents").fetchone()
        source_files = connection.execute("SELECT COUNT(*) AS count FROM source_files").fetchone()
        revisions = connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()
        duplicate_item = connection.execute(
            """
            SELECT status, doc_id, finished_at
            FROM ingest_items
            WHERE ingest_id = ?
            """,
            (second.ingest_id,),
        ).fetchone()
        first_doc = connection.execute(
            "SELECT doc_id FROM ingest_items WHERE ingest_id = ?",
            (first.ingest_id,),
        ).fetchone()
        second_run = connection.execute(
            "SELECT status, duplicate_items, succeeded_items FROM ingest_runs WHERE ingest_id = ?",
            (second.ingest_id,),
        ).fetchone()
    finally:
        connection.close()

    assert first.status == "succeeded"
    assert second.status == "completed_with_issues"
    assert second.duplicate_items == 1
    assert second.succeeded_items == 0
    assert second.written_revisions == 0
    assert documents["count"] == 1
    assert source_files["count"] == 1
    assert revisions["count"] == 1
    assert duplicate_item["status"] == "duplicate"
    assert duplicate_item["doc_id"] == first_doc["doc_id"]
    assert duplicate_item["finished_at"] is not None
    assert second_run["status"] == "completed_with_issues"
    assert second_run["duplicate_items"] == 1
    assert second_run["succeeded_items"] == 0


def test_m3_ingest_pipeline_writes_chunks_indexes_fts_and_makes_doc_searchable(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "searchable.md"
    source.write_text("# Searchable\nNeedle body for M3 ingest.\n", encoding="utf-8")

    result = run_m3_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        document = connection.execute(
            "SELECT doc_id, current_revision_id, fts_status FROM documents"
        ).fetchone()
        item = connection.execute(
            "SELECT status FROM ingest_items WHERE ingest_id = ?",
            (result.ingest_id,),
        ).fetchone()
        chunk_count = connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()
        fts_count = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()
        task = connection.execute(
            "SELECT status, result_json FROM tasks WHERE task_id = ?",
            (result.task_id,),
        ).fetchone()
        events = [
            row["event_type"]
            for row in connection.execute(
                "SELECT event_type FROM task_events WHERE task_id = ? ORDER BY created_at",
                (result.task_id,),
            )
        ]
        search = search_chunks(connection, "needle")
    finally:
        connection.close()

    assert result.status == "succeeded"
    assert result.succeeded_items == 1
    assert result.failed_items == 0
    assert result.written_revisions == 1
    assert result.chunked_documents == 1
    assert result.indexed_documents == 1
    assert result.indexed_chunks == 1
    assert result.index_failed_documents == 0
    assert result.searchable is True
    assert document["current_revision_id"] is not None
    assert document["fts_status"] == "indexed"
    assert item["status"] == "succeeded"
    assert chunk_count["count"] == 1
    assert fts_count["count"] == 1
    assert task["status"] == "succeeded"
    assert '"searchable": true' in task["result_json"]
    assert events[-2:] == ["chunks_written", "fts_rebuilt"]
    assert search.result_count == 1


def test_m3_ingest_pipeline_folder_can_be_searchable_with_unsupported_issue(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "note.txt").write_text("folder needle", encoding="utf-8")
    (sources / "scan.png").write_bytes(b"png")

    result = run_m3_ingest_pipeline(vault, sources)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        statuses = [
            row["status"]
            for row in connection.execute(
                "SELECT status FROM ingest_items WHERE ingest_id = ? ORDER BY source_uri",
                (result.ingest_id,),
            )
        ]
        search = search_chunks(connection, "needle")
        review = connection.execute(
            "SELECT type FROM review_items WHERE type = 'unsupported_source'"
        ).fetchone()
    finally:
        connection.close()

    assert result.status == "completed_with_issues"
    assert result.succeeded_items == 1
    assert result.unsupported_items == 1
    assert result.searchable is True
    assert statuses == ["succeeded", "unsupported"]
    assert search.result_count == 1
    assert review["type"] == "unsupported_source"


def test_m3_ingest_pipeline_pdf_text_ingest_is_searchable(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF placeholder bytes")
    monkeypatch.setattr(normalizers, "_run_markitdown_file", lambda _path: "# Paper\nPDF text needle\n")

    result = run_m3_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        document = connection.execute(
            """
            SELECT source_type, current_revision_id, original_path, canonical_path, fts_status
            FROM documents
            """
        ).fetchone()
        converter_run = connection.execute(
            "SELECT converter_name, status FROM converter_runs"
        ).fetchone()
        revision_count = connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()
        chunk_count = connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()
        fts_count = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()
        search = search_chunks(connection, "PDF text needle")
    finally:
        connection.close()

    assert result.status == "succeeded"
    assert result.succeeded_items == 1
    assert result.failed_items == 0
    assert result.searchable is True
    assert document["source_type"] == "pdf"
    assert document["current_revision_id"] is not None
    assert document["original_path"].endswith("/original.pdf")
    assert document["canonical_path"].endswith("__rev_0001.md")
    assert document["fts_status"] == "indexed"
    assert converter_run["converter_name"] == "swallow"
    assert converter_run["status"] == "succeeded"
    assert revision_count["count"] == 1
    assert chunk_count["count"] == 1
    assert fts_count["count"] == 1
    assert search.result_count == 1


def test_m3_ingest_pipeline_pdf_empty_output_fails_without_searchable_revision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF image only placeholder")
    monkeypatch.setattr(normalizers, "_run_markitdown_file", lambda _path: "   ")

    result = run_m3_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        document = connection.execute(
            """
            SELECT source_type, current_revision_id, ingest_status, fts_status, needs_review, quality_status
            FROM documents
            """
        ).fetchone()
        item = connection.execute(
            "SELECT status, error_id FROM ingest_items WHERE ingest_id = ?",
            (result.ingest_id,),
        ).fetchone()
        converter_run = connection.execute(
            "SELECT status, converter_name FROM converter_runs"
        ).fetchone()
        revision_count = connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()
        chunk_count = connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()
        fts_count = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()
        review = connection.execute("SELECT type FROM review_items").fetchone()
        error = connection.execute("SELECT error_type FROM errors").fetchone()
    finally:
        connection.close()

    assert result.status == "completed_with_issues"
    assert result.succeeded_items == 0
    assert result.failed_items == 1
    assert result.searchable is False
    assert document["source_type"] == "pdf"
    assert document["current_revision_id"] is None
    assert document["ingest_status"] == "failed"
    assert document["fts_status"] == "not_indexed"
    assert document["needs_review"] == 1
    assert document["quality_status"] == "failed"
    assert item["status"] == "failed"
    assert item["error_id"] is not None
    assert converter_run["converter_name"] == "swallow"
    assert converter_run["status"] == "failed"
    assert revision_count["count"] == 0
    assert chunk_count["count"] == 0
    assert fts_count["count"] == 0
    assert review["type"] == "conversion_low_quality"
    assert error["error_type"] == "no_extractable_content"


def test_m3_conversion_failure_does_not_create_revision_chunks_or_fts(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "broken.pptx"
    source.write_bytes(b"not a real office package")

    def fail_markitdown(_path: Path) -> str:
        raise RuntimeError("markitdown boom")

    monkeypatch.setattr(normalizers, "_run_markitdown_file", fail_markitdown)

    result = run_m3_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        document = connection.execute(
            """
            SELECT current_revision_id, ingest_status, fts_status, needs_review, quality_status
            FROM documents
            """
        ).fetchone()
        item = connection.execute(
            "SELECT status, error_id FROM ingest_items WHERE ingest_id = ?",
            (result.ingest_id,),
        ).fetchone()
        converter_run = connection.execute(
            "SELECT status, converter_name FROM converter_runs"
        ).fetchone()
        revision_count = connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()
        chunk_count = connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()
        fts_count = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()
        review = connection.execute("SELECT type FROM review_items").fetchone()
        errors = connection.execute("SELECT COUNT(*) AS count FROM errors").fetchone()
        task = connection.execute(
            "SELECT status, result_json FROM tasks WHERE task_id = ?",
            (result.task_id,),
        ).fetchone()
    finally:
        connection.close()

    assert result.status == "completed_with_issues"
    assert result.failed_items == 1
    assert result.written_revisions == 0
    assert result.chunked_documents == 0
    assert result.indexed_chunks == 0
    assert result.searchable is False
    assert document["current_revision_id"] is None
    assert document["ingest_status"] == "failed"
    assert document["fts_status"] == "not_indexed"
    assert document["needs_review"] == 1
    assert document["quality_status"] == "failed"
    assert item["status"] == "failed"
    assert item["error_id"] is not None
    assert converter_run["status"] == "failed"
    assert converter_run["converter_name"] == "swallow"
    assert revision_count["count"] == 0
    assert chunk_count["count"] == 0
    assert fts_count["count"] == 0
    assert review["type"] == "conversion_low_quality"
    assert errors["count"] == 1
    assert task["status"] == "completed_with_issues"
    assert '"failed_items": 1' in task["result_json"]


def test_m3_no_content_file_fails_before_revision_and_does_not_pollute_later_ingest(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    good = tmp_path / "good.md"
    good.write_text("# Good\nnew searchable body\n", encoding="utf-8")

    first = run_m3_ingest_pipeline(vault, empty)
    second = run_m3_ingest_pipeline(vault, good)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        failed_empty = connection.execute(
            """
            SELECT ii.status AS item_status, ii.error_id, d.current_revision_id,
                   d.ingest_status, d.fts_status, d.needs_review, d.quality_status
            FROM ingest_items ii
            JOIN documents d ON d.doc_id = ii.doc_id
            WHERE ii.ingest_id = ?
            """,
            (first.ingest_id,),
        ).fetchone()
        revision_count = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM document_revisions dr
            JOIN ingest_items ii ON ii.doc_id = dr.doc_id
            WHERE ii.ingest_id = ?
            """,
            (first.ingest_id,),
        ).fetchone()
        empty_chunks = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM chunks c
            JOIN ingest_items ii ON ii.doc_id = c.doc_id
            WHERE ii.ingest_id = ?
            """,
            (first.ingest_id,),
        ).fetchone()
        empty_fts = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM chunks_fts f
            JOIN ingest_items ii ON ii.doc_id = f.doc_id
            WHERE ii.ingest_id = ?
            """,
            (first.ingest_id,),
        ).fetchone()
        error = connection.execute(
            """
            SELECT error_type
            FROM errors
            WHERE error_id = ?
            """,
            (failed_empty["error_id"],),
        ).fetchone()
        review = connection.execute(
            """
            SELECT type
            FROM review_items ri
            JOIN converter_runs cr ON cr.converter_run_id = ri.target_id
            JOIN ingest_items ii ON ii.doc_id = cr.doc_id
            WHERE ii.ingest_id = ?
            """,
            (first.ingest_id,),
        ).fetchone()
        second_task = connection.execute(
            "SELECT status, result_json FROM tasks WHERE task_id = ?",
            (second.task_id,),
        ).fetchone()
        search = search_chunks(connection, "searchable body")
    finally:
        connection.close()

    assert first.status == "completed_with_issues"
    assert first.failed_items == 1
    assert first.written_revisions == 0
    assert first.chunked_documents == 0
    assert first.index_failed_documents == 0
    assert first.searchable is False
    assert failed_empty["item_status"] == "failed"
    assert failed_empty["error_id"] is not None
    assert failed_empty["current_revision_id"] is None
    assert failed_empty["ingest_status"] == "failed"
    assert failed_empty["fts_status"] == "not_indexed"
    assert failed_empty["needs_review"] == 1
    assert failed_empty["quality_status"] == "failed"
    assert revision_count["count"] == 0
    assert empty_chunks["count"] == 0
    assert empty_fts["count"] == 0
    assert error["error_type"] == "no_extractable_content"
    assert review["type"] == "conversion_low_quality"
    assert second.status == "succeeded"
    assert second.failed_items == 0
    assert second.index_failed_documents == 0
    assert second.searchable is True
    assert second_task["status"] == "succeeded"
    assert '"index_failed_documents": 0' in second_task["result_json"]
    assert search.result_count == 1


def test_m3_reingest_changed_source_reuses_doc_and_writes_new_revision(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "same-path.md"
    source.write_text("# Same Path\nalphaone body\n", encoding="utf-8")

    first = run_m3_ingest_pipeline(vault, source)
    source.write_text("# Same Path\nbetatwo body\n", encoding="utf-8")
    second = run_m3_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        document = connection.execute(
            """
            SELECT doc_id, current_revision_id, canonical_path, fts_status
            FROM documents
            """
        ).fetchone()
        documents = connection.execute("SELECT COUNT(*) AS count FROM documents").fetchone()
        source_files = connection.execute("SELECT COUNT(*) AS count FROM source_files").fetchone()
        revisions = connection.execute(
            "SELECT revision_id, sequence, markdown_path FROM document_revisions ORDER BY sequence"
        ).fetchall()
        chunks = connection.execute(
            """
            SELECT revision_id, is_current
            FROM chunks
            WHERE doc_id = ?
            ORDER BY revision_id
            """,
            (document["doc_id"],),
        ).fetchall()
        fts_count = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()
        second_item = connection.execute(
            "SELECT status, doc_id FROM ingest_items WHERE ingest_id = ?",
            (second.ingest_id,),
        ).fetchone()
        old_search = search_chunks(connection, "alphaone")
        new_search = search_chunks(connection, "betatwo")
    finally:
        connection.close()

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert second.duplicate_items == 0
    assert second.succeeded_items == 1
    assert second.written_revisions == 1
    assert second.chunked_documents == 1
    assert second.searchable is True

    assert documents["count"] == 1
    assert source_files["count"] == 2
    assert len(revisions) == 2
    assert revisions[0]["sequence"] == 1
    assert revisions[0]["markdown_path"].endswith("__rev_0001.md")
    assert revisions[1]["sequence"] == 2
    assert revisions[1]["markdown_path"].endswith("__rev_0002.md")
    assert document["current_revision_id"] == revisions[1]["revision_id"]
    assert document["canonical_path"] == revisions[1]["markdown_path"]
    assert document["fts_status"] == "indexed"
    assert second_item["status"] == "succeeded"
    assert second_item["doc_id"] == document["doc_id"]
    assert [row["is_current"] for row in chunks] == [0, 1]
    assert fts_count["count"] == 1
    assert old_search.result_count == 0
    assert new_search.result_count == 1


def test_m3_reingest_same_normalized_markdown_skips_empty_revision(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "stable.md"
    source.write_bytes(b"# Stable\nsamebody\n")

    first = run_m3_ingest_pipeline(vault, source)
    source.write_bytes(b"# Stable\r\nsamebody\r\n")
    second = run_m3_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        document = connection.execute(
            "SELECT doc_id, current_revision_id, fts_status FROM documents"
        ).fetchone()
        documents = connection.execute("SELECT COUNT(*) AS count FROM documents").fetchone()
        source_files = connection.execute("SELECT COUNT(*) AS count FROM source_files").fetchone()
        revisions = connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()
        chunks = connection.execute("SELECT COUNT(*) AS count FROM chunks WHERE is_current = 1").fetchone()
        fts_count = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()
        converter_statuses = {
            row["status"]
            for row in connection.execute("SELECT status FROM converter_runs")
        }
        second_item = connection.execute(
            "SELECT status, doc_id FROM ingest_items WHERE ingest_id = ?",
            (second.ingest_id,),
        ).fetchone()
        search = search_chunks(connection, "samebody")
    finally:
        connection.close()

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert second.succeeded_items == 1
    assert second.duplicate_items == 0
    assert second.written_revisions == 0
    assert second.chunked_documents == 0
    assert second.searchable is True

    assert documents["count"] == 1
    assert source_files["count"] == 2
    assert revisions["count"] == 1
    assert chunks["count"] == 1
    assert fts_count["count"] == 1
    assert document["current_revision_id"] is not None
    assert document["fts_status"] == "indexed"
    assert converter_statuses == {"succeeded", "skipped_no_content_change"}
    assert second_item["status"] == "succeeded"
    assert second_item["doc_id"] == document["doc_id"]
    assert search.result_count == 1


def test_m3_ingest_pipeline_invokes_checkpoint_stages(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "note.md"
    source.write_text("# Note\nBody\n", encoding="utf-8")
    seen: list[str] = []

    run_m3_ingest_pipeline(vault, source, checkpoint=seen.append)

    assert seen == list(M3_PIPELINE_CHECKPOINT_STAGES)


def test_m3_ingest_pipeline_cancelled_checkpoint_reraises_and_cancels_task(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "note.md"
    source.write_text("# Note\nBody\n", encoding="utf-8")

    def checkpoint(stage: str) -> None:
        if stage == "archive":
            raise CheckpointCancelled("archive")

    with pytest.raises(CheckpointCancelled) as exc_info:
        run_m3_ingest_pipeline(vault, source, checkpoint=checkpoint)

    assert exc_info.value.checkpoint == "archive"

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        task = connection.execute(
            "SELECT status, error_json FROM tasks ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        event_types = [
            row["event_type"]
            for row in connection.execute(
                "SELECT event_type FROM task_events ORDER BY created_at"
            )
        ]
    finally:
        connection.close()

    assert task["status"] == "cancelled"
    assert task["error_json"] is not None
    assert '"checkpoint": "archive"' in task["error_json"]
    assert '"type": "CheckpointCancelled"' in task["error_json"]
    assert "ingest_cancelled" in event_types
