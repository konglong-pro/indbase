"""Document profile builder for v0.3.1 taxonomy foundation."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3

from indbase_core.feature_extraction import (
    MAX_FEATURES_PER_DOCUMENT,
    extract_feature_drafts,
    load_known_tag_phrases,
    validate_feature_draft,
)
from indbase_core.ids import new_prefixed_id
from indbase_core.time import utc_now_iso

PROFILE_VERSION = "deterministic-v1"


@dataclass(frozen=True)
class ProfileBuildResult:
    doc_id: str
    revision_id: str
    profile_id: str
    feature_count: int
    rebuilt: bool


def build_document_profile(
    connection: sqlite3.Connection,
    doc_id: str,
    *,
    rebuild: bool = False,
) -> ProfileBuildResult:
    document = _require_profile_eligible_document(connection, doc_id)
    revision_id = str(document["current_revision_id"])
    chunks = _load_current_revision_chunks(connection, doc_id, revision_id)
    existing = connection.execute(
        """
        SELECT profile_id
        FROM document_profiles
        WHERE doc_id = ?
          AND revision_id = ?
          AND profile_version = ?
          AND status = 'active'
        """,
        (doc_id, revision_id, PROFILE_VERSION),
    ).fetchone()
    if existing is not None and not rebuild:
        return ProfileBuildResult(
            doc_id=doc_id,
            revision_id=revision_id,
            profile_id=str(existing["profile_id"]),
            feature_count=_count_active_features(connection, doc_id, revision_id),
            rebuilt=False,
        )

    _mark_stale_profiles_and_features(connection, doc_id, revision_id)
    known_phrases = load_known_tag_phrases(connection)
    drafts = extract_feature_drafts(
        connection,
        doc_id=doc_id,
        revision_id=revision_id,
        chunks=chunks,
        known_phrases=known_phrases,
    )
    for draft in drafts:
        validate_feature_draft(draft)

    summary = _build_summary_for_classification(document, chunks, drafts)
    evidence_chunk_ids = [str(chunk["chunk_id"]) for chunk in chunks[:10]]
    features_json = [
        {
            "text": draft.text,
            "normalized_text": draft.normalized_text,
            "type": draft.feature_type,
            "confidence": draft.confidence,
            "chunk_id": draft.chunk_id,
        }
        for draft in drafts
    ]
    now = utc_now_iso()
    profile_id = new_prefixed_id("profile")
    connection.execute(
        """
        INSERT INTO document_profiles(
          profile_id, doc_id, revision_id, profile_version,
          summary_for_classification, features_json, evidence_chunk_ids_json,
          status, created_by, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 'deterministic', ?, ?)
        """,
        (
            profile_id,
            doc_id,
            revision_id,
            PROFILE_VERSION,
            summary,
            json.dumps(features_json, ensure_ascii=False),
            json.dumps(evidence_chunk_ids, ensure_ascii=False),
            now,
            now,
        ),
    )
    for draft in drafts:
        connection.execute(
            """
            INSERT INTO feature_atoms(
              feature_id, doc_id, revision_id, chunk_id, text, normalized_text,
              type, confidence, quote, source, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'deterministic', 'active', ?, ?)
            """,
            (
                new_prefixed_id("feat"),
                doc_id,
                revision_id,
                draft.chunk_id,
                draft.text,
                draft.normalized_text,
                draft.feature_type,
                draft.confidence,
                draft.quote,
                now,
                now,
            ),
        )
    connection.commit()
    return ProfileBuildResult(
        doc_id=doc_id,
        revision_id=revision_id,
        profile_id=profile_id,
        feature_count=len(drafts),
        rebuilt=True,
    )


def show_document_profile(connection: sqlite3.Connection, doc_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT profile_id, doc_id, revision_id, profile_version,
               summary_for_classification, features_json, evidence_chunk_ids_json,
               status, created_by, created_at, updated_at
        FROM document_profiles
        WHERE doc_id = ?
          AND status = 'active'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (doc_id,),
    ).fetchone()


def list_stale_profiles(connection: sqlite3.Connection, *, limit: int = 50) -> list[sqlite3.Row]:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    return list(
        connection.execute(
            """
            SELECT dp.profile_id, dp.doc_id, dp.revision_id, d.current_revision_id, dp.updated_at
            FROM document_profiles dp
            JOIN documents d ON d.doc_id = dp.doc_id
            WHERE dp.status = 'active'
              AND d.deleted_at IS NULL
              AND d.current_revision_id IS NOT NULL
              AND dp.revision_id != d.current_revision_id
            ORDER BY dp.updated_at, dp.profile_id
            LIMIT ?
            """,
            (limit,),
        )
    )


def list_profiled_documents(connection: sqlite3.Connection, *, limit: int = 50) -> list[sqlite3.Row]:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    return list(
        connection.execute(
            """
            SELECT dp.doc_id, dp.revision_id, dp.profile_id
            FROM document_profiles dp
            JOIN documents d ON d.doc_id = dp.doc_id
            WHERE dp.status = 'active'
              AND dp.revision_id = d.current_revision_id
              AND d.status = 'active'
              AND d.deleted_at IS NULL
              AND d.ingest_status = 'revisioned'
            ORDER BY dp.updated_at, dp.doc_id
            LIMIT ?
            """,
            (limit,),
        )
    )


def _require_profile_eligible_document(connection: sqlite3.Connection, doc_id: str) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT doc_id, title, language, source_type, current_revision_id, status, ingest_status
        FROM documents
        WHERE doc_id = ?
          AND deleted_at IS NULL
        """,
        (doc_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Document not found: {doc_id}")
    if str(row["status"]) == "archived":
        raise ValueError(f"Archived documents cannot build profiles: {doc_id}")
    if row["current_revision_id"] is None:
        raise ValueError(f"Source shell documents cannot build profiles: {doc_id}")
    if str(row["ingest_status"]) != "revisioned":
        raise ValueError(
            f"Document ingest_status must be revisioned before profile build: {doc_id} ({row['ingest_status']})"
        )
    return row


def _load_current_revision_chunks(
    connection: sqlite3.Connection,
    doc_id: str,
    revision_id: str,
) -> list[sqlite3.Row]:
    rows = connection.execute(
        """
        SELECT chunk_id, doc_id, revision_id, sequence, heading_path_json, text
        FROM chunks
        WHERE doc_id = ?
          AND revision_id = ?
          AND is_current = 1
          AND deleted_at IS NULL
        ORDER BY sequence, chunk_id
        """,
        (doc_id, revision_id),
    ).fetchall()
    if not rows:
        raise ValueError(f"Document revision has no active current chunks: {revision_id}")
    return list(rows)


def _mark_stale_profiles_and_features(
    connection: sqlite3.Connection,
    doc_id: str,
    current_revision_id: str,
) -> None:
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE document_profiles
        SET status = 'stale', updated_at = ?
        WHERE doc_id = ?
          AND status = 'active'
          AND revision_id != ?
        """,
        (now, doc_id, current_revision_id),
    )
    connection.execute(
        """
        UPDATE feature_atoms
        SET status = 'stale', updated_at = ?
        WHERE doc_id = ?
          AND status = 'active'
          AND revision_id != ?
        """,
        (now, doc_id, current_revision_id),
    )


def _build_summary_for_classification(
    document: sqlite3.Row,
    chunks: list[sqlite3.Row],
    drafts: list[object],
) -> str:
    lines = [
        f"title: {document['title'] or ''}",
        f"source_type: {document['source_type'] or ''}",
        f"language: {document['language'] or ''}",
    ]
    headings: list[str] = []
    for chunk in chunks[:5]:
        raw = chunk["heading_path_json"]
        if not raw:
            continue
        try:
            path = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(path, list) and path:
            headings.append(" / ".join(str(item) for item in path))
    if headings:
        lines.append("headings: " + "; ".join(headings[:5]))
    feature_texts = []
    for draft in drafts[: min(10, MAX_FEATURES_PER_DOCUMENT)]:
        feature_texts.append(getattr(draft, "text", ""))
    if feature_texts:
        lines.append("features: " + "; ".join(feature_texts))
    return "\n".join(lines).strip()


def _count_active_features(connection: sqlite3.Connection, doc_id: str, revision_id: str) -> int:
    row = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM feature_atoms
        WHERE doc_id = ?
          AND revision_id = ?
          AND status = 'active'
        """,
        (doc_id, revision_id),
    ).fetchone()
    return int(row["count"] if row else 0)
