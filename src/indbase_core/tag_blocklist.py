"""User-reviewable tag blocklist for v0.3.2."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from indbase_core.ids import new_prefixed_id
from indbase_core.tag_governance import BLOCKLIST_MATCH_TYPES, record_tag_governance_event
from indbase_core.tags import normalize_tag_name
from indbase_core.time import utc_now_iso


@dataclass(frozen=True)
class BlocklistMatch:
    blocked_id: str
    pattern: str
    normalized_pattern: str
    match_type: str
    reason: str | None


@dataclass(frozen=True)
class BlocklistEntry:
    blocked_id: str
    pattern: str
    normalized_pattern: str
    match_type: str
    reason: str | None
    source: str
    created_by: str
    created_at: str


def add_blocklist_entry(
    connection: sqlite3.Connection,
    pattern: str,
    *,
    match_type: str = "exact",
    reason: str | None = None,
    source: str = "manual",
    created_by: str = "manual",
) -> BlocklistEntry:
    clean = " ".join(pattern.strip().split())
    if not clean:
        raise ValueError("Blocklist pattern must not be empty.")
    normalized_match_type = match_type.strip().lower()
    if normalized_match_type not in BLOCKLIST_MATCH_TYPES:
        allowed = ", ".join(sorted(BLOCKLIST_MATCH_TYPES))
        raise ValueError(f"Invalid match_type {match_type!r}; expected one of: {allowed}")

    normalized_pattern = normalize_tag_name(clean)
    existing = connection.execute(
        """
        SELECT blocked_id
        FROM tag_blocklist
        WHERE normalized_pattern = ?
          AND match_type = ?
          AND deleted_at IS NULL
        """,
        (normalized_pattern, normalized_match_type),
    ).fetchone()
    if existing is not None:
        raise ValueError(f"Blocklist entry already exists for pattern: {clean}")

    blocked_id = new_prefixed_id("tagblk")
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO tag_blocklist(
          blocked_id, pattern, normalized_pattern, match_type,
          reason, source, created_by, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (blocked_id, clean, normalized_pattern, normalized_match_type, reason, source, created_by, now),
    )
    record_tag_governance_event(
        connection,
        "blocked",
        payload={"blocked_id": blocked_id, "pattern": clean, "match_type": normalized_match_type},
        created_by=created_by,
    )
    connection.commit()
    return BlocklistEntry(
        blocked_id=blocked_id,
        pattern=clean,
        normalized_pattern=normalized_pattern,
        match_type=normalized_match_type,
        reason=reason,
        source=source,
        created_by=created_by,
        created_at=now,
    )


def remove_blocklist_entry(
    connection: sqlite3.Connection,
    blocked_id: str,
    *,
    created_by: str = "manual",
) -> bool:
    row = connection.execute(
        """
        SELECT blocked_id, pattern
        FROM tag_blocklist
        WHERE blocked_id = ?
          AND deleted_at IS NULL
        """,
        (blocked_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Blocklist entry not found: {blocked_id}")
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE tag_blocklist
        SET deleted_at = ?
        WHERE blocked_id = ?
        """,
        (now, blocked_id),
    )
    record_tag_governance_event(
        connection,
        "unblocked",
        payload={"blocked_id": blocked_id, "pattern": str(row["pattern"])},
        created_by=created_by,
    )
    connection.commit()
    return True


def list_blocklist_entries(
    connection: sqlite3.Connection,
    *,
    limit: int = 100,
) -> list[BlocklistEntry]:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    rows = connection.execute(
        """
        SELECT blocked_id, pattern, normalized_pattern, match_type,
               reason, source, created_by, created_at
        FROM tag_blocklist
        WHERE deleted_at IS NULL
        ORDER BY created_at DESC, blocked_id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        BlocklistEntry(
            blocked_id=str(row["blocked_id"]),
            pattern=str(row["pattern"]),
            normalized_pattern=str(row["normalized_pattern"]),
            match_type=str(row["match_type"]),
            reason=row["reason"],
            source=str(row["source"]),
            created_by=str(row["created_by"]),
            created_at=str(row["created_at"]),
        )
        for row in rows
    ]


def match_blocklist(
    connection: sqlite3.Connection,
    normalized_name: str,
) -> BlocklistMatch | None:
    exact = connection.execute(
        """
        SELECT blocked_id, pattern, normalized_pattern, match_type, reason
        FROM tag_blocklist
        WHERE deleted_at IS NULL
          AND match_type = 'exact'
          AND normalized_pattern = ?
        LIMIT 1
        """,
        (normalized_name,),
    ).fetchone()
    if exact is not None:
        return BlocklistMatch(
            blocked_id=str(exact["blocked_id"]),
            pattern=str(exact["pattern"]),
            normalized_pattern=str(exact["normalized_pattern"]),
            match_type=str(exact["match_type"]),
            reason=exact["reason"],
        )

    for row in connection.execute(
        """
        SELECT blocked_id, pattern, normalized_pattern, match_type, reason
        FROM tag_blocklist
        WHERE deleted_at IS NULL
          AND match_type = 'contains'
        """
    ):
        needle = str(row["normalized_pattern"])
        if needle and needle in normalized_name:
            return BlocklistMatch(
                blocked_id=str(row["blocked_id"]),
                pattern=str(row["pattern"]),
                normalized_pattern=needle,
                match_type=str(row["match_type"]),
                reason=row["reason"],
            )
    return None
