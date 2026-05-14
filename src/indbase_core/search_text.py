"""Deterministic search text helpers for FTS and CJK fallback."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

WHITESPACE_RE = re.compile(r"\s+")
ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\ufeff]")
ASCII_TERM_RE = re.compile(r"[a-z0-9_]+")

CJK_RANGES = (
    (0x3400, 0x4DBF),  # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    (0x3040, 0x309F),  # Hiragana
    (0x30A0, 0x30FF),  # Katakana
    (0xFF66, 0xFF9F),  # Halfwidth Katakana
    (0xAC00, 0xD7AF),  # Hangul syllables
)


@dataclass(frozen=True)
class PreparedSearchText:
    normalized: str
    cjk_bigrams: tuple[str, ...]
    fts_text: str


@dataclass(frozen=True)
class CjkFallbackQuery:
    normalized: str
    runs: tuple[str, ...]
    terms: tuple[str, ...]

    @property
    def has_cjk(self) -> bool:
        return bool(self.runs)


def normalize_search_text(text: str | None) -> str:
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = ZERO_WIDTH_RE.sub("", normalized)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.casefold()
    return WHITESPACE_RE.sub(" ", normalized).strip()


def contains_cjk(text: str | None) -> bool:
    if not text:
        return False
    normalized = normalize_search_text(text)
    return any(is_cjk_char(char) for char in normalized)


def is_cjk_char(char: str) -> bool:
    if len(char) != 1:
        raise ValueError("is_cjk_char expects a single character")
    codepoint = ord(char)
    if unicodedata.category(char)[0] in {"P", "S"}:
        return False
    return any(start <= codepoint <= end for start, end in CJK_RANGES)


def cjk_runs(text: str | None) -> tuple[str, ...]:
    normalized = normalize_search_text(text)
    runs: list[str] = []
    current: list[str] = []
    for char in normalized:
        if is_cjk_char(char):
            current.append(char)
            continue
        if current:
            runs.append("".join(current))
            current = []
    if current:
        runs.append("".join(current))
    return tuple(runs)


def cjk_bigrams(text: str | None, *, include_unigrams: bool = False) -> tuple[str, ...]:
    grams: list[str] = []
    for run in cjk_runs(text):
        if len(run) == 1:
            if include_unigrams:
                grams.append(run)
            continue
        grams.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tuple(_unique(grams))


def cjk_query_terms(query: str | None) -> tuple[str, ...]:
    terms: list[str] = []
    for run in cjk_runs(query):
        terms.append(run)
        terms.extend(cjk_bigrams(run, include_unigrams=True))
    return tuple(_unique(terms))


def ascii_query_terms(query: str | None) -> tuple[str, ...]:
    normalized = normalize_search_text(query)
    return tuple(_unique(ASCII_TERM_RE.findall(normalized)))


def build_fts_text(text: str | None) -> str:
    normalized = normalize_search_text(text)
    parts = [normalized]
    parts.extend(cjk_bigrams(normalized, include_unigrams=True))
    return " ".join(part for part in _unique(parts) if part)


def prepare_search_text(text: str | None) -> PreparedSearchText:
    normalized = normalize_search_text(text)
    bigrams = cjk_bigrams(normalized, include_unigrams=True)
    return PreparedSearchText(
        normalized=normalized,
        cjk_bigrams=bigrams,
        fts_text=build_fts_text(normalized),
    )


def prepare_cjk_fallback_query(query: str | None) -> CjkFallbackQuery:
    normalized = normalize_search_text(query)
    runs = cjk_runs(normalized)
    return CjkFallbackQuery(
        normalized=normalized,
        runs=runs,
        terms=cjk_query_terms(normalized),
    )


def cjk_substring_score(
    query: str | CjkFallbackQuery,
    *,
    title: str | None = None,
    heading_path: str | None = None,
    text: str | None = None,
) -> int:
    prepared = query if isinstance(query, CjkFallbackQuery) else prepare_cjk_fallback_query(query)
    if not prepared.has_cjk:
        return 0

    score = 0
    title_text = normalize_search_text(title)
    heading_text = normalize_search_text(heading_path)
    chunk_text = normalize_search_text(text)
    if _contains_any(title_text, prepared.terms):
        score += 30
    if _contains_any(heading_text, prepared.terms):
        score += 20
    if _contains_any(chunk_text, prepared.terms):
        score += 10
    return score


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return bool(text) and any(term in text for term in terms)


def _unique(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    unique_values: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique_values.append(value)
    return tuple(unique_values)
