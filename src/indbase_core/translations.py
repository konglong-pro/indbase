"""Translation output workflows for M9."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3

from indbase_core.errors import record_error
from indbase_core.ids import new_prefixed_id
from indbase_core.paths import vault_paths
from indbase_core.tasks import add_task_event, create_task, finish_task, start_task
from indbase_core.time import utc_now_iso


TRANSLATION_MODEL = "local/pseudo-translation-v1"
SELECTED_CHUNKS_PROMPT_VERSION = "m9.1"
FULL_DOCUMENT_PROMPT_VERSION = "m9.2"
SELECTED_CHUNKS_TRANSLATION_MODE = "selected_chunks"
FULL_DOCUMENT_TRANSLATION_MODE = "full_document"

# Backward-compatible names for the M9.1 selected-chunk workflow.
PROMPT_VERSION = SELECTED_CHUNKS_PROMPT_VERSION
TRANSLATION_MODE = SELECTED_CHUNKS_TRANSLATION_MODE


@dataclass(frozen=True)
class TranslationResult:
    translation_id: str
    execution_id: str
    task_id: str
    source_doc_id: str
    source_revision_id: str
    source_chunk_ids: tuple[str, ...]
    target_language: str
    output_path: str
    status: str


class DeterministicTranslationAdapter:
    """Local deterministic adapter for validating translation storage contracts."""

    model = TRANSLATION_MODEL

    def translate(self, text: str, *, source_language: str | None, target_language: str) -> str:
        compact = " ".join(text.split())
        return f"[{target_language}] {compact}"


def translate_selected_chunks(
    connection: sqlite3.Connection,
    vault_path: Path | str,
    *,
    doc_id: str,
    revision_id: str,
    chunk_ids: tuple[str, ...] | list[str],
    target_language: str,
    source_language: str | None = None,
    adapter: DeterministicTranslationAdapter | None = None,
) -> TranslationResult:
    """Translate selected current chunks and write a durable translation output."""
    target = _clean_language(target_language, field_name="target_language")
    source = _clean_language(source_language, field_name="source_language") if source_language else None
    selected_chunk_ids = _normalize_chunk_ids(chunk_ids)
    document = _load_translation_document(connection, doc_id)
    _validate_document_for_translation(document, revision_id)
    chunks = _load_selected_chunks(connection, doc_id=doc_id, revision_id=revision_id, chunk_ids=selected_chunk_ids)
    return _translate_current_chunks(
        connection,
        vault_path,
        document=document,
        revision_id=revision_id,
        chunks=chunks,
        target_language=target,
        source_language=source,
        adapter=adapter,
        translation_mode=SELECTED_CHUNKS_TRANSLATION_MODE,
        prompt_version=SELECTED_CHUNKS_PROMPT_VERSION,
        task_type="translation_selected_chunks",
        execution_type="translation.selected_chunks",
        started_message="Selected-chunk translation started.",
        finished_message="Selected-chunk translation finished.",
        output_description="This output is a deterministic local translation scaffold for selected source chunks.",
    )


def translate_full_document(
    connection: sqlite3.Connection,
    vault_path: Path | str,
    *,
    doc_id: str,
    revision_id: str,
    target_language: str,
    source_language: str | None = None,
    adapter: DeterministicTranslationAdapter | None = None,
) -> TranslationResult:
    """Translate all current chunks for a document revision into one output."""
    target = _clean_language(target_language, field_name="target_language")
    source = _clean_language(source_language, field_name="source_language") if source_language else None
    document = _load_translation_document(connection, doc_id)
    _validate_document_for_translation(document, revision_id)
    chunks = _load_current_revision_chunks(connection, doc_id=doc_id, revision_id=revision_id)
    return _translate_current_chunks(
        connection,
        vault_path,
        document=document,
        revision_id=revision_id,
        chunks=chunks,
        target_language=target,
        source_language=source,
        adapter=adapter,
        translation_mode=FULL_DOCUMENT_TRANSLATION_MODE,
        prompt_version=FULL_DOCUMENT_PROMPT_VERSION,
        task_type="translation_full_document",
        execution_type="translation.full_document",
        started_message="Full-document translation started.",
        finished_message="Full-document translation finished.",
        output_description="This output is a deterministic local translation scaffold for the full current source document.",
    )


def _translate_current_chunks(
    connection: sqlite3.Connection,
    vault_path: Path | str,
    *,
    document: sqlite3.Row,
    revision_id: str,
    chunks: list[sqlite3.Row],
    target_language: str,
    source_language: str | None,
    adapter: DeterministicTranslationAdapter | None,
    translation_mode: str,
    prompt_version: str,
    task_type: str,
    execution_type: str,
    started_message: str,
    finished_message: str,
    output_description: str,
) -> TranslationResult:
    source_chunk_ids = tuple(str(chunk["chunk_id"]) for chunk in chunks)
    doc_id = str(document["doc_id"])
    source_lang = source_language or document["language"]
    translation_adapter = adapter or DeterministicTranslationAdapter()
    model = str(translation_adapter.model)

    task_id = create_task(
        connection,
        task_type,
        input_data={
            "doc_id": doc_id,
            "revision_id": revision_id,
            "chunk_ids": list(source_chunk_ids),
            "source_language": source_lang,
            "target_language": target_language,
            "translation_mode": translation_mode,
            "model": model,
            "prompt_version": prompt_version,
        },
    )
    start_task(connection, task_id)
    add_task_event(
        connection,
        task_id,
        "translation_started",
        started_message,
        {"doc_id": doc_id, "revision_id": revision_id, "chunk_count": len(chunks), "translation_mode": translation_mode},
    )

    execution_id = new_prefixed_id("execution")
    translation_id = new_prefixed_id("translation")
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO executions(
          execution_id, type, source_doc_id, source_revision_id,
          input_json, model, prompt_version, status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)
        """,
        (
            execution_id,
            execution_type,
            doc_id,
            revision_id,
            _json(
                {
                    "task_id": task_id,
                    "chunk_ids": list(source_chunk_ids),
                    "source_language": source_lang,
                    "target_language": target_language,
                    "translation_mode": translation_mode,
                }
            ),
            model,
            prompt_version,
            now,
            now,
        ),
    )

    output_path: Path | None = None
    try:
        translated_chunks = [
            {
                "chunk_id": str(chunk["chunk_id"]),
                "sequence": int(chunk["sequence"]),
                "heading_path": json.loads(chunk["heading_path_json"] or "[]"),
                "source_text": str(chunk["text"]),
                "translated_text": translation_adapter.translate(
                    str(chunk["text"]),
                    source_language=source_lang,
                    target_language=target_language,
                ),
            }
            for chunk in chunks
        ]

        output_path = _write_translation_output(
            vault_path,
            translation_id=translation_id,
            execution_id=execution_id,
            document=document,
            revision_id=revision_id,
            chunk_ids=source_chunk_ids,
            source_language=source_lang,
            target_language=target_language,
            translation_mode=translation_mode,
            model=model,
            prompt_version=prompt_version,
            output_description=output_description,
            translated_chunks=translated_chunks,
        )
        output_rel = vault_paths(vault_path).relative_to_vault(output_path)
        finished = utc_now_iso()
        connection.execute(
            """
            UPDATE executions
            SET output_path = ?,
                output_json = ?,
                status = 'succeeded',
                updated_at = ?,
                finished_at = ?
            WHERE execution_id = ?
            """,
            (
                output_rel,
                _json({"translation_id": translation_id, "chunk_count": len(translated_chunks)}),
                finished,
                finished,
                execution_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO translations(
              translation_id, execution_id, source_doc_id, source_revision_id,
              source_language, target_language, translation_mode,
              source_chunk_ids_json, output_path, model, prompt_version,
              status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'succeeded', ?, ?)
            """,
            (
                translation_id,
                execution_id,
                doc_id,
                revision_id,
                source_lang,
                target_language,
                translation_mode,
                _json(list(source_chunk_ids)),
                output_rel,
                model,
                prompt_version,
                now,
                finished,
            ),
        )
        result_data = {
            "translation_id": translation_id,
            "execution_id": execution_id,
            "output_path": output_rel,
            "chunk_count": len(translated_chunks),
            "translation_mode": translation_mode,
        }
        finish_task(connection, task_id, "succeeded", result_data=result_data)
        add_task_event(connection, task_id, "translation_finished", finished_message, result_data)
        connection.commit()
        return TranslationResult(
            translation_id=translation_id,
            execution_id=execution_id,
            task_id=task_id,
            source_doc_id=doc_id,
            source_revision_id=revision_id,
            source_chunk_ids=source_chunk_ids,
            target_language=target_language,
            output_path=output_rel,
            status="succeeded",
        )
    except Exception as exc:
        _mark_translation_failed(
            connection,
            vault_path,
            task_id=task_id,
            execution_id=execution_id,
            doc_id=doc_id,
            revision_id=revision_id,
            chunk_ids=source_chunk_ids,
            translation_id=translation_id,
            translation_mode=translation_mode,
            output_path=output_path,
            exc=exc,
        )
        raise


def list_translations(
    connection: sqlite3.Connection,
    *,
    doc_id: str | None = None,
    status: str | None = None,
    limit: int = 20,
) -> list[sqlite3.Row]:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    clauses = ["t.deleted_at IS NULL"]
    params: list[object] = []
    if doc_id is not None:
        clauses.append("t.source_doc_id = ?")
        params.append(doc_id)
    if status is not None:
        clauses.append("t.status = ?")
        params.append(status)
    params.append(limit)
    return list(
        connection.execute(
            f"""
            SELECT t.translation_id, t.execution_id, t.source_doc_id, d.title,
                   t.source_revision_id, t.source_language, t.target_language,
                   t.translation_mode, t.source_chunk_ids_json, t.output_path,
                   t.model, t.prompt_version, t.status, t.created_at, t.updated_at
            FROM translations t
            JOIN documents d ON d.doc_id = t.source_doc_id
            WHERE {" AND ".join(clauses)}
            ORDER BY t.created_at DESC, t.translation_id
            LIMIT ?
            """,
            params,
        )
    )


def get_translation(connection: sqlite3.Connection, translation_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT t.translation_id, t.execution_id, t.source_doc_id, d.title,
               t.source_revision_id, t.source_language, t.target_language,
               t.translation_mode, t.source_chunk_ids_json, t.output_path,
               t.model, t.prompt_version, t.status, t.created_at, t.updated_at,
               t.deleted_at
        FROM translations t
        JOIN documents d ON d.doc_id = t.source_doc_id
        WHERE t.translation_id = ?
        """,
        (translation_id,),
    ).fetchone()


def resolve_translation_output_path(
    connection: sqlite3.Connection,
    vault_path: Path | str,
    translation_id: str,
    *,
    folder: bool = False,
) -> Path:
    row = get_translation(connection, translation_id)
    if row is None or row["deleted_at"] is not None:
        raise ValueError(f"Translation not found: {translation_id}")
    if not row["output_path"]:
        raise ValueError(f"Translation has no output path: {translation_id}")
    target = vault_paths(vault_path).root / str(row["output_path"])
    if folder:
        target = target.parent
    target = target.resolve(strict=False)
    if not target.exists():
        raise ValueError(f"Translation output path does not exist: {target}")
    return target


def _load_translation_document(connection: sqlite3.Connection, doc_id: str) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT doc_id, title, status, current_revision_id, language, canonical_path
        FROM documents
        WHERE doc_id = ?
          AND deleted_at IS NULL
        """,
        (doc_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Document not found: {doc_id}")
    return row


def _validate_document_for_translation(document: sqlite3.Row, revision_id: str) -> None:
    doc_id = str(document["doc_id"])
    if document["status"] != "active":
        raise ValueError(f"Document is not active: {doc_id}")
    if document["current_revision_id"] is None:
        raise ValueError(f"Document has no current revision: {doc_id}")
    if document["current_revision_id"] != revision_id:
        raise ValueError(f"M9 translation requires the current revision for document {doc_id}: {revision_id}")


def _load_current_revision_chunks(
    connection: sqlite3.Connection,
    *,
    doc_id: str,
    revision_id: str,
) -> list[sqlite3.Row]:
    rows = connection.execute(
        """
        SELECT chunk_id, doc_id, revision_id, sequence, heading_path_json, text, is_current
        FROM chunks
        WHERE doc_id = ?
          AND revision_id = ?
          AND is_current = 1
          AND deleted_at IS NULL
        ORDER BY sequence, chunk_id
        """,
        (doc_id, revision_id),
    ).fetchall()
    if not rows:
        raise ValueError(f"Document revision has no active current chunks: {revision_id}")
    return list(rows)


def _load_selected_chunks(
    connection: sqlite3.Connection,
    *,
    doc_id: str,
    revision_id: str,
    chunk_ids: tuple[str, ...],
) -> list[sqlite3.Row]:
    placeholders = ", ".join("?" for _ in chunk_ids)
    rows = connection.execute(
        f"""
        SELECT chunk_id, doc_id, revision_id, sequence, heading_path_json, text, is_current
        FROM chunks
        WHERE chunk_id IN ({placeholders})
          AND doc_id = ?
          AND revision_id = ?
          AND is_current = 1
          AND deleted_at IS NULL
        """,
        (*chunk_ids, doc_id, revision_id),
    ).fetchall()
    by_id = {str(row["chunk_id"]): row for row in rows}
    missing = [chunk_id for chunk_id in chunk_ids if chunk_id not in by_id]
    if missing:
        raise ValueError(f"Selected chunk(s) are not active current chunks for revision {revision_id}: {', '.join(missing)}")
    return [by_id[chunk_id] for chunk_id in chunk_ids]


def _write_translation_output(
    vault_path: Path | str,
    *,
    translation_id: str,
    execution_id: str,
    document: sqlite3.Row,
    revision_id: str,
    chunk_ids: tuple[str, ...],
    source_language: str | None,
    target_language: str,
    translation_mode: str,
    model: str,
    prompt_version: str,
    output_description: str,
    translated_chunks: list[dict[str, object]],
) -> Path:
    paths = vault_paths(vault_path)
    output_path = paths.outputs_translations / f"{translation_id}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = {
        "schema_version": "indbase.translation.v1",
        "type": "translation",
        "translation_id": translation_id,
        "execution_id": execution_id,
        "source_doc_id": document["doc_id"],
        "source_revision_id": revision_id,
        "source_chunk_ids": list(chunk_ids),
        "source_language": source_language,
        "target_language": target_language,
        "translation_mode": translation_mode,
        "model": model,
        "prompt_version": prompt_version,
    }
    body = [
        "---",
        *(_yaml_line(key, value) for key, value in frontmatter.items()),
        "---",
        "",
        f"# Translation: {document['title'] or document['doc_id']}",
        "",
        output_description,
        "",
    ]
    for item in translated_chunks:
        heading = " / ".join(str(part) for part in item["heading_path"]) if item["heading_path"] else "Untitled"
        body.extend(
            [
                f"## Chunk {item['chunk_id']}",
                "",
                f"- source_doc_id: `{document['doc_id']}`",
                f"- source_revision_id: `{revision_id}`",
                f"- source_chunk_id: `{item['chunk_id']}`",
                f"- heading: {heading}",
                "",
                "### Source",
                "",
                str(item["source_text"]),
                "",
                "### Translation",
                "",
                str(item["translated_text"]),
                "",
            ]
        )
    _write_new_file(output_path, "\n".join(body).rstrip() + "\n")
    return output_path


def _mark_translation_failed(
    connection: sqlite3.Connection,
    vault_path: Path | str,
    *,
    task_id: str,
    execution_id: str,
    doc_id: str,
    revision_id: str,
    chunk_ids: tuple[str, ...],
    translation_id: str,
    translation_mode: str,
    output_path: Path | None,
    exc: Exception,
) -> None:
    paths = vault_paths(vault_path)
    output_rel: str | None = None
    if output_path is not None:
        try:
            output_rel = paths.relative_to_vault(output_path)
        except ValueError:
            output_rel = str(output_path)
        if output_path.exists():
            output_path.unlink()

    error_id = record_error(
        connection,
        component="translation",
        error_type=type(exc).__name__,
        message=str(exc),
        task_id=task_id,
        severity="error",
        retryable=True,
        user_message="Translation failed before a durable output was written.",
        payload={
            "doc_id": doc_id,
            "revision_id": revision_id,
            "chunk_ids": list(chunk_ids),
            "translation_id": translation_id,
            "execution_id": execution_id,
            "translation_mode": translation_mode,
            "output_path": output_rel,
        },
    )
    failed = utc_now_iso()
    error_data = {
        "error_id": error_id,
        "error_type": type(exc).__name__,
        "message": str(exc),
        "translation_id": translation_id,
        "execution_id": execution_id,
        "translation_mode": translation_mode,
    }
    connection.execute(
        """
        UPDATE executions
        SET output_json = ?,
            status = 'failed',
            updated_at = ?,
            finished_at = ?
        WHERE execution_id = ?
        """,
        (_json(error_data), failed, failed, execution_id),
    )
    finish_task(connection, task_id, "failed", error_data=error_data)
    add_task_event(connection, task_id, "translation_failed", "Translation failed.", error_data)
    connection.commit()


def _normalize_chunk_ids(chunk_ids: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in chunk_ids:
        chunk_id = str(raw).strip()
        if not chunk_id:
            continue
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        normalized.append(chunk_id)
    if not normalized:
        raise ValueError("At least one chunk ID is required.")
    return tuple(normalized)


def _clean_language(value: str, *, field_name: str) -> str:
    clean = " ".join(value.strip().split())
    if not clean:
        raise ValueError(f"{field_name} must not be empty.")
    return clean


def _write_new_file(path: Path, content: str) -> None:
    if path.exists():
        raise FileExistsError(f"Translation output already exists: {path}")
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(path)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _yaml_line(key: str, value: object) -> str:
    if isinstance(value, bool):
        return f"{key}: {'true' if value else 'false'}"
    if value is None:
        return f"{key}: null"
    if isinstance(value, list):
        return f"{key}: {json.dumps(value, ensure_ascii=False)}"
    return f"{key}: {json.dumps(str(value), ensure_ascii=False)}"
