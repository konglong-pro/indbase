"""Taxonomy constants and validation for v0.3.1."""

from __future__ import annotations

TAG_TYPES: frozenset[str] = frozenset(
    {
        "topic",
        "method",
        "tool",
        "entity",
        "workflow",
        "format",
        "language",
        "project",
    }
)

TAG_STATUSES: frozenset[str] = frozenset({"active", "deprecated", "archived", "blocked"})

ALIAS_STATUSES: frozenset[str] = frozenset({"active", "deprecated", "blocked"})

ASSIGNABLE_TAG_STATUSES: frozenset[str] = frozenset({"active"})

DOCUMENT_TAG_SOURCES: frozenset[str] = frozenset(
    {
        "manual",
        "deterministic",
        "llm_suggestion",
        "accepted_suggestion",
        "auto",
        "accepted_candidate",
        "legacy_classification",
    }
)

DOCUMENT_TAG_STATUSES: frozenset[str] = frozenset({"active", "removed"})

CATEGORY_SOURCES: frozenset[str] = frozenset(
    {
        "system_seed",
        "manual",
        "manual_legacy",
        "deterministic",
        "accepted_suggestion",
        "llm_suggestion",
    }
)

PROFILE_STATUSES: frozenset[str] = frozenset({"active", "stale"})

FEATURE_ATOM_STATUSES: frozenset[str] = frozenset(
    {"active", "stale", "promoted", "ignored", "local_keyword"}
)

TAG_CANDIDATE_STATUSES: frozenset[str] = frozenset(
    {"pending", "accepted", "rejected", "merged", "archived", "blocked"}
)

TAXONOMY_SUGGESTION_STATUSES: frozenset[str] = frozenset(
    {"pending", "accepted", "rejected", "stale", "superseded"}
)

LEGACY_TAG_TYPE_BY_NORMALIZED_NAME: dict[str, str] = {
    "ai": "topic",
    "rag": "method",
    "embedding": "method",
    "ocr": "method",
    "sqlite": "tool",
    "python": "tool",
}


def validate_tag_type(tag_type: str) -> str:
    clean = tag_type.strip().lower()
    if clean not in TAG_TYPES:
        allowed = ", ".join(sorted(TAG_TYPES))
        raise ValueError(f"Invalid tag type {tag_type!r}; expected one of: {allowed}")
    return clean


def validate_tag_status(status: str) -> str:
    clean = status.strip().lower()
    if clean not in TAG_STATUSES:
        allowed = ", ".join(sorted(TAG_STATUSES))
        raise ValueError(f"Invalid tag status {status!r}; expected one of: {allowed}")
    return clean


def validate_alias_status(status: str) -> str:
    clean = status.strip().lower()
    if clean not in ALIAS_STATUSES:
        allowed = ", ".join(sorted(ALIAS_STATUSES))
        raise ValueError(f"Invalid alias status {status!r}; expected one of: {allowed}")
    return clean


def validate_document_tag_source(source: str) -> str:
    clean = source.strip().lower()
    if clean not in DOCUMENT_TAG_SOURCES:
        allowed = ", ".join(sorted(DOCUMENT_TAG_SOURCES))
        raise ValueError(f"Invalid document tag source {source!r}; expected one of: {allowed}")
    return clean


def validate_category_source(source: str) -> str:
    clean = source.strip().lower()
    if clean not in CATEGORY_SOURCES:
        allowed = ", ".join(sorted(CATEGORY_SOURCES))
        raise ValueError(f"Invalid category source {source!r}; expected one of: {allowed}")
    return clean
