"""P1 chunk locator carry-forward after normalize replacement."""

from __future__ import annotations

import json
import sqlite3

from indbase_core.ids import new_prefixed_id
from indbase_core.time import utc_now_iso


def record_normalize_locator_mappings(
    connection: sqlite3.Connection,
    *,
    output_run_id: str,
    doc_id: str,
    parent_revision_id: str,
    new_revision_id: str,
) -> None:
    old_chunks = _load_revision_chunks(connection, doc_id, parent_revision_id)
    new_chunks = _load_revision_chunks(connection, doc_id, new_revision_id)
    old_by_identity = {
        (_heading_key(row["heading_path_json"]), str(row["text"])): row for row in old_chunks
    }
    now = utc_now_iso()
    for new_row in new_chunks:
        key = (_heading_key(new_row["heading_path_json"]), str(new_row["text"]))
        old_row = old_by_identity.get(key)
        if old_row is None:
            continue
        locator_json = old_row["source_locator_json"]
        if locator_json:
            connection.execute(
                """
                UPDATE chunks
                SET source_locator_json = ?, updated_at = ?
                WHERE chunk_id = ?
                """,
                (locator_json, now, new_row["chunk_id"]),
            )
        connection.execute(
            """
            INSERT INTO output_sources (
              output_source_id, output_run_id, source_doc_id, source_revision_id,
              source_chunk_id, source_locator_json, mapping_confidence, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_prefixed_id("outsrc"),
                output_run_id,
                doc_id,
                new_revision_id,
                str(new_row["chunk_id"]),
                locator_json,
                "exact",
                now,
                now,
            ),
        )


def _load_revision_chunks(
    connection: sqlite3.Connection,
    doc_id: str,
    revision_id_value: str,
) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT chunk_id, heading_path_json, text, source_locator_json
            FROM chunks
            WHERE doc_id = ?
              AND revision_id = ?
              AND deleted_at IS NULL
            ORDER BY sequence, chunk_id
            """,
            (doc_id, revision_id_value),
        )
    )


def _heading_key(heading_path_json: object) -> str:
    if not heading_path_json:
        return "[]"
    if isinstance(heading_path_json, str):
        return heading_path_json
    return json.dumps(heading_path_json, ensure_ascii=False, sort_keys=True)
