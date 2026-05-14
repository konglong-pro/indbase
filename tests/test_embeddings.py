from pathlib import Path
import sqlite3

from typer.testing import CliRunner

import indbase_core.normalizers as normalizers
from indbase_core.db import connect
from indbase_core.documents import archive_document
from indbase_core.doctor import run_doctor
from indbase_core.embeddings import DeterministicEmbeddingAdapter, rebuild_vector_index
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.ocr import run_ocr_for_document
from indbase_core.search import search_chunks
from indbase_core.time import utc_now_iso
from indbase_core.vault import init_vault
from indbase_cli.main import app


def test_rebuild_vector_index_embeds_active_current_chunks_only(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    active = tmp_path / "active.md"
    archived = tmp_path / "archived.md"
    active.write_text("# Active\nactive vector unique\n", encoding="utf-8")
    archived.write_text("# Archived\narchived vector unique\n", encoding="utf-8")
    run_m3_ingest_pipeline(vault, active)
    run_m3_ingest_pipeline(vault, archived)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        archived_doc = connection.execute("SELECT doc_id FROM documents WHERE title = 'archived'").fetchone()["doc_id"]
        archive_document(connection, archived_doc)
        result = rebuild_vector_index(connection)
        rows = list(connection.execute("SELECT doc_id, revision_id, chunk_id, provider, model, dimension, vector_ref, status FROM embeddings"))
        active_doc = connection.execute("SELECT doc_id, current_revision_id, embedding_status FROM documents WHERE title = 'active'").fetchone()
        archived_embeddings = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM embeddings e
            JOIN documents d ON d.doc_id = e.doc_id
            WHERE d.status = 'archived'
            """
        ).fetchone()
    finally:
        connection.close()

    assert result.embedded_chunks > 0
    assert result.failed_chunks == 0
    assert result.skipped_archived_chunks == 0
    assert len(rows) == result.embedded_chunks
    assert {row["doc_id"] for row in rows} == {active_doc["doc_id"]}
    assert {row["revision_id"] for row in rows} == {active_doc["current_revision_id"]}
    assert {row["provider"] for row in rows} == {"local"}
    assert {row["model"] for row in rows} == {"hash-v1"}
    assert rows[0]["dimension"] == 8
    assert '"vector"' in rows[0]["vector_ref"]
    assert {row["status"] for row in rows} == {"indexed"}
    assert active_doc["embedding_status"] == "indexed"
    assert archived_embeddings["count"] == 0


def test_rebuild_vector_index_skips_source_shells_and_failed_docs(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF image only")
    monkeypatch.setattr(normalizers, "_run_markitdown_file", lambda _path: " ")
    run_m3_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        result = rebuild_vector_index(connection)
        embeddings = connection.execute("SELECT COUNT(*) AS count FROM embeddings").fetchone()
        document = connection.execute("SELECT current_revision_id, ingest_status, embedding_status FROM documents").fetchone()
    finally:
        connection.close()

    assert result.current_chunks == 0
    assert result.embedded_chunks == 0
    assert result.failed_chunks == 0
    assert embeddings["count"] == 0
    assert document["current_revision_id"] is None
    assert document["ingest_status"] == "failed"
    assert document["embedding_status"] == "not_applicable"


def test_rebuild_vector_index_removes_old_revision_embeddings_after_reingest(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "paper.md"
    source.write_text("# Paper\nfirst vector revision unique\n", encoding="utf-8")
    run_m3_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        first = rebuild_vector_index(connection)
        first_revision = connection.execute("SELECT current_revision_id FROM documents").fetchone()["current_revision_id"]
    finally:
        connection.close()

    source.write_text("# Paper\nsecond vector revision unique\n", encoding="utf-8")
    run_m3_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        second = rebuild_vector_index(connection)
        current_revision = connection.execute("SELECT current_revision_id FROM documents").fetchone()["current_revision_id"]
        old_embeddings = connection.execute(
            "SELECT COUNT(*) AS count FROM embeddings WHERE revision_id = ?",
            (first_revision,),
        ).fetchone()
        current_embeddings = connection.execute(
            "SELECT COUNT(*) AS count FROM embeddings WHERE revision_id = ? AND status = 'indexed'",
            (current_revision,),
        ).fetchone()
        stale = connection.execute("SELECT COUNT(*) AS count FROM embeddings WHERE status = 'stale'").fetchone()
    finally:
        connection.close()

    assert first.embedded_chunks > 0
    assert second.embedded_chunks > 0
    assert current_revision != first_revision
    assert old_embeddings["count"] == 0
    assert current_embeddings["count"] == second.embedded_chunks
    assert stale["count"] == 0


def test_rebuild_vector_index_embeds_current_ocr_revision(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF image only")
    monkeypatch.setattr(normalizers, "_run_markitdown_file", lambda _path: " ")
    run_m3_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        row = connection.execute("SELECT doc_id, original_path FROM documents").fetchone()
        (vault / row["original_path"]).with_name("original.pdf.ocr.txt").write_text("ocr vector current unique", encoding="utf-8")
        ocr = run_ocr_for_document(connection, vault, row["doc_id"])
        result = rebuild_vector_index(connection)
        converter_names = list(
            connection.execute(
                """
                SELECT DISTINCT dr.converter_name
                FROM embeddings e
                JOIN document_revisions dr ON dr.revision_id = e.revision_id
                """
            )
        )
    finally:
        connection.close()

    assert ocr.status == "succeeded"
    assert result.embedded_chunks > 0
    assert [row["converter_name"] for row in converter_names] == ["ocr_sidecar"]


class FailingEmbeddingAdapter(DeterministicEmbeddingAdapter):
    provider = "test"
    model = "fail-v1"

    def embed(self, text: str) -> tuple[float, ...]:
        raise RuntimeError("embedding boom")


def test_embedding_failure_records_error_review_without_breaking_fts(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "note.md"
    source.write_text("# Note\nfts survives embedding failure\n", encoding="utf-8")
    run_m3_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        before_search = search_chunks(connection, "fts survives")
        result = rebuild_vector_index(connection, adapter=FailingEmbeddingAdapter())
        after_search = search_chunks(connection, "fts survives")
        document = connection.execute("SELECT fts_status, embedding_status FROM documents").fetchone()
        error = connection.execute("SELECT component, error_type FROM errors WHERE component = 'embedding_indexer'").fetchone()
        review = connection.execute("SELECT type, target_type FROM review_items WHERE type = 'embedding_failed'").fetchone()
        task = connection.execute("SELECT status FROM tasks WHERE task_id = ?", (result.task_id,)).fetchone()
    finally:
        connection.close()

    assert before_search.result_count == 1
    assert after_search.result_count == 1
    assert result.failed_chunks == result.current_chunks
    assert result.embedded_chunks == 0
    assert document["fts_status"] == "indexed"
    assert document["embedding_status"] == "failed"
    assert error["component"] == "embedding_indexer"
    assert error["error_type"] == "RuntimeError"
    assert review["target_type"] == "chunk"
    assert task["status"] == "failed"


def test_cli_index_rebuild_vectors_and_status(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "note.md"
    source.write_text("# Note\ncli vector unique\n", encoding="utf-8")
    run_m3_ingest_pipeline(vault, source)

    runner = CliRunner()
    rebuild = runner.invoke(app, ["index", "rebuild", "--vectors", "--vault", str(vault)])
    status = runner.invoke(app, ["index", "status", "--vault", str(vault)])

    assert rebuild.exit_code == 0
    assert "Vector rebuild complete" in rebuild.output
    assert "Embedded chunks:" in rebuild.output
    assert status.exit_code == 0
    assert "Embedding Status" in status.output
    assert "Embedding Rows" in status.output


def test_doctor_detects_orphan_embedding(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    raw_connection = sqlite3.connect(vault / ".indbase" / "db.sqlite")
    try:
        raw_connection.execute(
            """
            INSERT INTO embeddings(
              embedding_id, chunk_id, doc_id, revision_id, provider, model,
              dimension, vector_ref, content_hash, status, created_at, updated_at
            )
            VALUES ('embedding_orphan', 'chunk_missing', 'doc_missing', 'rev_missing',
                    'local', 'hash-v1', 8, '{"vector":[0]}', 'sha256:missing',
                    'indexed', ?, ?)
            """,
            (utc_now_iso(), utc_now_iso()),
        )
        raw_connection.commit()
    finally:
        raw_connection.close()

    report = run_doctor(vault)

    assert report.exit_code == 2
    assert "orphan_embedding" in {finding.code for finding in report.findings}
