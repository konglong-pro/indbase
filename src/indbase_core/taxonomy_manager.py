"""Deterministic tag manager for feature atoms -> suggestions/candidates."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import json
import sqlite3

from indbase_core.ids import new_prefixed_id
from indbase_core.profile import list_profiled_documents, show_document_profile
from indbase_core.tags import normalize_tag_name
from indbase_core.taxonomy import validate_tag_type
from indbase_core.time import utc_now_iso

HIGH_CONFIDENCE = 0.90
REVIEW_CONFIDENCE = 0.75
AMBIGUOUS_CONFIDENCE = 0.55


@dataclass(frozen=True)
class TaxonomyAnalyzeResult:
    doc_id: str
    revision_id: str
    features_scanned: int
    tag_assign_suggestions: int
    tag_candidates: int
    local_keywords: int


@dataclass(frozen=True)
class TaxonomyAnalyzeRun:
    scanned_documents: int
    results: tuple[TaxonomyAnalyzeResult, ...]


def analyze_document_taxonomy(connection: sqlite3.Connection, doc_id: str) -> TaxonomyAnalyzeResult:
    profile = show_document_profile(connection, doc_id)
    if profile is None:
        raise ValueError(f"No active profile for document {doc_id}. Run `indb profile build {doc_id}` first.")
    revision_id = str(profile["revision_id"])
    features = connection.execute(
        """
        SELECT feature_id, text, normalized_text, type, confidence, chunk_id, quote
        FROM feature_atoms
        WHERE doc_id = ?
          AND revision_id = ?
          AND status = 'active'
        ORDER BY confidence DESC, feature_id
        """,
        (doc_id, revision_id),
    ).fetchall()
    return _analyze_features(connection, doc_id, revision_id, features)


def analyze_all_profiled_documents(
    connection: sqlite3.Connection,
    *,
    limit: int = 50,
) -> TaxonomyAnalyzeRun:
    rows = list_profiled_documents(connection, limit=limit)
    results: list[TaxonomyAnalyzeResult] = []
    for row in rows:
        results.append(analyze_document_taxonomy(connection, str(row["doc_id"])))
    return TaxonomyAnalyzeRun(scanned_documents=len(rows), results=tuple(results))


def _analyze_features(
    connection: sqlite3.Connection,
    doc_id: str,
    revision_id: str,
    features: list[sqlite3.Row],
) -> TaxonomyAnalyzeResult:
    tag_assign = 0
    candidates = 0
    local_keywords = 0
    for feature in features:
        match = _best_tag_match(connection, feature)
        if match is None:
            created = _record_tag_candidate(connection, doc_id, revision_id, feature)
            _mark_feature_status(connection, str(feature["feature_id"]), "local_keyword" if not created else "active")
            if created:
                candidates += 1
            else:
                local_keywords += 1
            continue
        if match.score >= HIGH_CONFIDENCE:
            _upsert_tag_assign_suggestion(
                connection,
                doc_id=doc_id,
                revision_id=revision_id,
                feature=feature,
                tag_id=match.tag_id,
                tag_name=match.tag_name,
                confidence=match.score,
                needs_review=False,
            )
            tag_assign += 1
        elif match.score >= REVIEW_CONFIDENCE:
            _upsert_tag_assign_suggestion(
                connection,
                doc_id=doc_id,
                revision_id=revision_id,
                feature=feature,
                tag_id=match.tag_id,
                tag_name=match.tag_name,
                confidence=match.score,
                needs_review=True,
            )
            tag_assign += 1
        elif match.score >= AMBIGUOUS_CONFIDENCE:
            _upsert_tag_assign_suggestion(
                connection,
                doc_id=doc_id,
                revision_id=revision_id,
                feature=feature,
                tag_id=match.tag_id,
                tag_name=match.tag_name,
                confidence=match.score,
                needs_review=True,
            )
            tag_assign += 1
        else:
            created = _record_tag_candidate(connection, doc_id, revision_id, feature)
            if created:
                candidates += 1
            else:
                local_keywords += 1
                _mark_feature_status(connection, str(feature["feature_id"]), "local_keyword")
    connection.commit()
    return TaxonomyAnalyzeResult(
        doc_id=doc_id,
        revision_id=revision_id,
        features_scanned=len(features),
        tag_assign_suggestions=tag_assign,
        tag_candidates=candidates,
        local_keywords=local_keywords,
    )


@dataclass(frozen=True)
class TagMatch:
    tag_id: str
    tag_name: str
    score: float
    match_kind: str


def _best_tag_match(connection: sqlite3.Connection, feature: sqlite3.Row) -> TagMatch | None:
    normalized = str(feature["normalized_text"] or normalize_tag_name(str(feature["text"])))
    feature_type = validate_tag_type(str(feature["type"]))
    exact = connection.execute(
        """
        SELECT tag_id, name
        FROM tags
        WHERE normalized_name = ?
          AND type = ?
          AND deleted_at IS NULL
          AND status = 'active'
        """,
        (normalized, feature_type),
    ).fetchone()
    if exact is not None:
        return TagMatch(str(exact["tag_id"]), str(exact["name"]), 0.95, "exact_tag")

    alias = connection.execute(
        """
        SELECT t.tag_id, t.name
        FROM tag_aliases ta
        JOIN tags t ON t.tag_id = ta.tag_id
        WHERE ta.normalized_alias = ?
          AND ta.status = 'active'
          AND ta.deleted_at IS NULL
          AND t.type = ?
          AND t.deleted_at IS NULL
          AND t.status = 'active'
        """,
        (normalized, feature_type),
    ).fetchone()
    if alias is not None:
        return TagMatch(str(alias["tag_id"]), str(alias["name"]), 0.93, "exact_alias")

    best: TagMatch | None = None
    rows = connection.execute(
        """
        SELECT tag_id, name, normalized_name
        FROM tags
        WHERE type = ?
          AND deleted_at IS NULL
          AND status = 'active'
        """,
        (feature_type,),
    ).fetchall()
    for row in rows:
        score = _fuzzy_score(normalized, str(row["normalized_name"]))
        if best is None or score > best.score:
            best = TagMatch(str(row["tag_id"]), str(row["name"]), score, "fuzzy")
    return best


def _fuzzy_score(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    sequence_score = SequenceMatcher(None, left, right).ratio()
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return sequence_score
    overlap = len(left_tokens & right_tokens) / max(len(left_tokens), len(right_tokens))
    return round((sequence_score * 0.7) + (overlap * 0.3), 3)


def _upsert_tag_assign_suggestion(
    connection: sqlite3.Connection,
    *,
    doc_id: str,
    revision_id: str,
    feature: sqlite3.Row,
    tag_id: str,
    tag_name: str,
    confidence: float,
    needs_review: bool,
) -> None:
    payload = {
        "tag_id": tag_id,
        "tag_name": tag_name,
        "feature_id": feature["feature_id"],
        "feature_text": feature["text"],
        "quote": feature["quote"],
        "chunk_id": feature["chunk_id"],
        "needs_review": needs_review,
    }
    existing = connection.execute(
        """
        SELECT suggestion_id
        FROM taxonomy_suggestions
        WHERE doc_id = ?
          AND revision_id = ?
          AND type = 'tag_assign'
          AND status = 'pending'
          AND json_extract(payload_json, '$.tag_id') = ?
          AND json_extract(payload_json, '$.feature_id') = ?
        """,
        (doc_id, revision_id, tag_id, feature["feature_id"]),
    ).fetchone()
    now = utc_now_iso()
    if existing is not None:
        connection.execute(
            """
            UPDATE taxonomy_suggestions
            SET payload_json = ?, confidence = ?, updated_at = ?
            WHERE suggestion_id = ?
            """,
            (json.dumps(payload, ensure_ascii=False), confidence, now, existing["suggestion_id"]),
        )
        return
    connection.execute(
        """
        INSERT INTO taxonomy_suggestions(
          suggestion_id, type, doc_id, revision_id, target_id, payload_json,
          confidence, status, source, created_at, updated_at
        )
        VALUES (?, 'tag_assign', ?, ?, ?, ?, ?, 'pending', 'deterministic', ?, ?)
        """,
        (
            new_prefixed_id("taxsugg"),
            doc_id,
            revision_id,
            tag_id,
            json.dumps(payload, ensure_ascii=False),
            confidence,
            now,
            now,
        ),
    )


def _record_tag_candidate(
    connection: sqlite3.Connection,
    doc_id: str,
    revision_id: str,
    feature: sqlite3.Row,
) -> bool:
    name = str(feature["text"]).strip()
    normalized = str(feature["normalized_text"] or normalize_tag_name(name))
    feature_type = validate_tag_type(str(feature["type"]))
    existing = connection.execute(
        """
        SELECT candidate_id, status
        FROM tag_candidates
        WHERE normalized_name = ?
          AND type = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (normalized, feature_type),
    ).fetchone()
    now = utc_now_iso()
    evidence_docs = json.dumps([doc_id], ensure_ascii=False)
    evidence_chunks = json.dumps([str(feature["chunk_id"])], ensure_ascii=False)
    if existing is None:
        connection.execute(
            """
            INSERT INTO tag_candidates(
              candidate_id, name, normalized_name, type,
              evidence_doc_ids_json, evidence_chunk_ids_json,
              occurrence_count, distinct_doc_count, confidence,
              status, created_by, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?, 'pending', 'deterministic', ?, ?)
            """,
            (
                new_prefixed_id("tagcand"),
                name,
                normalized,
                feature_type,
                evidence_docs,
                evidence_chunks,
                float(feature["confidence"]),
                now,
                now,
            ),
        )
        return True
    status = str(existing["status"])
    if status == "pending":
        connection.execute(
            """
            UPDATE tag_candidates
            SET occurrence_count = COALESCE(occurrence_count, 0) + 1,
                evidence_doc_ids_json = ?,
                evidence_chunk_ids_json = ?,
                confidence = MAX(COALESCE(confidence, 0), ?),
                updated_at = ?
            WHERE candidate_id = ?
            """,
            (evidence_docs, evidence_chunks, float(feature["confidence"]), now, existing["candidate_id"]),
        )
        return True
    if status in {"rejected", "blocked", "merged", "archived", "accepted"}:
        return False
    return False


def _mark_feature_status(connection: sqlite3.Connection, feature_id: str, status: str) -> None:
    connection.execute(
        """
        UPDATE feature_atoms
        SET status = ?, updated_at = ?
        WHERE feature_id = ?
        """,
        (status, utc_now_iso(), feature_id),
    )
