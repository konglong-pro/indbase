"""Identifier helpers for stable v0.1 entities."""

from __future__ import annotations

from datetime import datetime
import re
import secrets

DOC_ID_PATTERN = re.compile(r"^doc_(\d{8})_([0-9a-f]{6,})$")


def new_doc_id(now: datetime | None = None, short_id: str | None = None) -> str:
    timestamp = now or datetime.now().astimezone()
    suffix = short_id or random_suffix()
    return f"doc_{timestamp:%Y%m%d}_{suffix}"


def new_prefixed_id(prefix: str, now: datetime | None = None, short_id: str | None = None) -> str:
    timestamp = now or datetime.now().astimezone()
    suffix = short_id or random_suffix(length=12)
    return f"{prefix}_{timestamp:%Y%m%d}_{suffix}"


def revision_id(doc_id: str, sequence: int) -> str:
    if sequence < 1:
        raise ValueError("revision sequence must be >= 1")
    return f"rev_{doc_id}_{sequence:04d}"


def random_suffix(length: int = 6) -> str:
    if length < 1:
        raise ValueError("suffix length must be >= 1")
    return secrets.token_hex((length + 1) // 2)[:length]


def date_parts_from_doc_id(doc_id: str) -> tuple[str, str, str]:
    match = DOC_ID_PATTERN.match(doc_id)
    if not match:
        raise ValueError(f"Invalid doc_id: {doc_id!r}")
    date = match.group(1)
    return date[:4], date[4:6], date[6:8]
