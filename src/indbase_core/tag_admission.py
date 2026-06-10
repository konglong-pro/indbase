"""Tag admission policy for new-tag proposals (v0.3.2)."""

from __future__ import annotations

from dataclasses import dataclass
import re
import sqlite3

from indbase_core.tag_governance import POLICY_VERSION
from indbase_core.tags import normalize_tag_name

_PATH_LIKE_RE = re.compile(
    r"(?:[\\/]|\.(?:md|txt|pdf|docx)\b|^[a-z]:\\|^\w+://)",
    re.IGNORECASE,
)
_VERSION_LIKE_RE = re.compile(r"\bv?\d+(?:\.\d+){1,3}\b", re.IGNORECASE)
_DATE_LIKE_RE = re.compile(r"\b\d{4}[-_/]\d{1,2}[-_/]\d{1,2}\b")
_VAGUE_NAMES: frozenset[str] = frozenset(
    {
        "misc",
        "miscellaneous",
        "other",
        "todo",
        "tbd",
        "unknown",
        "general",
        "note",
        "notes",
        "未分类",
        "其他",
        "杂项",
    }
)
_MAX_TAG_WORDS = 8
_MAX_TAG_CHARS = 80


@dataclass(frozen=True)
class TagAdmissionResult:
    outcome: str
    policy_version: str
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]


def evaluate_tag_admission(
    connection: sqlite3.Connection,
    raw_name: str,
    *,
    normalized_name: str | None = None,
    tag_type: str = "topic",
) -> TagAdmissionResult:
    """Evaluate whether a new-tag proposal should be admitted for review."""
    normalized = normalized_name or normalize_tag_name(raw_name)
    reasons: list[str] = []
    warnings: list[str] = []

    if not normalized:
        reasons.append("empty_name")
    if len(normalized) > _MAX_TAG_CHARS:
        reasons.append("overlong")
    if len(normalized.split()) > _MAX_TAG_WORDS:
        reasons.append("overlong")
    if normalized in _VAGUE_NAMES:
        reasons.append("vague")
    if len(normalized) <= 1:
        reasons.append("vague")
    if _PATH_LIKE_RE.search(raw_name) or _PATH_LIKE_RE.search(normalized):
        reasons.append("path_like")
    if _VERSION_LIKE_RE.search(raw_name):
        reasons.append("version_like")
    if _DATE_LIKE_RE.search(raw_name):
        reasons.append("date_like")

    if _matches_category_name(connection, normalized):
        reasons.append("category_equivalent")

    existing = connection.execute(
        """
        SELECT tag_id
        FROM tags
        WHERE normalized_name = ?
          AND deleted_at IS NULL
        """,
        (normalized,),
    ).fetchone()
    if existing is not None:
        reasons.append("duplicate_formal_tag")

    pending = connection.execute(
        """
        SELECT candidate_id
        FROM tag_candidates
        WHERE normalized_name = ?
          AND status = 'pending'
        LIMIT 1
        """,
        (normalized,),
    ).fetchone()
    if pending is not None:
        warnings.append("duplicate_pending_candidate")

    if tag_type == "project" and len(normalized.split()) >= 6:
        warnings.append("over_specific")

    outcome = "rejected" if reasons else "approved"
    return TagAdmissionResult(
        outcome=outcome,
        policy_version=POLICY_VERSION,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
    )


def _matches_category_name(connection: sqlite3.Connection, normalized: str) -> bool:
    rows = connection.execute(
        """
        SELECT name
        FROM categories
        WHERE deleted_at IS NULL
          AND is_active = 1
        """
    ).fetchall()
    for row in rows:
        if normalize_tag_name(str(row["name"])) == normalized:
            return True
    alias_rows = connection.execute(
        """
        SELECT label
        FROM category_localizations
        """
    ).fetchall()
    for row in alias_rows:
        if normalize_tag_name(str(row["label"])) == normalized:
            return True
    return False
