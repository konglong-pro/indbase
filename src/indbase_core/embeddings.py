"""Deterministic embedding indexing for M7.1."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3

from indbase_core.errors import record_error
from indbase_core.ids import new_prefixed_id
from indbase_core.reviews import create_review_item
from indbase_core.tasks import add_task_event, create_task, finish_task, start_task
from indbase_core.time import utc_now_iso


DEFAULT_EMBEDDING_PROVIDER = "local"
DEFAULT_EMBEDDING_MODEL = "hash-v1"
DEFAULT_EMBEDDING_DIMENSION = 8


@dataclass(frozen=True)
class EmbeddingIndexFailure:
    doc_id: str
    chunk_id: str
    reason: str
    error_id: str


@dataclass(frozen=True)
class VectorRebuildResult:
    task_id: str
    provider: str
    model: str
    dimension: int
    current_chunks: int
    embedded_chunks: int
    skipped_archived_chunks: int
    skipped_old_revision_chunks: int
    skipped_source_shells: int
    failed_chunks: int
    failures: tuple[EmbeddingIndexFailure, ...]


class DeterministicEmbeddingAdapter:
    """Small local adapter used for deterministic tests and dogfood scaffolding."""

    provider = DEFAULT_EMBEDDING_PROVIDER
    model = DEFAULT_EMBEDDING_MODEL
    dimension = DEFAULT_EMBEDDING_DIMENSION

    def embed(self, text: str) -> tuple[float, ...]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        values: list[float] = []
        for index in range(self.dimension):
            raw = int.from_bytes(digest[index * 4 : index * 4 + 4], "big", signed=False)
            values.append(round((raw / 0xFFFFFFFF) * 2 - 1, 6))
        return tuple(values)


def rebuild_vector_index(
    connection: sqlite3.Connection,
    *,
    adapter: DeterministicEmbeddingAdapter | None = None,
) -> VectorRebuildResult:
    """Rebuild deterministic vectors for active current chunks only."""
    embedding_adapter = adapter or DeterministicEmbeddingAdapter()
    provider = str(embedding_adapter.provider)
    model = str(embedding_adapter.model)
    dimension = int(embedding_adapter.dimension)
    task_id = create_task(
        connection,
        "vector_index_rebuild",
        input_data={"provider": provider, "model": model, "dimension": dimension},
    )
    start_task(connection, task_id)
    add_task_event(
        connection,
        task_id,
        "vector_rebuild_started",
        "Vector index rebuild started.",
        {"provider": provider, "model": model, "dimension": dimension},
    )

    now = utc_now_iso()
    current_chunks = _active_current_chunks(connection)
    connection.execute(
        """
        UPDATE documents
        SET embedding_status = 'indexing', updated_at = ?
        WHERE doc_id IN (
          SELECT DISTINCT d.doc_id
          FROM documents d
          JOIN chunks c ON c.doc_id = d.doc_id
          WHERE d.status = 'active'
            AND d.deleted_at IS NULL
            AND d.current_revision_id = c.revision_id
            AND c.is_current = 1
            AND c.deleted_at IS NULL
        )
        """,
        (now,),
    )
    connection.execute("DELETE FROM embeddings WHERE provider = ? AND model = ?", (provider, model))

    embedded_chunks = 0
    failures: list[EmbeddingIndexFailure] = []
    failed_doc_ids: set[str] = set()
    indexed_doc_ids: set[str] = set()
    for chunk in current_chunks:
        try:
            vector = tuple(float(value) for value in embedding_adapter.embed(str(chunk["text"])))
            if len(vector) != dimension:
                raise ValueError(f"Embedding dimension mismatch: expected {dimension}, got {len(vector)}")
            connection.execute(
                """
                INSERT INTO embeddings(
                  embedding_id, chunk_id, doc_id, revision_id, provider, model,
                  dimension, vector_ref, content_hash, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'indexed', ?, ?)
                """,
                (
                    new_prefixed_id("embedding"),
                    chunk["chunk_id"],
                    chunk["doc_id"],
                    chunk["revision_id"],
                    provider,
                    model,
                    dimension,
                    _vector_ref(vector),
                    chunk["content_hash"],
                    utc_now_iso(),
                    utc_now_iso(),
                ),
            )
            embedded_chunks += 1
            indexed_doc_ids.add(str(chunk["doc_id"]))
        except Exception as exc:
            failure = _record_embedding_failure(
                connection,
                task_id=task_id,
                doc_id=str(chunk["doc_id"]),
                chunk_id=str(chunk["chunk_id"]),
                provider=provider,
                model=model,
                exc=exc,
            )
            failures.append(failure)
            failed_doc_ids.add(str(chunk["doc_id"]))

    for doc_id in sorted(indexed_doc_ids - failed_doc_ids):
        connection.execute(
            """
            UPDATE documents
            SET embedding_status = 'indexed', updated_at = ?
            WHERE doc_id = ?
            """,
            (utc_now_iso(), doc_id),
        )
    for doc_id in sorted(failed_doc_ids):
        connection.execute(
            """
            UPDATE documents
            SET embedding_status = 'failed', updated_at = ?
            WHERE doc_id = ?
            """,
            (utc_now_iso(), doc_id),
        )

    stale_count = mark_stale_embeddings(connection, provider=provider, model=model)
    result_data = {
        "provider": provider,
        "model": model,
        "dimension": dimension,
        "current_chunks": len(current_chunks),
        "embedded_chunks": embedded_chunks,
        "failed_chunks": len(failures),
        "stale_embeddings": stale_count,
    }
    status = "failed" if failures else "succeeded"
    finish_task(connection, task_id, status, result_data=result_data)
    add_task_event(
        connection,
        task_id,
        "vector_rebuild_finished",
        "Vector index rebuild finished.",
        result_data,
    )
    connection.commit()
    return VectorRebuildResult(
        task_id=task_id,
        provider=provider,
        model=model,
        dimension=dimension,
        current_chunks=len(current_chunks),
        embedded_chunks=embedded_chunks,
        skipped_archived_chunks=_embedded_archived_chunks(connection, provider, model),
        skipped_old_revision_chunks=_embedded_old_revision_chunks(connection, provider, model),
        skipped_source_shells=_embedded_source_shells(connection, provider, model),
        failed_chunks=len(failures),
        failures=tuple(failures),
    )


def mark_stale_embeddings(
    connection: sqlite3.Connection,
    *,
    provider: str = DEFAULT_EMBEDDING_PROVIDER,
    model: str = DEFAULT_EMBEDDING_MODEL,
) -> int:
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE embeddings
        SET status = 'stale', updated_at = ?
        WHERE provider = ?
          AND model = ?
          AND deleted_at IS NULL
          AND (
            NOT EXISTS (
              SELECT 1
              FROM chunks c
              JOIN documents d ON d.doc_id = c.doc_id
              WHERE c.chunk_id = embeddings.chunk_id
                AND c.doc_id = embeddings.doc_id
                AND c.revision_id = embeddings.revision_id
                AND c.content_hash = embeddings.content_hash
                AND c.deleted_at IS NULL
                AND d.status = 'active'
                AND d.deleted_at IS NULL
                AND d.current_revision_id = c.revision_id
                AND c.is_current = 1
            )
          )
        """,
        (now, provider, model),
    )
    return int(connection.execute("SELECT changes() AS count").fetchone()["count"] or 0)


def _active_current_chunks(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT c.chunk_id, c.doc_id, c.revision_id, c.text, c.content_hash
            FROM chunks c
            JOIN documents d ON d.doc_id = c.doc_id
            WHERE d.status = 'active'
              AND d.deleted_at IS NULL
              AND d.ingest_status = 'revisioned'
              AND d.current_revision_id = c.revision_id
              AND c.is_current = 1
              AND c.deleted_at IS NULL
            ORDER BY d.created_at, c.sequence, c.chunk_id
            """
        )
    )


def _record_embedding_failure(
    connection: sqlite3.Connection,
    *,
    task_id: str,
    doc_id: str,
    chunk_id: str,
    provider: str,
    model: str,
    exc: Exception,
) -> EmbeddingIndexFailure:
    reason = type(exc).__name__
    message = f"Embedding failed for chunk {chunk_id}: {exc}"
    error_id = record_error(
        connection,
        task_id=task_id,
        component="embedding_indexer",
        error_type=reason,
        message=message,
        user_message="Chunk could not be indexed for vector search.",
        retryable=True,
        payload={"doc_id": doc_id, "chunk_id": chunk_id, "provider": provider, "model": model},
    )
    _create_review_once(
        connection,
        review_type="embedding_failed",
        target_type="chunk",
        target_id=chunk_id,
        reason=message,
    )
    return EmbeddingIndexFailure(doc_id=doc_id, chunk_id=chunk_id, reason=reason, error_id=error_id)


def _create_review_once(
    connection: sqlite3.Connection,
    *,
    review_type: str,
    target_type: str,
    target_id: str,
    reason: str,
) -> None:
    existing = connection.execute(
        """
        SELECT review_id
        FROM review_items
        WHERE type = ?
          AND target_type = ?
          AND target_id = ?
          AND reason = ?
          AND status = 'pending'
        LIMIT 1
        """,
        (review_type, target_type, target_id, reason),
    ).fetchone()
    if existing is not None:
        return
    create_review_item(
        connection,
        review_type=review_type,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        priority=35,
    )


def _embedded_archived_chunks(connection: sqlite3.Connection, provider: str, model: str) -> int:
    return _scalar_count(
        connection,
        """
        SELECT COUNT(*) AS count
        FROM embeddings e
        JOIN documents d ON d.doc_id = e.doc_id
        WHERE e.provider = ?
          AND e.model = ?
          AND d.status = 'archived'
          AND e.deleted_at IS NULL
        """,
        (provider, model),
    )


def _embedded_old_revision_chunks(connection: sqlite3.Connection, provider: str, model: str) -> int:
    return _scalar_count(
        connection,
        """
        SELECT COUNT(*) AS count
        FROM embeddings e
        JOIN documents d ON d.doc_id = e.doc_id
        WHERE e.provider = ?
          AND e.model = ?
          AND d.current_revision_id != e.revision_id
          AND e.deleted_at IS NULL
        """,
        (provider, model),
    )


def _embedded_source_shells(connection: sqlite3.Connection, provider: str, model: str) -> int:
    return _scalar_count(
        connection,
        """
        SELECT COUNT(*) AS count
        FROM embeddings e
        JOIN documents d ON d.doc_id = e.doc_id
        WHERE e.provider = ?
          AND e.model = ?
          AND d.current_revision_id IS NULL
          AND e.deleted_at IS NULL
        """,
        (provider, model),
    )


def _scalar_count(connection: sqlite3.Connection, sql: str, params: tuple[object, ...]) -> int:
    return int(connection.execute(sql, params).fetchone()["count"] or 0)


def _vector_ref(vector: tuple[float, ...]) -> str:
    return json.dumps({"vector": list(vector)}, sort_keys=True, separators=(",", ":"))
