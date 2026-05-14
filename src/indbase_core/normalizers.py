"""Deterministic Tier 1 source normalizers."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from html.parser import HTMLParser
from io import StringIO
import json
from pathlib import Path


@dataclass(frozen=True)
class NormalizedMarkdown:
    markdown: str
    converter_name: str
    warnings: tuple[str, ...] = ()


class NormalizationError(ValueError):
    """Raised when a source cannot be normalized into Markdown."""


def normalize_tier1_source(path: Path | str, source_type: str) -> NormalizedMarkdown:
    source_path = Path(path)
    normalized_type = source_type.lower().lstrip(".")
    if normalized_type == "md":
        return NormalizedMarkdown(
            markdown=_normalize_text(_read_text(source_path)),
            converter_name="direct_normalizer",
        )
    if normalized_type == "txt":
        return NormalizedMarkdown(
            markdown=_normalize_text(_read_text(source_path)),
            converter_name="direct_normalizer",
        )
    if normalized_type == "csv":
        return _normalize_csv(source_path)
    if normalized_type == "json":
        return _normalize_json(source_path)
    if normalized_type == "html":
        return _normalize_html(source_path)
    raise NormalizationError(f"Tier 1 normalizer is not implemented for .{source_type}")


def normalize_tier2_source(path: Path | str, source_type: str) -> NormalizedMarkdown:
    source_path = Path(path)
    normalized_type = source_type.lower().lstrip(".")
    if normalized_type not in {"docx", "xlsx", "pptx", "pdf"}:
        raise NormalizationError(f"Tier 2 normalizer is not implemented for .{source_type}")
    markdown = _run_markitdown_file(source_path)
    return NormalizedMarkdown(
        markdown=_normalize_text(markdown),
        converter_name="markitdown",
    )


def _normalize_csv(path: Path) -> NormalizedMarkdown:
    text = _normalize_text(_read_text(path))
    try:
        rows = list(csv.reader(StringIO(text)))
    except csv.Error:
        return NormalizedMarkdown(
            markdown=f"```text\n{text.rstrip()}\n```\n",
            converter_name="csv_normalizer",
            warnings=("csv_parse_failed_fallback_raw",),
        )

    if not rows:
        return NormalizedMarkdown(markdown="\n", converter_name="csv_normalizer")

    width = max(len(row) for row in rows)
    padded_rows = [row + [""] * (width - len(row)) for row in rows]
    header = padded_rows[0]
    body = padded_rows[1:]
    lines = [
        "| " + " | ".join(_escape_table_cell(cell) for cell in header) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
    ]
    lines.extend(
        "| " + " | ".join(_escape_table_cell(cell) for cell in row) + " |"
        for row in body
    )
    return NormalizedMarkdown(markdown="\n".join(lines) + "\n", converter_name="csv_normalizer")


def _normalize_json(path: Path) -> NormalizedMarkdown:
    text = _read_text(path)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        normalized = _normalize_text(text).rstrip()
        return NormalizedMarkdown(
            markdown=f"```text\n{normalized}\n```\n",
            converter_name="json_normalizer",
            warnings=("json_parse_failed_fallback_raw",),
        )

    pretty = json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True)
    return NormalizedMarkdown(
        markdown=f"```json\n{pretty}\n```\n",
        converter_name="json_normalizer",
    )


def _normalize_html(path: Path) -> NormalizedMarkdown:
    try:
        markdown = _run_markitdown_html(path)
        return NormalizedMarkdown(
            markdown=_normalize_text(markdown),
            converter_name="markitdown",
        )
    except Exception as exc:
        text = _read_text(path)
        fallback = _basic_html_to_markdown(text)
        warning = _html_fallback_warning(exc)
        return NormalizedMarkdown(
            markdown=_normalize_text(fallback),
            converter_name="html_fallback_normalizer",
            warnings=(warning,),
        )


def _run_markitdown_html(path: Path) -> str:
    return _run_markitdown_file(path)


def _run_markitdown_file(path: Path) -> str:
    from markitdown import MarkItDown

    result = MarkItDown().convert(path)
    markdown = getattr(result, "markdown", None) or getattr(result, "text_content", None)
    if not isinstance(markdown, str) or not markdown.strip():
        raise NormalizationError("MarkItDown returned empty conversion output.")
    return markdown


def _basic_html_to_markdown(html: str) -> str:
    parser = _BasicMarkdownHTMLParser()
    parser.feed(html)
    parser.close()
    return parser.markdown()


def _html_fallback_warning(exc: Exception) -> str:
    if isinstance(exc, ModuleNotFoundError):
        return "markitdown_missing_fallback_html"
    return "markitdown_failed_fallback_html"


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise NormalizationError(f"Could not decode text source: {path}")


def _normalize_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized if normalized.endswith("\n") else normalized + "\n"


def _escape_table_cell(value: str) -> str:
    return value.replace("\n", " ").replace("\r", " ").replace("|", "\\|")


class _BasicMarkdownHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._ignored_depth = 0
        self._pending_prefix: str | None = None
        self._block_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        if normalized in {"script", "style"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if normalized in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(normalized[1])
            self._ensure_blank_line()
            self._pending_prefix = "#" * level + " "
            self._block_stack.append(normalized)
        elif normalized == "p":
            self._ensure_blank_line()
            self._block_stack.append(normalized)
        elif normalized == "li":
            self._ensure_new_line()
            self._pending_prefix = "- "
            self._block_stack.append(normalized)
        elif normalized == "br":
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return
        if normalized in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li"}:
            self._parts.append("\n")
            if self._block_stack:
                self._block_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        collapsed = " ".join(data.split())
        if not collapsed:
            return
        if self._pending_prefix is not None:
            self._parts.append(self._pending_prefix)
            self._pending_prefix = None
        elif self._parts and not self._parts[-1].endswith((" ", "\n")):
            self._parts.append(" ")
        self._parts.append(collapsed)

    def markdown(self) -> str:
        text = "".join(self._parts)
        lines = [line.rstrip() for line in text.splitlines()]
        compact: list[str] = []
        blank = False
        for line in lines:
            if not line:
                if not blank and compact:
                    compact.append("")
                blank = True
                continue
            compact.append(line)
            blank = False
        return "\n".join(compact).strip() + "\n"

    def _ensure_blank_line(self) -> None:
        if not self._parts:
            return
        current = "".join(self._parts)
        if not current.endswith("\n\n"):
            if current.endswith("\n"):
                self._parts.append("\n")
            else:
                self._parts.append("\n\n")

    def _ensure_new_line(self) -> None:
        if self._parts and not self._parts[-1].endswith("\n"):
            self._parts.append("\n")
