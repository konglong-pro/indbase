"""Read-only ingest probe helpers for consoler preview."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from indbase_core.source_inspector import SourceInspection, inspect_source


def _lookup_existing_by_hash(
    connection: sqlite3.Connection,
    source_hash: str,
) -> str | None:
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


def _lookup_existing_by_uri(
    connection: sqlite3.Connection,
    normalized_source_uri: str,
) -> str | None:
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


def probe_ingest_file(vault_path: Path, source_path: Path) -> dict[str, Any]:
    inspection = inspect_source(source_path)
    db_path = vault_path / ".indbase" / "db.sqlite"
    duplicate_by_hash_doc_id: str | None = None
    duplicate_by_uri_doc_id: str | None = None
    if db_path.exists():
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            if inspection.is_supported:
                duplicate_by_hash_doc_id = _lookup_existing_by_hash(connection, inspection.source_hash)
                duplicate_by_uri_doc_id = _lookup_existing_by_uri(
                    connection, inspection.normalized_source_uri
                )
        finally:
            connection.close()

    return {
        "preview_kind": "probe_readonly",
        "summary": (
            f"Probe preview for {source_path.name} → vault {vault_path.name} "
            f"({'supported' if inspection.is_supported else 'unsupported'})"
        ),
        "inspection": _inspection_dict(inspection),
        "duplicates": {
            "by_source_hash": duplicate_by_hash_doc_id,
            "by_normalized_source_uri": duplicate_by_uri_doc_id,
            "is_duplicate": duplicate_by_hash_doc_id is not None or duplicate_by_uri_doc_id is not None,
        },
    }


def _inspection_dict(inspection: SourceInspection) -> dict[str, Any]:
    return {
        "source_uri": inspection.source_uri,
        "normalized_source_uri": inspection.normalized_source_uri,
        "original_filename": inspection.original_filename,
        "original_ext": inspection.original_ext,
        "mime_type": inspection.mime_type,
        "size_bytes": inspection.size_bytes,
        "source_hash": inspection.source_hash,
        "tier": inspection.tier,
        "is_supported": inspection.is_supported,
    }
