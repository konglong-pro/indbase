"""OCR v0 services."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3

from indbase_core.chunker import chunk_current_revision
from indbase_core.conversion import CONVERTER_VERSION, hash_markdown
from indbase_core.errors import record_error
from indbase_core.ids import new_prefixed_id, revision_id
from indbase_core.indexer import rebuild_fts_index
from indbase_core.paths import vault_paths
from indbase_core.reviews import create_review_item
from indbase_core.tasks import add_task_event, create_task, finish_task, start_task
from indbase_core.time import utc_now_iso


OCR_CONFIDENCE_REVIEW_THRESHOLD = 0.60


@dataclass(frozen=True)
class OcrPageResult:
    page_number: int
    text: str
    confidence: float | None = None


@dataclass(frozen=True)
class OcrRunResult:
    task_id: str
    doc_id: str
    status: str
    engine: str
    page_count: int
    revision_id: str | None
    chunk_count: int
    indexed_chunks: int
    review_items: int
    error_id: str | None = None


def run_ocr_for_document(
    connection: sqlite3.Connection,
    vault_path: Path | str,
    doc_id: str,
    *,
    engine: str = "sidecar",
    force: bool = False,
) -> OcrRunResult:
    """Run explicit OCR for one archived source document."""
    paths = vault_paths(vault_path)
    task_id = create_task(connection, "ocr", input_data={"doc_id": doc_id, "engine": engine, "force": force})
    start_task(connection, task_id)
    add_task_event(
        connection,
        task_id,
        "ocr_started",
        "OCR started.",
        {"doc_id": doc_id, "engine": engine, "force": force},
    )
    try:
        document = _load_document(connection, doc_id)
        if document is None:
            raise OcrPolicyError(f"Document not found: {doc_id}")
        _validate_ocr_allowed(document, force=force)
        if document["original_path"] is None:
            raise ValueError(f"Document has no archived original: {doc_id}")

        original_path = paths.root / str(document["original_path"])
        if not original_path.is_file():
            raise ValueError(f"Archived original is missing: {document['original_path']}")

        pages = _run_adapter(engine, original_path)
        non_empty_pages = tuple(
            OcrPageResult(
                page_number=page.page_number,
                text=_normalize_text(page.text),
                confidence=page.confidence,
            )
            for page in pages
            if _normalize_text(page.text).strip()
        )
        if not non_empty_pages:
            raise OcrError("OCR produced no extractable text.")

        markdown_body = _render_ocr_markdown(str(document["title"] or "Untitled"), non_empty_pages)
        content_hash = hash_markdown(markdown_body)
        if _current_revision_content_hash(connection, doc_id) == content_hash:
            add_task_event(
                connection,
                task_id,
                "ocr_no_content_change",
                "OCR text matched current revision.",
                {"doc_id": doc_id},
            )
            finish_task(
                connection,
                task_id,
                "succeeded",
                result_data={
                    "doc_id": doc_id,
                    "engine": engine,
                    "page_count": len(non_empty_pages),
                    "revision_id": document["current_revision_id"],
                    "no_content_change": True,
                },
            )
            return OcrRunResult(
                task_id=task_id,
                doc_id=doc_id,
                status="succeeded",
                engine=engine,
                page_count=len(non_empty_pages),
                revision_id=document["current_revision_id"],
                chunk_count=0,
                indexed_chunks=0,
                review_items=0,
            )

        revision = _write_ocr_revision(
            connection,
            paths.root,
            document,
            engine=engine,
            markdown_body=markdown_body,
            content_hash=content_hash,
        )
        review_items = _write_ocr_pages(
            connection,
            doc_id=doc_id,
            revision_id_value=revision.revision_id,
            pages=non_empty_pages,
            engine=engine,
        )
        chunk_result = chunk_current_revision(connection, paths.root, doc_id)
        index_result = rebuild_fts_index(connection, paths.root)
        add_task_event(
            connection,
            task_id,
            "ocr_completed",
            "OCR revision written, chunked, and indexed.",
            {
                "doc_id": doc_id,
                "revision_id": revision.revision_id,
                "page_count": len(non_empty_pages),
                "chunk_count": chunk_result.chunk_count,
                "indexed_chunks": index_result.indexed_chunks,
                "review_items": review_items,
            },
        )
        status = "completed_with_issues" if review_items else "succeeded"
        finish_task(
            connection,
            task_id,
            status,
            result_data={
                "doc_id": doc_id,
                "engine": engine,
                "page_count": len(non_empty_pages),
                "revision_id": revision.revision_id,
                "chunk_count": chunk_result.chunk_count,
                "indexed_chunks": index_result.indexed_chunks,
                "review_items": review_items,
            },
        )
        return OcrRunResult(
            task_id=task_id,
            doc_id=doc_id,
            status=status,
            engine=engine,
            page_count=len(non_empty_pages),
            revision_id=revision.revision_id,
            chunk_count=chunk_result.chunk_count,
            indexed_chunks=index_result.indexed_chunks,
            review_items=review_items,
        )
    except Exception as exc:
        if isinstance(exc, OcrPolicyError):
            add_task_event(
                connection,
                task_id,
                "ocr_blocked",
                "OCR was blocked by document state policy.",
                {"doc_id": doc_id, "engine": engine, "reason": str(exc)},
            )
            finish_task(
                connection,
                task_id,
                "failed",
                error_data={"type": type(exc).__name__, "message": str(exc)},
            )
            return OcrRunResult(
                task_id=task_id,
                doc_id=doc_id,
                status="blocked",
                engine=engine,
                page_count=0,
                revision_id=None,
                chunk_count=0,
                indexed_chunks=0,
                review_items=0,
            )
        error_id = _record_ocr_failure(connection, task_id, doc_id, engine, exc)
        finish_task(
            connection,
            task_id,
            "failed",
            error_data={"type": type(exc).__name__, "message": str(exc), "error_id": error_id},
        )
        return OcrRunResult(
            task_id=task_id,
            doc_id=doc_id,
            status="failed",
            engine=engine,
            page_count=0,
            revision_id=None,
            chunk_count=0,
            indexed_chunks=0,
            review_items=1,
            error_id=error_id,
        )


def list_ocr_pages(connection: sqlite3.Connection, doc_id: str) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT ocr_page_id, doc_id, revision_id, page_number, text, confidence,
                   quality_status, needs_review, engine, engine_version, created_at
            FROM ocr_pages
            WHERE doc_id = ?
              AND deleted_at IS NULL
            ORDER BY revision_id, page_number
            """,
            (doc_id,),
        )
    )


class OcrError(RuntimeError):
    """Raised when OCR cannot produce usable text."""


class OcrPolicyError(RuntimeError):
    """Raised when OCR is blocked by a document-state rule."""


@dataclass(frozen=True)
class _WrittenOcrRevision:
    revision_id: str
    markdown_path: str


def _load_document(connection: sqlite3.Connection, doc_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT doc_id, current_revision_id, title, original_title, filename_slug,
               status, source_type, source_uri, normalized_source_uri, source_hash,
               original_path, language, category_id
        FROM documents
        WHERE doc_id = ?
          AND deleted_at IS NULL
        """,
        (doc_id,),
    ).fetchone()


def _validate_ocr_allowed(document: sqlite3.Row, *, force: bool) -> None:
    doc_id = str(document["doc_id"])
    if document["status"] == "archived":
        raise OcrPolicyError(f"Document is archived; restore it before OCR: {doc_id}")
    if str(document["source_type"] or "").lower() != "pdf":
        raise OcrPolicyError(f"OCR v0 is only enabled for PDF documents: {doc_id}")
    if document["current_revision_id"] is not None and not force:
        raise OcrPolicyError(
            f"Document already has a current revision; rerun OCR with force=True to create an OCR revision: {doc_id}"
        )


def _run_adapter(engine: str, original_path: Path) -> tuple[OcrPageResult, ...]:
    normalized = engine.lower().strip()
    if normalized == "sidecar":
        return _run_sidecar_adapter(original_path)
    raise OcrError(f"OCR engine is not available: {engine}")


def _run_sidecar_adapter(original_path: Path) -> tuple[OcrPageResult, ...]:
    json_path = original_path.with_name(original_path.name + ".ocr.json")
    text_path = original_path.with_name(original_path.name + ".ocr.txt")
    if json_path.is_file():
        return _read_sidecar_json(json_path)
    if text_path.is_file():
        text = text_path.read_text(encoding="utf-8")
        pages = text.split("\f")
        return tuple(
            OcrPageResult(page_number=index + 1, text=page, confidence=1.0)
            for index, page in enumerate(pages)
        )
    raise OcrError(
        "Sidecar OCR text not found. Expected "
        f"{json_path.name} or {text_path.name} beside the archived original."
    )


def _read_sidecar_json(path: Path) -> tuple[OcrPageResult, ...]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise OcrError(f"Sidecar OCR JSON is invalid: {exc}") from exc
    if not isinstance(raw, list):
        raise OcrError("Sidecar OCR JSON must be a list of page objects.")
    pages: list[OcrPageResult] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise OcrError("Sidecar OCR JSON pages must be objects.")
        page_number = _json_page_number(item.get("page_number"), index)
        text = str(item.get("text", ""))
        confidence = _json_confidence(item.get("confidence"))
        pages.append(
            OcrPageResult(
                page_number=page_number,
                text=text,
                confidence=confidence,
            )
        )
    duplicate_pages = _duplicate_page_numbers(pages)
    if duplicate_pages:
        raise OcrError(f"Sidecar OCR JSON has duplicate page_number values: {', '.join(map(str, duplicate_pages))}.")
    return tuple(sorted(pages, key=lambda page: page.page_number))


def _json_page_number(value: object, index: int) -> int:
    if value is None:
        return index + 1
    if isinstance(value, bool) or not isinstance(value, int):
        raise OcrError("Sidecar OCR JSON page_number must be an integer when provided.")
    if value < 1:
        raise OcrError("Sidecar OCR JSON page_number must be >= 1.")
    return value


def _json_confidence(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OcrError("Sidecar OCR JSON confidence must be a number between 0 and 1.")
    confidence = float(value)
    if confidence < 0 or confidence > 1:
        raise OcrError("Sidecar OCR JSON confidence must be between 0 and 1.")
    return confidence


def _duplicate_page_numbers(pages: tuple[OcrPageResult, ...] | list[OcrPageResult]) -> tuple[int, ...]:
    seen: set[int] = set()
    duplicates: list[int] = []
    for page in pages:
        if page.page_number in seen and page.page_number not in duplicates:
            duplicates.append(page.page_number)
        seen.add(page.page_number)
    return tuple(duplicates)


def _write_ocr_revision(
    connection: sqlite3.Connection,
    vault_root: Path,
    document: sqlite3.Row,
    *,
    engine: str,
    markdown_body: str,
    content_hash: str,
) -> _WrittenOcrRevision:
    paths = vault_paths(vault_root)
    sequence = _next_revision_sequence(connection, str(document["doc_id"]))
    rev_id = revision_id(str(document["doc_id"]), sequence)
    markdown_path = paths.source_markdown_path(str(document["doc_id"]), str(document["filename_slug"] or "ocr"), sequence)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_rel = paths.relative_to_vault(markdown_path)
    now = utc_now_iso()
    converter_run_id = new_prefixed_id("converter_run")
    converter_name = f"ocr_{engine}"
    final_markdown = _render_source_markdown(
        document=document,
        revision_id_value=rev_id,
        canonical_path=markdown_rel,
        content_hash=content_hash,
        body=markdown_body,
        ingested_at=now,
        converter_name=converter_name,
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
            document["doc_id"],
            sequence,
            markdown_rel,
            content_hash,
            converter_name,
            CONVERTER_VERSION,
            len(markdown_body),
            now,
            now,
        ),
    )
    connection.execute(
        """
        INSERT INTO converter_runs(
          converter_run_id, doc_id, revision_id, converter_name, converter_version,
          input_hash, output_hash, warnings_json, quality_signals_json, status,
          started_at, finished_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, '[]', ?, 'succeeded', ?, ?, ?, ?)
        """,
        (
            converter_run_id,
            document["doc_id"],
            rev_id,
            converter_name,
            CONVERTER_VERSION,
            document["source_hash"],
            content_hash,
            _json({"engine": engine, "ocr": True}),
            now,
            now,
            now,
            now,
        ),
    )
    connection.execute(
        """
        UPDATE documents
        SET current_revision_id = ?,
            canonical_path = ?,
            ingest_status = 'revisioned',
            fts_status = 'not_indexed',
            quality_status = 'passed',
            quality_signals_json = ?,
            updated_at = ?
        WHERE doc_id = ?
        """,
        (
            rev_id,
            markdown_rel,
            _json({"ocr": True, "engine": engine}),
            now,
            document["doc_id"],
        ),
    )
    connection.commit()
    return _WrittenOcrRevision(revision_id=rev_id, markdown_path=markdown_rel)


def _write_ocr_pages(
    connection: sqlite3.Connection,
    *,
    doc_id: str,
    revision_id_value: str,
    pages: tuple[OcrPageResult, ...],
    engine: str,
) -> int:
    now = utc_now_iso()
    review_items = 0
    for page in pages:
        confidence = page.confidence
        low_confidence = confidence is not None and confidence < OCR_CONFIDENCE_REVIEW_THRESHOLD
        quality_status = "warning" if low_confidence else "passed"
        connection.execute(
            """
            INSERT INTO ocr_pages(
              ocr_page_id, doc_id, revision_id, page_number, text, confidence,
              quality_status, quality_signals_json, needs_review, engine,
              engine_version, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_prefixed_id("ocr_page"),
                doc_id,
                revision_id_value,
                page.page_number,
                page.text,
                confidence,
                quality_status,
                _json({"confidence": confidence}),
                1 if low_confidence else 0,
                engine,
                CONVERTER_VERSION,
                now,
                now,
            ),
        )
        if low_confidence:
            review_items += 1
            create_review_item(
                connection,
                review_type="ocr_low_quality",
                target_type="document",
                target_id=doc_id,
                reason=f"OCR page {page.page_number} confidence is below threshold.",
                priority=40,
            )
    if review_items:
        connection.execute(
            """
            UPDATE documents
            SET needs_review = 1, quality_status = 'warning', updated_at = ?
            WHERE doc_id = ?
            """,
            (now, doc_id),
        )
    connection.commit()
    return review_items


def _record_ocr_failure(
    connection: sqlite3.Connection,
    task_id: str,
    doc_id: str,
    engine: str,
    exc: Exception,
) -> str:
    now = utc_now_iso()
    has_current_revision = _document_has_current_revision(connection, doc_id)
    error_id = record_error(
        connection,
        task_id=task_id,
        component="ocr",
        error_type=_ocr_error_type(exc),
        message=str(exc),
        user_message="OCR did not produce usable text.",
        retryable=True,
        payload={"doc_id": doc_id, "engine": engine},
    )
    create_review_item(
        connection,
        review_type="ocr_low_quality",
        target_type="document",
        target_id=doc_id,
        reason=f"OCR failed for {doc_id}: {exc}",
        priority=40,
    )
    if has_current_revision:
        connection.execute(
            """
            UPDATE documents
            SET needs_review = 1, updated_at = ?
            WHERE doc_id = ?
            """,
            (now, doc_id),
        )
    else:
        connection.execute(
            """
            UPDATE documents
            SET needs_review = 1, quality_status = 'failed', updated_at = ?
            WHERE doc_id = ?
            """,
            (now, doc_id),
        )
    add_task_event(
        connection,
        task_id,
        "ocr_failed",
        "OCR failed.",
        {"doc_id": doc_id, "engine": engine, "error_id": error_id},
    )
    connection.commit()
    return error_id


def _render_ocr_markdown(title: str, pages: tuple[OcrPageResult, ...]) -> str:
    lines = [f"# {title} OCR", ""]
    for page in pages:
        lines.append(f"## Page {page.page_number}")
        lines.append("")
        lines.append(page.text.strip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_source_markdown(
    *,
    document: sqlite3.Row,
    revision_id_value: str,
    canonical_path: str,
    content_hash: str,
    body: str,
    ingested_at: str,
    converter_name: str,
) -> str:
    frontmatter = {
        "schema_version": "indbase.source.v1",
        "type": "source_document",
        "status": document["status"],
        "doc_id": document["doc_id"],
        "revision_id": revision_id_value,
        "title": document["title"],
        "original_title": document["original_title"],
        "filename_slug": document["filename_slug"],
        "source_type": document["source_type"],
        "source_uri": document["source_uri"],
        "normalized_source_uri": document["normalized_source_uri"],
        "original_path": document["original_path"],
        "canonical_path": canonical_path,
        "source_hash": document["source_hash"],
        "content_hash": content_hash,
        "converter": converter_name,
        "converter_version": CONVERTER_VERSION,
        "language": document["language"],
        "category_id": document["category_id"],
        "quality_status": "passed",
        "quality_signals": {"ocr": True},
        "chunk_count": 0,
        "fts_indexed": False,
        "embedding_indexed": False,
        "needs_review": False,
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


def _current_revision_content_hash(connection: sqlite3.Connection, doc_id: str) -> str | None:
    row = connection.execute(
        """
        SELECT dr.content_hash
        FROM documents d
        JOIN document_revisions dr ON dr.revision_id = d.current_revision_id
        WHERE d.doc_id = ?
        """,
        (doc_id,),
    ).fetchone()
    if row is None:
        return None
    return str(row["content_hash"])


def _document_has_current_revision(connection: sqlite3.Connection, doc_id: str) -> bool:
    row = connection.execute(
        """
        SELECT current_revision_id
        FROM documents
        WHERE doc_id = ?
          AND current_revision_id IS NOT NULL
        """,
        (doc_id,),
    ).fetchone()
    return row is not None


def _ocr_error_type(exc: Exception) -> str:
    if isinstance(exc, OcrError):
        return "ocr_no_extractable_text"
    return type(exc).__name__


def _normalize_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    return normalized if normalized.endswith("\n") else normalized + "\n"


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


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
