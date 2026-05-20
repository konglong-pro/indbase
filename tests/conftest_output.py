"""Shared helpers for transition output tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from indbase_core.paths import vault_paths


def insert_minimal_document(connection: sqlite3.Connection, vault: Path, *, body: str) -> str:
    doc_id = "doc_20250101_abc123"
    revision_id_value = "rev_doc_20250101_abc123_0001"
    paths = vault_paths(vault)
    markdown_path = paths.source_markdown_path(doc_id, "demo", 1)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown = (
        f"---\nschema_version: indbase.source.v1\ntype: source_document\n"
        f"doc_id: {doc_id}\nrevision_id: {revision_id_value}\ntitle: Demo\n---\n\n{body}"
    )
    markdown_path.write_text(markdown, encoding="utf-8")
    rel = paths.relative_to_vault(markdown_path)
    connection.execute(
        """
        INSERT INTO documents (
          doc_id, title, filename_slug, status, ingest_status, current_revision_id,
          canonical_path, created_at, updated_at
        ) VALUES (?, 'Demo', 'demo', 'active', 'revisioned', ?, ?, '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')
        """,
        (doc_id, revision_id_value, rel),
    )
    connection.execute(
        """
        INSERT INTO document_revisions (
          revision_id, doc_id, sequence, markdown_path, content_hash,
          converter_name, converter_version, promotion_status, created_at, updated_at
        ) VALUES (?, ?, 1, ?, 'hash', 'test', 'test', 'promoted', '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')
        """,
        (revision_id_value, doc_id, rel),
    )
    connection.commit()
    return doc_id
