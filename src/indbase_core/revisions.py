"""Immutable source Markdown revision writer."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3

from indbase_core.conversion import hash_markdown
from indbase_core.errors import record_error
from indbase_core.ids import revision_id
from indbase_core.paths import vault_paths
from indbase_core.time import utc_now_iso


@dataclass(frozen=True)
class WrittenRevision:
    ingest_item_id: str
    doc_id: str
    revision_id: str
    sequence: int
    markdown_path: str


@dataclass(frozen=True)
class RevisionWriteResult:
    ingest_id: str
    written_revisions: tuple[WrittenRevision, ...]
    failed_items: int


def write_revisions_for_converted_sources(
    connection: sqlite3.Connection,
    vault_path: Path | str,
    ingest_id: str,
) -> RevisionWriteResult:
    paths = vault_paths(vault_path)
    rows = connection.execute(
        """
        SELECT ii.ingest_item_id, ii.doc_id, d.title, d.original_title, d.filename_slug,
               d.status, d.source_type, d.source_uri, d.normalized_source_uri,
               d.source_hash, d.original_path, d.language, d.category_id,
               d.quality_status, d.quality_signals_json, d.needs_review,
               cr.converter_run_id, cr.converter_name, cr.converter_version,
               cr.output_hash, cr.quality_signals_json AS converter_quality_signals_json
        FROM ingest_items ii
        JOIN documents d ON d.doc_id = ii.doc_id
        JOIN converter_runs cr ON cr.doc_id = d.doc_id
        WHERE ii.ingest_id = ?
          AND ii.status = 'running'
          AND d.ingest_status = 'converted'
          AND cr.status = 'succeeded'
          AND cr.revision_id IS NULL
          AND cr.created_at = (
            SELECT MAX(created_at)
            FROM converter_runs latest
            WHERE latest.doc_id = d.doc_id
              AND latest.status = 'succeeded'
              AND latest.revision_id IS NULL
          )
        ORDER BY ii.source_uri
        """,
        (ingest_id,),
    ).fetchall()

    written: list[WrittenRevision] = []
    failed = 0
    for row in rows:
        try:
            written.append(_write_one_revision(connection, paths.root, row))
        except Exception as exc:
            failed += 1
            _mark_revision_failed(connection, row["ingest_item_id"], row["doc_id"], exc)

    _update_ingest_run_after_revision_write(connection, ingest_id, failed)
    connection.commit()
    return RevisionWriteResult(
        ingest_id=ingest_id,
        written_revisions=tuple(written),
        failed_items=failed,
    )


def write_revision_for_converter_run(
    connection: sqlite3.Connection,
    vault_path: Path | str,
    converter_run_id: str,
) -> WrittenRevision:
    row = connection.execute(
        """
        SELECT ii.ingest_item_id, ii.doc_id, d.title, d.original_title, d.filename_slug,
               d.status, d.source_type, d.source_uri, d.normalized_source_uri,
               d.source_hash, d.original_path, d.language, d.category_id,
               d.quality_status, d.quality_signals_json, d.needs_review,
               cr.converter_run_id, cr.converter_name, cr.converter_version,
               cr.output_hash, cr.quality_signals_json AS converter_quality_signals_json
        FROM converter_runs cr
        JOIN documents d ON d.doc_id = cr.doc_id
        JOIN ingest_items ii ON ii.doc_id = d.doc_id
        WHERE cr.converter_run_id = ?
          AND cr.revision_id IS NULL
          AND cr.status = 'pending_review'
          AND ii.status = 'pending_review'
        ORDER BY ii.updated_at DESC, ii.created_at DESC
        LIMIT 1
        """,
        (converter_run_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Pending conversion candidate not found: {converter_run_id}")
    written = _write_one_revision(connection, vault_path, row)
    connection.commit()
    return written


def _write_one_revision(
    connection: sqlite3.Connection,
    vault_root: Path,
    row: sqlite3.Row,
) -> WrittenRevision:
    paths = vault_paths(vault_root)
    converter_run_id = row["converter_run_id"]
    candidate_path = paths.converter_candidate_path(converter_run_id)
    body = candidate_path.read_text(encoding="utf-8")
    content_hash = hash_markdown(body)
    if content_hash != row["output_hash"]:
        raise ValueError(
            f"Converter candidate hash mismatch for {converter_run_id}: "
            f"{content_hash} != {row['output_hash']}"
        )

    sequence = _next_revision_sequence(connection, row["doc_id"])
    rev_id = revision_id(row["doc_id"], sequence)
    markdown_path = paths.source_markdown_path(row["doc_id"], row["filename_slug"], sequence)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_rel = paths.relative_to_vault(markdown_path)
    now = utc_now_iso()
    final_markdown = _render_source_markdown(
        row=row,
        revision_id_value=rev_id,
        canonical_path=markdown_rel,
        content_hash=content_hash,
        body=body,
        ingested_at=now,
    )
    _write_immutable_file(markdown_path, final_markdown)

    connection.execute(
        """
        INSERT INTO document_revisions(
          revision_id, doc_id, sequence, markdown_path, content_hash,
          converter_name, converter_version, chunk_strategy, text_length,
          chunk_count, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, 0, ?, ?)
        """,
        (
            rev_id,
            row["doc_id"],
            sequence,
            markdown_rel,
            content_hash,
            row["converter_name"],
            row["converter_version"],
            len(body),
            now,
            now,
        ),
    )
    connection.execute(
        """
        UPDATE converter_runs
        SET revision_id = ?, updated_at = ?
        WHERE converter_run_id = ?
        """,
        (rev_id, now, converter_run_id),
    )
    connection.execute(
        """
        UPDATE documents
        SET current_revision_id = ?, canonical_path = ?, ingest_status = 'revisioned',
            updated_at = ?
        WHERE doc_id = ?
        """,
        (rev_id, markdown_rel, now, row["doc_id"]),
    )
    return WrittenRevision(
        ingest_item_id=row["ingest_item_id"],
        doc_id=row["doc_id"],
        revision_id=rev_id,
        sequence=sequence,
        markdown_path=markdown_rel,
    )


def _render_source_markdown(
    *,
    row: sqlite3.Row,
    revision_id_value: str,
    canonical_path: str,
    content_hash: str,
    body: str,
    ingested_at: str,
) -> str:
    quality_signals = _json_or_empty_object(row["quality_signals_json"])
    frontmatter = {
        "schema_version": "indbase.source.v1",
        "type": "source_document",
        "status": row["status"],
        "doc_id": row["doc_id"],
        "revision_id": revision_id_value,
        "title": row["title"],
        "original_title": row["original_title"],
        "filename_slug": row["filename_slug"],
        "source_type": row["source_type"],
        "source_uri": row["source_uri"],
        "normalized_source_uri": row["normalized_source_uri"],
        "original_path": row["original_path"],
        "canonical_path": canonical_path,
        "source_hash": row["source_hash"],
        "content_hash": content_hash,
        "converter": row["converter_name"],
        "converter_version": row["converter_version"],
        "language": row["language"],
        "category_id": row["category_id"],
        "quality_status": row["quality_status"],
        "quality_signals": quality_signals,
        "chunk_count": 0,
        "fts_indexed": False,
        "embedding_indexed": False,
        "needs_review": bool(row["needs_review"]),
        "review_reasons": [],
        "ingested_at": ingested_at,
    }
    return "---\n" + "\n".join(_yaml_line(key, value) for key, value in frontmatter.items()) + "\n---\n\n" + body


def _write_immutable_file(path: Path, content: str) -> None:
    if path.exists():
        raise FileExistsError(f"Revision Markdown already exists: {path}")
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(path)


def _next_revision_sequence(connection: sqlite3.Connection, doc_id: str) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM document_revisions WHERE doc_id = ?",
        (doc_id,),
    ).fetchone()
    return int(row["next_sequence"])


def _mark_revision_failed(
    connection: sqlite3.Connection,
    ingest_item_id: str,
    doc_id: str,
    exc: Exception,
) -> None:
    now = utc_now_iso()
    error_id = record_error(
        connection,
        component="revision_writer",
        error_type=type(exc).__name__,
        message=str(exc),
        user_message="Failed to write immutable source Markdown revision.",
        retryable=True,
        payload={"ingest_item_id": ingest_item_id, "doc_id": doc_id},
    )
    connection.execute(
        """
        UPDATE ingest_items
        SET status = 'failed', error_id = ?, updated_at = ?, finished_at = ?
        WHERE ingest_item_id = ?
        """,
        (error_id, now, now, ingest_item_id),
    )
    connection.execute(
        """
        UPDATE documents
        SET ingest_status = 'failed', fts_status = 'failed', updated_at = ?
        WHERE doc_id = ?
        """,
        (now, doc_id),
    )


def _update_ingest_run_after_revision_write(
    connection: sqlite3.Connection,
    ingest_id: str,
    failed_count: int,
) -> None:
    if failed_count == 0:
        connection.execute(
            "UPDATE ingest_runs SET updated_at = ? WHERE ingest_id = ?",
            (utc_now_iso(), ingest_id),
        )
        return
    connection.execute(
        """
        UPDATE ingest_runs
        SET failed_items = COALESCE(failed_items, 0) + ?,
            status = 'completed_with_issues',
            updated_at = ?
        WHERE ingest_id = ?
        """,
        (failed_count, utc_now_iso(), ingest_id),
    )


def _yaml_line(key: str, value: object) -> str:
    if isinstance(value, bool):
        rendered = "true" if value else "false"
    elif isinstance(value, int):
        rendered = str(value)
    elif value is None:
        rendered = "null"
    elif isinstance(value, (dict, list)):
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        rendered = json.dumps(str(value), ensure_ascii=False)
    return f"{key}: {rendered}"


def _json_or_empty_object(value: str | None) -> dict[str, object]:
    if not value:
        return {}
    parsed = json.loads(value)
    if isinstance(parsed, dict):
        return parsed
    return {}
