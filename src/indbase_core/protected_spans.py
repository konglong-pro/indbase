"""Protected span detection for transition output (v1)."""

from __future__ import annotations

from dataclasses import dataclass
import re

from indbase_core.chunker import FRONTMATTER_RE

DISPLAY_MATH_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
LATEX_BRACKET_MATH_RE = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)
FENCE_RE = re.compile(r"(^|\n)(```[^\n]*\n.*?^```)", re.MULTILINE | re.DOTALL)


@dataclass(frozen=True)
class ProtectedSpan:
    start: int
    end: int
    kind: str


def spans_for_export_markdown(markdown: str, *, input_kind: str) -> tuple[ProtectedSpan, ...]:
    spans: list[ProtectedSpan] = []
    match = FRONTMATTER_RE.match(markdown)
    if match is not None:
        spans.append(ProtectedSpan(0, match.end(), "frontmatter"))
        body = markdown[match.end() :]
        body_offset = match.end()
    else:
        body = markdown
        body_offset = 0
    spans.extend(_offset_spans(_body_spans(body, input_kind=input_kind), body_offset))
    return _merge_spans(spans)


def spans_for_normalize_body(body: str) -> tuple[ProtectedSpan, ...]:
    return _body_spans(body, input_kind="source_document")


def apply_spans(markdown: str, spans: tuple[ProtectedSpan, ...]) -> str:
    if not spans:
        return markdown
    parts: list[str] = []
    cursor = 0
    for span in spans:
        if span.start < cursor:
            continue
        parts.append(markdown[cursor : span.start])
        parts.append(markdown[span.start : span.end])
        cursor = span.end
    parts.append(markdown[cursor:])
    return "".join(parts)


def validate_protected_spans(original: str, updated: str, spans: tuple[ProtectedSpan, ...]) -> list[str]:
    errors: list[str] = []
    for span in spans:
        if span.start < 0 or span.end > len(original) or span.start >= span.end:
            errors.append(f"invalid span bounds for {span.kind}")
            continue
        if original[span.start : span.end] != updated[span.start : span.end]:
            errors.append(f"protected span changed: {span.kind} [{span.start}:{span.end}]")
    return errors


def _body_spans(body: str, *, input_kind: str) -> tuple[ProtectedSpan, ...]:
    spans: list[ProtectedSpan] = []
    spans.extend(_fenced_code_spans(body))
    spans.extend(_block_quote_spans(body))
    spans.extend(_display_math_spans(body))
    if input_kind == "translation":
        spans.extend(_translation_source_spans(body))
    elif input_kind == "accepted_atomic_note":
        spans.extend(_atomic_note_citation_spans(body))
    return _merge_spans(spans)


def _fenced_code_spans(text: str) -> list[ProtectedSpan]:
    return [ProtectedSpan(match.start(2), match.end(2), "code_fence") for match in FENCE_RE.finditer(text)]


def _block_quote_spans(text: str) -> list[ProtectedSpan]:
    spans: list[ProtectedSpan] = []
    start: int | None = None
    index = 0
    while index < len(text):
        line_end = text.find("\n", index)
        if line_end == -1:
            line_end = len(text)
        line = text[index:line_end]
        is_quote = line.startswith(">")
        if is_quote and start is None:
            start = index
        if not is_quote and start is not None:
            spans.append(ProtectedSpan(start, index, "block_quote"))
            start = None
        index = line_end + 1 if line_end < len(text) else line_end
    if start is not None:
        spans.append(ProtectedSpan(start, len(text), "block_quote"))
    return spans


def _display_math_spans(text: str) -> list[ProtectedSpan]:
    spans: list[ProtectedSpan] = []
    for pattern in (DISPLAY_MATH_RE, LATEX_BRACKET_MATH_RE):
        for match in pattern.finditer(text):
            spans.append(ProtectedSpan(match.start(), match.end(), "display_math"))
    return spans


def _translation_source_spans(body: str) -> list[ProtectedSpan]:
    spans: list[ProtectedSpan] = []
    marker = "### Source"
    translation_marker = "### Translation"
    search_from = 0
    while True:
        start = body.find(marker, search_from)
        if start == -1:
            break
        content_start = start + len(marker)
        end = body.find(translation_marker, content_start)
        if end == -1:
            end = len(body)
        spans.append(ProtectedSpan(start, end, "translation_source"))
        search_from = end
    return spans


def _atomic_note_citation_spans(body: str) -> list[ProtectedSpan]:
    spans: list[ProtectedSpan] = []
    marker = "Citations:"
    search_from = 0
    while True:
        start = body.find(marker, search_from)
        if start == -1:
            break
        section_end = body.find("\n### ", start + len(marker))
        if section_end == -1:
            section_end = len(body)
        spans.append(ProtectedSpan(start, section_end, "atomic_note_citations"))
        search_from = section_end
    return spans


def _offset_spans(spans: tuple[ProtectedSpan, ...], offset: int) -> list[ProtectedSpan]:
    return [ProtectedSpan(span.start + offset, span.end + offset, span.kind) for span in spans]


def _merge_spans(spans: list[ProtectedSpan]) -> tuple[ProtectedSpan, ...]:
    if not spans:
        return ()
    ordered = sorted(spans, key=lambda span: (span.start, span.end))
    merged: list[ProtectedSpan] = [ordered[0]]
    for span in ordered[1:]:
        last = merged[-1]
        if span.start <= last.end:
            merged[-1] = ProtectedSpan(last.start, max(last.end, span.end), last.kind)
        else:
            merged.append(span)
    return tuple(merged)
