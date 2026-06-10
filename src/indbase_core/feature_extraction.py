"""Deterministic feature atom extraction for v0.3.1 profiles."""

from __future__ import annotations

from dataclasses import dataclass
import re
import sqlite3

from indbase_core.tags import normalize_tag_name
from indbase_core.taxonomy import TAG_TYPES, validate_tag_type

MAX_FEATURES_PER_CHUNK = 5
MAX_FEATURES_PER_DOCUMENT = 30

ENGLISH_PHRASE_RE = re.compile(r"\b([a-z][a-z0-9]*(?:\s+[a-z][a-z0-9]*){1,3})\b", re.IGNORECASE)
URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
FENCE_RE = re.compile(r"```")
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "with",
    }
)

_TYPE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("method", ("rag", "retrieval", "hybrid search", "embedding", "workflow")),
    ("tool", ("sqlite", "python", "pytest", "typer", "fts")),
    ("topic", ("artificial intelligence", "llm", "agent", "database", "knowledge")),
    ("format", ("markdown", "html", "pdf", "json", "csv")),
    ("language", ("english", "chinese", "japanese")),
)


@dataclass(frozen=True)
class FeatureDraft:
    text: str
    normalized_text: str
    feature_type: str
    confidence: float
    chunk_id: str
    quote: str


def extract_feature_drafts(
    connection: sqlite3.Connection,
    *,
    doc_id: str,
    revision_id: str,
    chunks: list[sqlite3.Row],
    known_phrases: tuple[str, ...],
) -> list[FeatureDraft]:
    drafts: list[FeatureDraft] = []
    seen_normalized: set[str] = set()
    for chunk in chunks:
        chunk_text = str(chunk["text"] or "")
        chunk_id = str(chunk["chunk_id"])
        per_chunk = 0
        for phrase in _ordered_phrase_candidates(chunk_text, known_phrases):
            if per_chunk >= MAX_FEATURES_PER_CHUNK:
                break
            if len(drafts) >= MAX_FEATURES_PER_DOCUMENT:
                return drafts
            normalized = normalize_tag_name(phrase)
            if not normalized or normalized in seen_normalized:
                continue
            quote = _find_quote(chunk_text, phrase)
            if quote is None:
                continue
            feature_type = _infer_feature_type(phrase)
            confidence = _confidence_for_phrase(phrase, known_phrases)
            drafts.append(
                FeatureDraft(
                    text=phrase,
                    normalized_text=normalized,
                    feature_type=feature_type,
                    confidence=confidence,
                    chunk_id=chunk_id,
                    quote=quote,
                )
            )
            seen_normalized.add(normalized)
            per_chunk += 1
    return drafts


def _ordered_phrase_candidates(chunk_text: str, known_phrases: tuple[str, ...]) -> list[str]:
    ordered: list[str] = []
    lowered = chunk_text.casefold()
    for phrase in known_phrases:
        if phrase and phrase.casefold() in lowered:
            ordered.append(phrase)
    if FENCE_RE.search(chunk_text) or URL_RE.search(chunk_text):
        return ordered
    for match in ENGLISH_PHRASE_RE.finditer(chunk_text):
        phrase = " ".join(match.group(1).split())
        words = phrase.split()
        if len(words) < 2 or len(words) > 4:
            continue
        if any(len(word) < 2 for word in words):
            continue
        if all(word.casefold() in STOPWORDS for word in words):
            continue
        if phrase.casefold() in {item.casefold() for item in ordered}:
            continue
        ordered.append(phrase)
    return ordered


def _find_quote(chunk_text: str, phrase: str) -> str | None:
    start = chunk_text.casefold().find(phrase.casefold())
    if start < 0:
        return None
    return chunk_text[start : start + len(phrase)]


def _infer_feature_type(phrase: str) -> str:
    lowered = phrase.casefold()
    for feature_type, keywords in _TYPE_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return validate_tag_type(feature_type)
    return "topic"


def _confidence_for_phrase(phrase: str, known_phrases: tuple[str, ...]) -> float:
    if phrase in known_phrases:
        return 0.92
    words = phrase.split()
    if len(words) >= 3:
        return 0.78
    return 0.7


def load_known_tag_phrases(connection: sqlite3.Connection) -> tuple[str, ...]:
    phrases: list[str] = []
    tag_rows = connection.execute(
        """
        SELECT name
        FROM tags
        WHERE deleted_at IS NULL
          AND status = 'active'
        ORDER BY name
        """
    ).fetchall()
    alias_rows = connection.execute(
        """
        SELECT alias
        FROM tag_aliases
        WHERE deleted_at IS NULL
          AND status = 'active'
        ORDER BY alias
        """
    ).fetchall()
    for row in tag_rows:
        name = str(row["name"]).strip()
        if name:
            phrases.append(name)
    for row in alias_rows:
        alias = str(row["alias"]).strip()
        if alias:
            phrases.append(alias)
    return tuple(phrases)


def validate_feature_draft(draft: FeatureDraft) -> None:
    validate_tag_type(draft.feature_type)
    if draft.feature_type not in TAG_TYPES:
        raise ValueError(f"Invalid feature type: {draft.feature_type}")
    if not draft.quote:
        raise ValueError("Feature quote is required.")
    if not 0.0 <= draft.confidence <= 1.0:
        raise ValueError("Feature confidence must be between 0 and 1.")
