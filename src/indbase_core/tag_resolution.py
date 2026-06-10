"""Tag resolution for raw candidates (v0.3.2)."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from indbase_core.tag_blocklist import match_blocklist
from indbase_core.tags import normalize_tag_name


@dataclass(frozen=True)
class TagResolutionResult:
    raw_name: str
    normalized_name: str
    outcome: str
    canonical_tag_id: str | None
    resolved_tag_id: str | None
    merged_from_tag_id: str | None
    via_alias: bool
    blocklist_match_id: str | None
    reasons: tuple[str, ...]


def resolve_tag_candidate(
    connection: sqlite3.Connection,
    raw_name: str,
    *,
    doc_category_id: str | None = None,
) -> TagResolutionResult:
    """Resolve a raw tag string to a governance outcome before persistence."""
    clean = " ".join(raw_name.strip().split())
    if not clean:
        raise ValueError("Tag name must not be empty.")
    normalized = normalize_tag_name(clean)

    block = match_blocklist(connection, normalized)
    if block is not None:
        return TagResolutionResult(
            raw_name=clean,
            normalized_name=normalized,
            outcome="blocked",
            canonical_tag_id=None,
            resolved_tag_id=None,
            merged_from_tag_id=None,
            via_alias=False,
            blocklist_match_id=block.blocked_id,
            reasons=(f"blocklist:{block.match_type}",),
        )

    alias_row = connection.execute(
        """
        SELECT ta.tag_id, ta.alias_id
        FROM tag_aliases ta
        WHERE ta.normalized_alias = ?
          AND ta.deleted_at IS NULL
          AND ta.status = 'active'
        LIMIT 1
        """,
        (normalized,),
    ).fetchone()
    if alias_row is not None:
        return _resolve_tag_row(
            connection,
            clean,
            normalized,
            tag_id=str(alias_row["tag_id"]),
            via_alias=True,
            reasons=("alias_match",),
            merged_from_tag_id=None,
            doc_category_id=doc_category_id,
        )

    tag_row = connection.execute(
        """
        SELECT tag_id, status, merged_into_tag_id, scope, scope_category_id
        FROM tags
        WHERE normalized_name = ?
          AND deleted_at IS NULL
        """,
        (normalized,),
    ).fetchone()
    if tag_row is not None:
        return _resolve_tag_row(
            connection,
            clean,
            normalized,
            tag_id=str(tag_row["tag_id"]),
            via_alias=False,
            reasons=("formal_tag_match",),
            merged_from_tag_id=None,
            doc_category_id=doc_category_id,
            initial_row=tag_row,
        )

    return TagResolutionResult(
        raw_name=clean,
        normalized_name=normalized,
        outcome="propose_new",
        canonical_tag_id=None,
        resolved_tag_id=None,
        merged_from_tag_id=None,
        via_alias=False,
        blocklist_match_id=None,
        reasons=("no_formal_tag",),
    )


def resolve_canonical_tag_id(
    connection: sqlite3.Connection,
    tag_id: str,
) -> str | None:
    """Follow merged_into_tag_id chain to the canonical active target."""
    return _follow_merge_chain(connection, tag_id)[0]


def _resolve_tag_row(
    connection: sqlite3.Connection,
    clean: str,
    normalized: str,
    *,
    tag_id: str,
    via_alias: bool,
    reasons: tuple[str, ...],
    merged_from_tag_id: str | None,
    doc_category_id: str | None,
    initial_row: sqlite3.Row | None = None,
) -> TagResolutionResult:
    canonical_id, status, scope, scope_category_id, chain_from = _follow_merge_chain(
        connection, tag_id, initial_row=initial_row
    )
    merged_from = merged_from_tag_id or chain_from

    if status == "archived" or status == "blocked":
        return TagResolutionResult(
            raw_name=clean,
            normalized_name=normalized,
            outcome="archived",
            canonical_tag_id=canonical_id,
            resolved_tag_id=tag_id,
            merged_from_tag_id=merged_from,
            via_alias=via_alias,
            blocklist_match_id=None,
            reasons=reasons + (f"status:{status}",),
        )

    if status == "deprecated":
        return TagResolutionResult(
            raw_name=clean,
            normalized_name=normalized,
            outcome="deprecated",
            canonical_tag_id=canonical_id,
            resolved_tag_id=tag_id,
            merged_from_tag_id=merged_from,
            via_alias=via_alias,
            blocklist_match_id=None,
            reasons=reasons + ("status:deprecated",),
        )

    if canonical_id is not None and canonical_id != tag_id:
        return TagResolutionResult(
            raw_name=clean,
            normalized_name=normalized,
            outcome="merged",
            canonical_tag_id=canonical_id,
            resolved_tag_id=tag_id,
            merged_from_tag_id=merged_from or tag_id,
            via_alias=via_alias,
            blocklist_match_id=None,
            reasons=reasons + ("status:merged",),
        )

    if status != "active":
        return TagResolutionResult(
            raw_name=clean,
            normalized_name=normalized,
            outcome="deprecated",
            canonical_tag_id=canonical_id,
            resolved_tag_id=tag_id,
            merged_from_tag_id=merged_from,
            via_alias=via_alias,
            blocklist_match_id=None,
            reasons=reasons + (f"status:{status}",),
        )

    if scope == "category_bound" and doc_category_id is not None:
        if scope_category_id and scope_category_id != doc_category_id:
            return TagResolutionResult(
                raw_name=clean,
                normalized_name=normalized,
                outcome="propose_new",
                canonical_tag_id=None,
                resolved_tag_id=tag_id,
                merged_from_tag_id=merged_from,
                via_alias=via_alias,
                blocklist_match_id=None,
                reasons=reasons + ("scope_mismatch",),
            )

    return TagResolutionResult(
        raw_name=clean,
        normalized_name=normalized,
        outcome="canonical",
        canonical_tag_id=canonical_id,
        resolved_tag_id=tag_id,
        merged_from_tag_id=merged_from,
        via_alias=via_alias,
        blocklist_match_id=None,
        reasons=reasons,
    )


def _follow_merge_chain(
    connection: sqlite3.Connection,
    tag_id: str,
    *,
    initial_row: sqlite3.Row | None = None,
    max_hops: int = 16,
) -> tuple[str | None, str, str | None, str | None, str | None]:
    """Return canonical_id, status, scope, scope_category_id, first_merged_from."""
    visited: set[str] = set()
    current_id = tag_id
    first_merged_from: str | None = None
    row = initial_row

    for _ in range(max_hops):
        if current_id in visited:
            return None, "merged", None, None, first_merged_from or tag_id
        visited.add(current_id)

        if row is None:
            row = connection.execute(
                """
                SELECT tag_id, status, merged_into_tag_id, scope, scope_category_id
                FROM tags
                WHERE tag_id = ?
                """,
                (current_id,),
            ).fetchone()
        if row is None:
            return None, "archived", None, None, first_merged_from

        status = str(row["status"] or "active")
        merged_into = row["merged_into_tag_id"]
        scope = str(row["scope"] or "global")
        scope_category_id = row["scope_category_id"]

        if merged_into and str(merged_into) != current_id:
            if first_merged_from is None:
                first_merged_from = current_id
            current_id = str(merged_into)
            row = None
            continue

        return current_id, status, scope, scope_category_id, first_merged_from

    return None, "merged", None, None, first_merged_from or tag_id


def is_auto_attach_eligible(resolution: TagResolutionResult) -> bool:
    return resolution.outcome == "canonical" and resolution.canonical_tag_id is not None
