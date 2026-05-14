"""Run the M7.1 embedding index gate against temporary vaults."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3

import indbase_core.normalizers as normalizers
from indbase_core.db import connect
from indbase_core.doctor import run_doctor
from indbase_core.documents import archive_document
from indbase_core.embeddings import DeterministicEmbeddingAdapter, rebuild_vector_index
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.ocr import run_ocr_for_document
from indbase_core.search import search_chunks
from indbase_core.time import utc_now_iso
from indbase_core.vault import init_vault


ROOT = Path.cwd()


class FailingEmbeddingAdapter(DeterministicEmbeddingAdapter):
    provider = "gate"
    model = "fail-v1"

    def embed(self, text: str) -> tuple[float, ...]:
        raise RuntimeError("gate embedding failure")


def main() -> None:
    root = ROOT / ".tmp" / f"m71-embedding-index-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    root.mkdir(parents=True)
    original_markitdown = normalizers._run_markitdown_file
    try:
        scope = _run_scope_gate(root / "scope")
        failure = _run_failure_gate(root / "failure")
        orphan = _run_orphan_detection_gate(root / "orphan")
    finally:
        normalizers._run_markitdown_file = original_markitdown

    summary = {**scope, **failure, **orphan}
    hard_metrics = {
        "embedded_archived_chunks": summary["embedded_archived_chunks"],
        "embedded_old_revision_chunks": summary["embedded_old_revision_chunks"],
        "embedded_source_shells": summary["embedded_source_shells"],
        "embedding_failures_blocking_fts": summary["embedding_failures_blocking_fts"],
        "stale_embeddings_after_reingest": summary["stale_embeddings_after_reingest"],
        "orphan_embeddings": summary["orphan_embeddings"],
    }
    if summary["embedded_current_chunks"] <= 0:
        raise RuntimeError(f"M7.1 embedded no current chunks: {summary}")
    if any(value != 0 for value in hard_metrics.values()):
        raise RuntimeError(f"M7.1 hard metrics failed: {hard_metrics}")
    if summary["ocr_current_revision_embeddings"] <= 0:
        raise RuntimeError(f"M7.1 did not embed OCR current revision: {summary}")
    if summary["orphan_embedding_doctor_detected"] != 1:
        raise RuntimeError(f"M7.1 doctor did not detect orphan embedding: {summary}")

    print(json.dumps(summary, sort_keys=True))
    print("M71_EMBEDDING_INDEX_GATE=passed")
    print(f"DOGFOOD_ROOT={root}")


def _run_scope_gate(root: Path) -> dict[str, int]:
    vault = root / "vault"
    root.mkdir(parents=True)
    init_vault(vault)

    active = root / "active.md"
    changing = root / "changing.md"
    archived = root / "archived.md"
    shell_pdf = root / "shell.pdf"
    ocr_pdf = root / "ocr.pdf"
    active.write_text("# Active\nactive embedding gate unique\n", encoding="utf-8")
    changing.write_text("# Changing\nold embedding gate unique\n", encoding="utf-8")
    archived.write_text("# Archived\narchived embedding gate unique\n", encoding="utf-8")
    shell_pdf.write_bytes(b"%PDF shell")
    ocr_pdf.write_bytes(b"%PDF ocr")

    run_m3_ingest_pipeline(vault, active)
    run_m3_ingest_pipeline(vault, changing)
    changing.write_text("# Changing\ncurrent embedding gate unique\n", encoding="utf-8")
    run_m3_ingest_pipeline(vault, changing)
    run_m3_ingest_pipeline(vault, archived)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        archived_doc = connection.execute("SELECT doc_id FROM documents WHERE title = 'archived'").fetchone()["doc_id"]
        archive_document(connection, archived_doc)
    finally:
        connection.close()

    normalizers._run_markitdown_file = lambda _path: " "
    run_m3_ingest_pipeline(vault, shell_pdf)
    run_m3_ingest_pipeline(vault, ocr_pdf)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        ocr_doc = connection.execute("SELECT doc_id, original_path FROM documents WHERE source_uri = ?", (str(ocr_pdf),)).fetchone()
        (vault / ocr_doc["original_path"]).with_name("original.pdf.ocr.txt").write_text(
            "ocr embedding gate unique",
            encoding="utf-8",
        )
        ocr = run_ocr_for_document(connection, vault, ocr_doc["doc_id"])
        result = rebuild_vector_index(connection)
        summary = {
            "embedded_current_chunks": result.embedded_chunks,
            "embedded_archived_chunks": _count(
                connection,
                """
                SELECT COUNT(*) AS count
                FROM embeddings e
                JOIN documents d ON d.doc_id = e.doc_id
                WHERE d.status = 'archived'
                """,
            ),
            "embedded_old_revision_chunks": _count(
                connection,
                """
                SELECT COUNT(*) AS count
                FROM embeddings e
                JOIN documents d ON d.doc_id = e.doc_id
                WHERE d.current_revision_id != e.revision_id
                """,
            ),
            "embedded_source_shells": _count(
                connection,
                """
                SELECT COUNT(*) AS count
                FROM embeddings e
                JOIN documents d ON d.doc_id = e.doc_id
                WHERE d.current_revision_id IS NULL
                """,
            ),
            "stale_embeddings_after_reingest": _count(connection, "SELECT COUNT(*) AS count FROM embeddings WHERE status = 'stale'"),
            "orphan_embeddings": _count(
                connection,
                """
                SELECT COUNT(*) AS count
                FROM embeddings e
                LEFT JOIN chunks c ON c.chunk_id = e.chunk_id
                LEFT JOIN documents d ON d.doc_id = e.doc_id
                LEFT JOIN document_revisions dr ON dr.revision_id = e.revision_id
                WHERE c.chunk_id IS NULL OR d.doc_id IS NULL OR dr.revision_id IS NULL
                """,
            ),
            "ocr_current_revision_embeddings": _count(
                connection,
                """
                SELECT COUNT(*) AS count
                FROM embeddings e
                JOIN document_revisions dr ON dr.revision_id = e.revision_id
                WHERE dr.converter_name = 'ocr_sidecar'
                """,
            ),
        }
    finally:
        connection.close()

    if ocr.status != "succeeded":
        raise RuntimeError(f"OCR setup failed for M7.1 gate: {ocr}")
    return summary


def _run_failure_gate(root: Path) -> dict[str, int]:
    vault = root / "vault"
    source = root / "note.md"
    root.mkdir(parents=True)
    source.write_text("# Note\nfts remains after embedding failure\n", encoding="utf-8")
    init_vault(vault)
    run_m3_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        before = search_chunks(connection, "fts remains")
        result = rebuild_vector_index(connection, adapter=FailingEmbeddingAdapter())
        after = search_chunks(connection, "fts remains")
        document = connection.execute("SELECT fts_status, embedding_status FROM documents").fetchone()
        errors = _count(connection, "SELECT COUNT(*) AS count FROM errors WHERE component = 'embedding_indexer'")
        reviews = _count(connection, "SELECT COUNT(*) AS count FROM review_items WHERE type = 'embedding_failed'")
    finally:
        connection.close()

    blocking = 0
    if before.result_count != 1 or after.result_count != 1:
        blocking = 1
    if document["fts_status"] != "indexed" or document["embedding_status"] != "failed":
        blocking = 1
    if result.failed_chunks == 0 or errors == 0 or reviews == 0:
        blocking = 1
    return {
        "embedding_failures_blocking_fts": blocking,
        "embedding_failure_errors": errors,
        "embedding_failure_reviews": reviews,
    }


def _run_orphan_detection_gate(root: Path) -> dict[str, int]:
    vault = root / "vault"
    root.mkdir(parents=True)
    init_vault(vault)
    raw_connection = sqlite3.connect(vault / ".indbase" / "db.sqlite")
    try:
        raw_connection.execute(
            """
            INSERT INTO embeddings(
              embedding_id, chunk_id, doc_id, revision_id, provider, model,
              dimension, vector_ref, content_hash, status, created_at, updated_at
            )
            VALUES ('embedding_orphan_gate', 'chunk_missing', 'doc_missing', 'rev_missing',
                    'local', 'hash-v1', 8, '{"vector":[0]}', 'sha256:missing',
                    'indexed', ?, ?)
            """,
            (utc_now_iso(), utc_now_iso()),
        )
        raw_connection.commit()
    finally:
        raw_connection.close()

    codes = {finding.code for finding in run_doctor(vault).findings}
    return {"orphan_embedding_doctor_detected": 1 if "orphan_embedding" in codes else 0}


def _count(connection, sql: str) -> int:
    return int(connection.execute(sql).fetchone()["count"] or 0)


if __name__ == "__main__":
    main()
