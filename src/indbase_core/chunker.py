"""Markdown chunking services for immutable revisions."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from indbase_core.conversion import hash_markdown
from indbase_core.paths import vault_paths
from indbase_core.time import utc_now_iso

CHUNK_STRATEGY = "markdown_heading_v1"
DEFAULT_TARGET_TOKENS = 800
DEFAULT_MAX_TOKENS = 1500
FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n(?:\r?\n)?", re.DOTALL)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
TOKEN_RE = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]|[A-Za-z0-9_]+|[^\s]")


@dataclass(frozen=True)
class ChunkingOptions:
    target_tokens: int = DEFAULT_TARGET_TOKENS
    max_tokens: int = DEFAULT_MAX_TOKENS

    def __post_init__(self) -> None:
        if self.target_tokens < 1:
            raise ValueError("target_tokens must be >= 1")
        if self.max_tokens < self.target_tokens:
            raise ValueError("max_tokens must be >= target_tokens")


@dataclass(frozen=True)
class ChunkCandidate:
    sequence: int
    heading_path: tuple[str, ...]
    text: str
    start_offset: int
    end_offset: int
    token_count: int
    content_hash: str


@dataclass(frozen=True)
class StoredChunk:
    chunk_id: str
    sequence: int
    heading_path: tuple[str, ...]
    text: str
    start_offset: int | None
    end_offset: int | None
    token_count: int | None
    content_hash: str | None
    is_current: bool


@dataclass(frozen=True)
class ChunkWriteResult:
    doc_id: str
    revision_id: str
    chunk_count: int
    inserted_count: int
    existing_count: int
    chunks: tuple[StoredChunk, ...]


@dataclass(frozen=True)
class _TextBlock:
    heading_path: tuple[str, ...]
    text: str
    start_offset: int
    end_offset: int
    token_count: int


def chunk_current_revision(
    connection: sqlite3.Connection,
    vault_path: Path | str,
    doc_id: str,
    *,
    options: ChunkingOptions | None = None,
) -> ChunkWriteResult:
    row = connection.execute(
        """
        SELECT current_revision_id
        FROM documents
        WHERE doc_id = ?
        """,
        (doc_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Document not found: {doc_id}")
    revision_id = row["current_revision_id"]
    if revision_id is None:
        raise ValueError(f"Document has no current revision: {doc_id}")
    return chunk_revision(connection, vault_path, str(revision_id), options=options)


def chunk_revision(
    connection: sqlite3.Connection,
    vault_path: Path | str,
    revision_id: str,
    *,
    options: ChunkingOptions | None = None,
) -> ChunkWriteResult:
    row = connection.execute(
        """
        SELECT dr.revision_id, dr.doc_id, dr.markdown_path, dr.content_hash,
               d.current_revision_id, d.language
        FROM document_revisions dr
        JOIN documents d ON d.doc_id = dr.doc_id
        WHERE dr.revision_id = ?
        """,
        (revision_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Revision not found: {revision_id}")

    existing = _load_stored_chunks(connection, revision_id)
    is_current = row["current_revision_id"] == revision_id
    if existing:
        _refresh_revision_chunk_metadata(
            connection,
            doc_id=str(row["doc_id"]),
            revision_id=revision_id,
            chunk_count=len(existing),
            is_current=is_current,
        )
        connection.commit()
        refreshed = _load_stored_chunks(connection, revision_id)
        return ChunkWriteResult(
            doc_id=str(row["doc_id"]),
            revision_id=revision_id,
            chunk_count=len(refreshed),
            inserted_count=0,
            existing_count=len(refreshed),
            chunks=tuple(refreshed),
        )

    paths = vault_paths(vault_path)
    markdown_path = paths.root / row["markdown_path"]
    markdown = markdown_path.read_text(encoding="utf-8")
    body, _body_start = strip_frontmatter(markdown)
    if hash_markdown(body) != row["content_hash"]:
        raise ValueError(f"Revision Markdown content hash mismatch: {revision_id}")

    candidates = chunk_markdown_body(body, options=options)
    now = utc_now_iso()
    if is_current:
        connection.execute(
            "UPDATE chunks SET is_current = 0, updated_at = ? WHERE doc_id = ?",
            (now, row["doc_id"]),
        )
    for candidate in candidates:
        connection.execute(
            """
            INSERT INTO chunks(
              chunk_id, doc_id, revision_id, sequence, heading_path_json,
              text, start_offset, end_offset, source_page, language,
              token_count, content_hash, is_current, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
            """,
            (
                chunk_id_for_revision(revision_id, candidate.sequence),
                row["doc_id"],
                revision_id,
                candidate.sequence,
                json.dumps(list(candidate.heading_path), ensure_ascii=False),
                candidate.text,
                candidate.start_offset,
                candidate.end_offset,
                row["language"],
                candidate.token_count,
                candidate.content_hash,
                1 if is_current else 0,
                now,
                now,
            ),
        )
    _refresh_revision_chunk_metadata(
        connection,
        doc_id=str(row["doc_id"]),
        revision_id=revision_id,
        chunk_count=len(candidates),
        is_current=is_current,
    )
    connection.commit()
    chunks = _load_stored_chunks(connection, revision_id)
    return ChunkWriteResult(
        doc_id=str(row["doc_id"]),
        revision_id=revision_id,
        chunk_count=len(chunks),
        inserted_count=len(chunks),
        existing_count=0,
        chunks=tuple(chunks),
    )


def strip_frontmatter(markdown: str) -> tuple[str, int]:
    match = FRONTMATTER_RE.match(markdown)
    if match is None:
        return markdown, 0
    return markdown[match.end() :], match.end()


def chunk_markdown_body(
    body: str,
    *,
    options: ChunkingOptions | None = None,
) -> tuple[ChunkCandidate, ...]:
    opts = options or ChunkingOptions()
    blocks = _split_large_blocks(_parse_markdown_blocks(body), opts.max_tokens)
    if not blocks and body.strip():
        start, end, text = _trimmed_span(body, 0, len(body))
        blocks = (
            _TextBlock(
                heading_path=(),
                text=text,
                start_offset=start,
                end_offset=end,
                token_count=estimate_token_count(text),
            ),
        )

    chunks: list[ChunkCandidate] = []
    current: list[_TextBlock] = []
    current_tokens = 0
    current_heading: tuple[str, ...] | None = None
    for block in blocks:
        heading_changed = current_heading is not None and block.heading_path != current_heading
        target_exceeded = current and current_tokens + block.token_count > opts.target_tokens
        if heading_changed or target_exceeded:
            chunks.append(_render_candidate(len(chunks) + 1, current))
            current = []
            current_tokens = 0
        current.append(block)
        current_tokens += block.token_count
        current_heading = block.heading_path
    if current:
        chunks.append(_render_candidate(len(chunks) + 1, current))
    return tuple(chunks)


def estimate_token_count(text: str) -> int:
    return len(TOKEN_RE.findall(text))


def chunk_id_for_revision(revision_id: str, sequence: int) -> str:
    if sequence < 1:
        raise ValueError("chunk sequence must be >= 1")
    return f"chunk_{revision_id}_{sequence:04d}"


def _parse_markdown_blocks(body: str) -> tuple[_TextBlock, ...]:
    blocks: list[_TextBlock] = []
    heading_path: list[str] = []
    paragraph_lines: list[str] = []
    paragraph_start: int | None = None
    position = 0

    def flush_paragraph(end_position: int) -> None:
        nonlocal paragraph_lines, paragraph_start
        if paragraph_start is None:
            return
        raw = "".join(paragraph_lines)
        start, end, text = _trimmed_span(raw, paragraph_start, end_position)
        if text:
            blocks.append(
                _TextBlock(
                    heading_path=tuple(heading_path),
                    text=text,
                    start_offset=start,
                    end_offset=end,
                    token_count=estimate_token_count(text),
                )
            )
        paragraph_lines = []
        paragraph_start = None

    for line in body.splitlines(keepends=True):
        line_start = position
        line_end = position + len(line)
        position = line_end
        stripped = line.strip()
        heading_match = HEADING_RE.match(stripped)
        if heading_match is not None:
            flush_paragraph(line_start)
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            heading_path = heading_path[: level - 1]
            heading_path.append(title)
            continue
        if stripped == "":
            flush_paragraph(line_start)
            continue
        if paragraph_start is None:
            paragraph_start = line_start
        paragraph_lines.append(line)
    flush_paragraph(len(body))
    return tuple(blocks)


def _split_large_blocks(blocks: tuple[_TextBlock, ...], max_tokens: int) -> tuple[_TextBlock, ...]:
    split: list[_TextBlock] = []
    for block in blocks:
        if block.token_count <= max_tokens:
            split.append(block)
            continue
        token_matches = list(TOKEN_RE.finditer(block.text))
        for start_index in range(0, len(token_matches), max_tokens):
            end_index = min(start_index + max_tokens, len(token_matches))
            first = token_matches[start_index]
            last = token_matches[end_index - 1]
            absolute_start = block.start_offset + first.start()
            absolute_end = block.start_offset + last.end()
            text = block.text[first.start() : last.end()]
            split.append(
                _TextBlock(
                    heading_path=block.heading_path,
                    text=text,
                    start_offset=absolute_start,
                    end_offset=absolute_end,
                    token_count=estimate_token_count(text),
                )
            )
    return tuple(split)


def _render_candidate(sequence: int, blocks: list[_TextBlock]) -> ChunkCandidate:
    heading_path = blocks[0].heading_path
    body = "\n\n".join(block.text for block in blocks).strip()
    if heading_path:
        heading_context = "\n".join(f"{'#' * min(index + 1, 6)} {title}" for index, title in enumerate(heading_path))
        text = f"{heading_context}\n\n{body}".strip()
    else:
        text = body
    return ChunkCandidate(
        sequence=sequence,
        heading_path=heading_path,
        text=text,
        start_offset=min(block.start_offset for block in blocks),
        end_offset=max(block.end_offset for block in blocks),
        token_count=estimate_token_count(text),
        content_hash=_hash_text(text),
    )


def _trimmed_span(text: str, absolute_start: int, absolute_end: int) -> tuple[int, int, str]:
    leading = len(text) - len(text.lstrip())
    trailing = len(text.rstrip())
    start = absolute_start + leading
    end = absolute_start + trailing
    return start, end, text[leading:trailing]


def _hash_text(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _refresh_revision_chunk_metadata(
    connection: sqlite3.Connection,
    *,
    doc_id: str,
    revision_id: str,
    chunk_count: int,
    is_current: bool,
) -> None:
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE document_revisions
        SET chunk_strategy = ?, chunk_count = ?, updated_at = ?
        WHERE revision_id = ?
        """,
        (CHUNK_STRATEGY, chunk_count, now, revision_id),
    )
    connection.execute(
        """
        UPDATE chunks
        SET is_current = ?, updated_at = ?
        WHERE doc_id = ?
          AND revision_id = ?
        """,
        (1 if is_current else 0, now, doc_id, revision_id),
    )


def _load_stored_chunks(connection: sqlite3.Connection, revision_id: str) -> list[StoredChunk]:
    rows = connection.execute(
        """
        SELECT chunk_id, sequence, heading_path_json, text, start_offset,
               end_offset, token_count, content_hash, is_current
        FROM chunks
        WHERE revision_id = ?
          AND deleted_at IS NULL
        ORDER BY sequence
        """,
        (revision_id,),
    ).fetchall()
    return [
        StoredChunk(
            chunk_id=str(row["chunk_id"]),
            sequence=int(row["sequence"]),
            heading_path=tuple(json.loads(row["heading_path_json"] or "[]")),
            text=str(row["text"]),
            start_offset=row["start_offset"],
            end_offset=row["end_offset"],
            token_count=row["token_count"],
            content_hash=row["content_hash"],
            is_current=bool(row["is_current"]),
        )
        for row in rows
    ]
