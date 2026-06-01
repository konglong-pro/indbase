"""Agent-owned artifact view construction for indbase logical URIs."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from consoler_agent_sdk import AgentError, json_block, markdown_block, table_block


ARTIFACT_VIEW_TITLES = {
    "indbase.ingest_run": "Ingest run",
    "indbase.document": "Document",
    "indbase.document_revision": "Document revision",
}


def build_indbase_artifact_view(
    *,
    artifact_uri: str,
    kind: str,
    block_id: str,
    action_id: str,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    if not artifact_uri.startswith("indbase://"):
        raise AgentError("artifact.not_found", f"Unsupported artifact URI: {artifact_uri}")

    vault_path = _vault_path(metadata)
    parsed = urlparse(artifact_uri)
    entity_kind = parsed.netloc
    entity_id = parsed.path.lstrip("/")
    if not entity_kind or not entity_id:
        raise AgentError("artifact.not_found", f"Malformed indbase artifact URI: {artifact_uri}")

    if kind == "indbase.ingest_run":
        blocks = _view_ingest_run(vault_path, entity_id)
    elif kind == "indbase.document":
        blocks = _view_document(vault_path, entity_id)
    elif kind == "indbase.document_revision":
        blocks = _view_document_revision(vault_path, entity_id)
    else:
        raise AgentError("artifact.not_found", f"Unsupported artifact kind: {kind}")

    return {
        "artifact_uri": artifact_uri,
        "kind": kind,
        "title": ARTIFACT_VIEW_TITLES[kind],
        "metadata": {
            "action_id": action_id,
            "block_id": block_id,
            "entity_kind": entity_kind,
            "entity_id": entity_id,
            **(metadata or {}),
        },
        "blocks": blocks,
    }


def _vault_path(metadata: dict[str, Any] | None) -> Path:
    if not metadata or not metadata.get("vault_path"):
        raise AgentError(
            "artifact.vault_required",
            "vault_path is required in artifact metadata for retrieval",
        )
    return Path(str(metadata["vault_path"])).expanduser()


def _connect_ro(vault_path: Path) -> sqlite3.Connection:
    db_path = vault_path / ".indbase" / "db.sqlite"
    if not db_path.is_file():
        raise AgentError("artifact.not_found", f"Vault database not found: {db_path}")
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _view_ingest_run(vault_path: Path, ingest_id: str) -> list[dict[str, Any]]:
    connection = _connect_ro(vault_path)
    try:
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
        if row is None:
            raise AgentError("artifact.not_found", f"Ingest run not found: {ingest_id}")

        items = connection.execute(
            """
            SELECT ingest_item_id, doc_id, source_uri, normalized_source_uri, status, finished_at
            FROM ingest_items
            WHERE ingest_id = ?
            ORDER BY created_at
            """,
            (ingest_id,),
        ).fetchall()
    finally:
        connection.close()

    summary = {key: row[key] for key in row.keys()}
    return [
        markdown_block(f"# Ingest run `{ingest_id}`", title="Ingest run"),
        table_block(
            ["field", "value"],
            [[key, str(summary[key]) if summary[key] is not None else ""] for key in summary.keys()],
            title="Run summary",
        ),
        table_block(
            ["ingest_item_id", "doc_id", "status", "source_uri"],
            [
                [
                    str(row["ingest_item_id"]),
                    str(row["doc_id"] or ""),
                    str(row["status"] or ""),
                    str(row["source_uri"] or ""),
                ]
                for row in items
            ],
            title="Ingest items",
        ),
        json_block(
            {"ingest_run": summary, "ingest_items": [dict(row) for row in items]},
            title="Raw ingest run",
        ),
    ]


def _view_document(vault_path: Path, doc_id: str) -> list[dict[str, Any]]:
    connection = _connect_ro(vault_path)
    try:
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
        if row is None:
            raise AgentError("artifact.not_found", f"Document not found: {doc_id}")
        summary = {key: row[key] for key in row.keys()}
    finally:
        connection.close()

    return [
        markdown_block(f"# Document `{doc_id}`", title="Document"),
        table_block(
            ["field", "value"],
            [[key, str(summary[key]) if summary[key] is not None else ""] for key in summary.keys()],
            title="Document summary",
        ),
        json_block(summary, title="Raw document"),
    ]


def _view_document_revision(vault_path: Path, revision_id: str) -> list[dict[str, Any]]:
    connection = _connect_ro(vault_path)
    try:
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
        if row is None:
            raise AgentError("artifact.not_found", f"Document revision not found: {revision_id}")
        summary = {key: row[key] for key in row.keys()}
    finally:
        connection.close()

    return [
        markdown_block(f"# Revision `{revision_id}`", title="Document revision"),
        table_block(
            ["field", "value"],
            [[key, str(summary[key]) if summary[key] is not None else ""] for key in summary.keys()],
            title="Revision summary",
        ),
        json_block(summary, title="Raw revision"),
    ]


def ingest_artifact_metadata_with_vault(
    result: Any,
    snapshot: dict[str, Any],
    vault_path: Path,
) -> dict[str, Any]:
    from indbase_agent.ingest_state_snapshot import ingest_artifact_metadata

    metadata = ingest_artifact_metadata(result, snapshot)
    metadata["vault_path"] = str(vault_path)
    return metadata
