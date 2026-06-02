"""Relation-backed tag search filters for v0.3.2."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from indbase_core.tag_resolution import resolve_canonical_tag_id, resolve_tag_candidate

TRUSTED_DOCUMENT_TAG_SOURCES: frozenset[str] = frozenset(
    {
        "manual",
        "accepted_candidate",
        "auto",
        "legacy_classification",
    }
)

_TRUSTED_SOURCE_SQL = ", ".join(f"'{value}'" for value in sorted(TRUSTED_DOCUMENT_TAG_SOURCES))


@dataclass(frozen=True)
class TagFilterResolution:
    tag_ref: str
    canonical_tag_id: str
    filter_tag_ids: tuple[str, ...]
    via_alias: bool
    resolved_from_merged: bool


def parse_tag_search_query(raw_query: str) -> tuple[str | None, str]:
    """Parse optional tag:<ref> prefix from a search query."""
    text = raw_query.strip()
    lowered = text.casefold()
    if not lowered.startswith("tag:"):
        return None, text
    remainder = text[len("tag:") :].lstrip()
    if not remainder:
        return None, text
    if remainder.startswith('"'):
        end = remainder.find('"', 1)
        if end > 0:
            tag_ref = remainder[1:end]
            query = remainder[end + 1 :].strip()
            return tag_ref, query
    parts = remainder.split(None, 1)
    tag_ref = parts[0]
    query = parts[1].strip() if len(parts) > 1 else ""
    return tag_ref, query


def resolve_tag_filter(connection: sqlite3.Connection, tag_ref: str) -> TagFilterResolution:
    """Resolve a tag name, alias, tag_id, or merged label to trusted filter tag IDs."""
    clean = " ".join(tag_ref.strip().split())
    if not clean:
        raise ValueError("Tag filter must not be empty.")

    if clean.startswith("tag_"):
        row = connection.execute(
            """
            SELECT tag_id, name, status
            FROM tags
            WHERE tag_id = ?
              AND deleted_at IS NULL
            """,
            (clean,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown tag filter: {tag_ref}")
        if str(row["status"]) == "archived":
            raise ValueError(f"Tag filter references archived tag: {tag_ref}")
        resolution = resolve_tag_candidate(connection, str(row["name"]))
        canonical_id = resolve_canonical_tag_id(connection, clean) or clean
    else:
        resolution = resolve_tag_candidate(connection, clean)
        if resolution.outcome == "blocked":
            raise ValueError(f"Tag filter is blocked: {tag_ref}")
        if resolution.outcome in {"archived"}:
            raise ValueError(f"Tag filter references archived tag: {tag_ref}")
        if resolution.canonical_tag_id is None:
            raise ValueError(f"Unknown tag filter: {tag_ref}")
        canonical_id = resolution.canonical_tag_id

    filter_ids = tuple(
        sorted(
            {
                str(row["tag_id"])
                for row in connection.execute(
                    """
                    SELECT tag_id
                    FROM tags
                    WHERE deleted_at IS NULL
                      AND status != 'archived'
                      AND (tag_id = ? OR merged_into_tag_id = ?)
                    """,
                    (canonical_id, canonical_id),
                )
            }
        )
    )
    if not filter_ids:
        raise ValueError(f"Unknown tag filter: {tag_ref}")

    return TagFilterResolution(
        tag_ref=clean,
        canonical_tag_id=canonical_id,
        filter_tag_ids=filter_ids,
        via_alias=resolution.via_alias,
        resolved_from_merged=resolution.outcome == "merged",
    )


def trusted_document_tag_exists_sql(*, document_alias: str = "d") -> str:
    """SQL EXISTS fragment; append tag id placeholders to params."""
    placeholders = "{tag_placeholders}"
    return f"""
        EXISTS (
          SELECT 1
          FROM document_tags filter_dt
          WHERE filter_dt.doc_id = {document_alias}.doc_id
            AND filter_dt.deleted_at IS NULL
            AND filter_dt.status = 'active'
            AND filter_dt.source IN ({_TRUSTED_SOURCE_SQL})
            AND filter_dt.tag_id IN ({placeholders})
        )
    """


def list_trusted_document_tag_names(connection: sqlite3.Connection, doc_id: str) -> list[str]:
    rows = connection.execute(
        f"""
        SELECT t.name
        FROM document_tags dt
        JOIN tags t ON t.tag_id = dt.tag_id
        WHERE dt.doc_id = ?
          AND dt.deleted_at IS NULL
          AND dt.status = 'active'
          AND dt.source IN ({_TRUSTED_SOURCE_SQL})
          AND t.deleted_at IS NULL
          AND t.status = 'active'
        ORDER BY t.name
        """,
        (doc_id,),
    ).fetchall()
    return [str(row["name"]) for row in rows]


def document_matches_tag_filter(
    connection: sqlite3.Connection,
    doc_id: str,
    filter_tag_ids: tuple[str, ...],
) -> bool:
    if not filter_tag_ids:
        return False
    placeholders = ", ".join("?" for _ in filter_tag_ids)
    row = connection.execute(
        f"""
        SELECT 1
        FROM document_tags dt
        WHERE dt.doc_id = ?
          AND dt.deleted_at IS NULL
          AND dt.status = 'active'
          AND dt.source IN ({_TRUSTED_SOURCE_SQL})
          AND dt.tag_id IN ({placeholders})
        LIMIT 1
        """,
        (doc_id, *filter_tag_ids),
    ).fetchone()
    return row is not None
