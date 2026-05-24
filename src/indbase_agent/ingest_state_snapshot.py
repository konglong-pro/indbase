"""Read-only vault state snapshots for ingest result diff/artifact blocks."""

from __future__ import annotations

import difflib
import json
import sqlite3
from pathlib import Path
from typing import Any

from indbase_core.source_inspector import SourceInspection, inspect_source



def capture_ingest_state_snapshot(
    vault_path: Path,
    source_path: Path,
    *,
    ingest_id: str | None = None,
) -> dict[str, Any]:
    inspection = inspect_source(source_path)
    snapshot: dict[str, Any] = {
        "source": _source_summary(inspection),
        "existing_document": None,
        "ingest_run": None,
        "ingest_items": [],
        "document": None,
        "current_revision": None,
    }

    db_path = vault_path / ".indbase" / "db.sqlite"
    if not db_path.exists():
        return snapshot

    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        snapshot["existing_document"] = _existing_document_summary(connection, inspection)
        if ingest_id:
            snapshot["ingest_run"] = _load_ingest_run(connection, ingest_id)
            snapshot["ingest_items"] = _load_ingest_items(connection, ingest_id)
            doc_id = _primary_doc_id(snapshot["ingest_items"])
            if doc_id:
                snapshot["document"] = _load_document_summary(connection, doc_id)
                revision_id = (
                    snapshot["document"]["current_revision_id"]
                    if snapshot["document"]
                    else None
                )
                if revision_id:
                    snapshot["current_revision"] = _load_revision_summary(
                        connection, str(revision_id)
                    )
    finally:
        connection.close()

    return snapshot


def vault_state_unified_diff(before: dict[str, Any], after: dict[str, Any]) -> str:
    before_lines = _stable_json_lines(before)
    after_lines = _stable_json_lines(after)
    return "".join(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile="before ingest",
            tofile="after ingest",
        )
    )


def ingest_artifact_metadata(
    result: Any,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    ingest_run = snapshot.get("ingest_run") or {}
    document = snapshot.get("document") or {}
    revision = snapshot.get("current_revision") or {}
    return {
        "status": result.status,
        "task_id": result.task_id,
        "total_items": result.total_items,
        "succeeded_items": result.succeeded_items,
        "failed_items": result.failed_items,
        "indexed_documents": result.indexed_documents,
        "indexed_chunks": result.indexed_chunks,
        "ingest_run_status": ingest_run.get("status"),
        "doc_id": document.get("doc_id"),
        "current_revision_id": document.get("current_revision_id"),
        "markdown_path": revision.get("markdown_path"),
    }


def _stable_json_lines(payload: dict[str, Any]) -> list[str]:
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    if not text:
        return ["{}\n"]
    return [f"{line}\n" for line in text.splitlines()]


def _source_summary(inspection: SourceInspection) -> dict[str, Any]:
    return {
        "normalized_source_uri": inspection.normalized_source_uri,
        "original_filename": inspection.original_filename,
        "source_hash": inspection.source_hash,
        "size_bytes": inspection.size_bytes,
        "tier": inspection.tier,
        "is_supported": inspection.is_supported,
    }


def _lookup_doc_id_by_hash(connection: sqlite3.Connection, source_hash: str) -> str | None:
    row = connection.execute(
        """
        SELECT doc_id
        FROM source_files
        WHERE source_hash = ?
        ORDER BY created_at
        LIMIT 1
        """,
        (source_hash,),
    ).fetchone()
    return str(row["doc_id"]) if row else None


def _lookup_doc_id_by_uri(connection: sqlite3.Connection, normalized_source_uri: str) -> str | None:
    row = connection.execute(
        """
        SELECT doc_id
        FROM documents
        WHERE normalized_source_uri = ?
          AND deleted_at IS NULL
        ORDER BY created_at
        LIMIT 1
        """,
        (normalized_source_uri,),
    ).fetchone()
    return str(row["doc_id"]) if row else None


def _existing_document_summary(
    connection: sqlite3.Connection,
    inspection: SourceInspection,
) -> dict[str, Any] | None:
    if not inspection.is_supported:
        return None
    by_hash = _lookup_doc_id_by_hash(connection, inspection.source_hash)
    by_uri = _lookup_doc_id_by_uri(connection, inspection.normalized_source_uri)
    doc_id = by_hash or by_uri
    if not doc_id:
        return None
    return {
        "doc_id": doc_id,
        "matched_by_source_hash": by_hash is not None,
        "matched_by_normalized_source_uri": by_uri is not None,
    }


def _load_ingest_run(connection: sqlite3.Connection, ingest_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT ingest_id, task_id, source_kind, status, total_items, succeeded_items,
               failed_items, unsupported_items, duplicate_items, review_items_count,
               created_at, finished_at
        FROM ingest_runs
        WHERE ingest_id = ?
        """,
        (ingest_id,),
    ).fetchone()
    return _row_dict(row)


def _load_ingest_items(connection: sqlite3.Connection, ingest_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT ingest_item_id, doc_id, source_uri, normalized_source_uri, status, finished_at
        FROM ingest_items
        WHERE ingest_id = ?
        ORDER BY created_at
        """,
        (ingest_id,),
    ).fetchall()
    return [_row_dict(row) for row in rows]


def _primary_doc_id(ingest_items: list[dict[str, Any]]) -> str | None:
    for item in ingest_items:
        if item.get("status") == "succeeded" and item.get("doc_id"):
            return str(item["doc_id"])
    for item in ingest_items:
        if item.get("doc_id"):
            return str(item["doc_id"])
    return None


def _load_document_summary(connection: sqlite3.Connection, doc_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT doc_id, current_revision_id, title, normalized_source_uri, source_hash,
               canonical_path, ingest_status, fts_status, status
        FROM documents
        WHERE doc_id = ?
          AND deleted_at IS NULL
        """,
        (doc_id,),
    ).fetchone()
    return _row_dict(row)


def _load_revision_summary(
    connection: sqlite3.Connection,
    revision_id: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT revision_id, doc_id, sequence, markdown_path, content_hash,
               chunk_count, text_length
        FROM document_revisions
        WHERE revision_id = ?
          AND deleted_at IS NULL
        """,
        (revision_id,),
    ).fetchone()
    return _row_dict(row)


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}
