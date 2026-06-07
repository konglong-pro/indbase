"""Deterministic Big Category classification for v0.3.1."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
import sqlite3

from indbase_core.category_profiles import (
    PROFILE_VERSION_V1,
    list_classification_ready_profiles,
    parse_profile_cues,
    profile_snapshot,
)
from indbase_core.documents import set_document_category
from indbase_core.ids import new_prefixed_id
from indbase_core.indexer import refresh_document_fts_metadata
from indbase_core.reviews import create_review_item
from indbase_core.time import utc_now_iso

CLASSIFIER_VERSION = "v031-rules-v1"
HARNESS_VERSION = "none"
CONFIDENT_THRESHOLD = 0.85
MARGIN_THRESHOLD = 0.15
UNCATEGORIZED_ID = "cat_uncategorized"

_WORD_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)

_PRESERVE_STATUSES = frozenset({"manual", "accepted"})


@dataclass(frozen=True)
class CategoryClassificationRunResult:
    category_run_id: str
    scanned_documents: int
    confident_count: int
    suggestion_count: int
    abstained_count: int
    preserved_count: int
    error_count: int
    status: str


def run_category_classification(
    connection: sqlite3.Connection,
    *,
    trigger: str = "manual",
    doc_id: str | None = None,
    force: bool = False,
    confident_threshold: float = CONFIDENT_THRESHOLD,
    margin_threshold: float = MARGIN_THRESHOLD,
) -> CategoryClassificationRunResult:
    """Classify active current documents using profile cues and audit tables."""
    if trigger not in {
        "post_ingest",
        "manual",
        "recheck",
        "fixture_gate",
        "migration",
    }:
        raise ValueError(f"Invalid trigger: {trigger}")

    run_id = new_prefixed_id("catrun")
    now = utc_now_iso()
    snapshot = profile_snapshot(connection)
    connection.execute(
        """
        INSERT INTO category_classification_runs(
          category_run_id, trigger, classifier_version, harness_version,
          threshold, margin_threshold, profile_snapshot_json,
          scanned_documents, confident_count, suggestion_count, abstained_count,
          preserved_count, error_count, status, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0, 'running', ?)
        """,
        (
            run_id,
            trigger,
            CLASSIFIER_VERSION,
            HARNESS_VERSION,
            confident_threshold,
            margin_threshold,
            json.dumps(snapshot, ensure_ascii=False),
            now,
        ),
    )

    profiles = list_classification_ready_profiles(connection)
    documents = _load_documents(connection, doc_id=doc_id)
    confident = suggestion = abstained = preserved = errors = 0

    for document in documents:
        try:
            outcome = _classify_document(
                connection,
                run_id=run_id,
                document=document,
                profiles=profiles,
                force=force,
                confident_threshold=confident_threshold,
                margin_threshold=margin_threshold,
            )
            if outcome == "confident_assigned":
                confident += 1
            elif outcome == "suggested":
                suggestion += 1
            elif outcome in {"preserved_manual", "preserved_accepted"}:
                preserved += 1
            elif outcome == "abstained":
                abstained += 1
            elif outcome == "error":
                errors += 1
        except Exception as exc:  # noqa: BLE001 — record per-document taxonomy errors visibly
            errors += 1
            _insert_error_result(connection, run_id, document, str(exc))

    status = "succeeded"
    if errors and (confident or suggestion or abstained or preserved):
        status = "completed_with_issues"
    elif errors:
        status = "failed"

    connection.execute(
        """
        UPDATE category_classification_runs
        SET scanned_documents = ?, confident_count = ?, suggestion_count = ?,
            abstained_count = ?, preserved_count = ?, error_count = ?,
            status = ?, finished_at = ?
        WHERE category_run_id = ?
        """,
        (
            len(documents),
            confident,
            suggestion,
            abstained,
            preserved,
            errors,
            status,
            utc_now_iso(),
            run_id,
        ),
    )
    connection.commit()
    return CategoryClassificationRunResult(
        category_run_id=run_id,
        scanned_documents=len(documents),
        confident_count=confident,
        suggestion_count=suggestion,
        abstained_count=abstained,
        preserved_count=preserved,
        error_count=errors,
        status=status,
    )


def accept_category_suggestion(
    connection: sqlite3.Connection,
    category_suggestion_id: str,
    *,
    reason: str | None = None,
) -> str:
    row = connection.execute(
        """
        SELECT cs.*, d.category_id AS current_category_id
        FROM category_suggestions cs
        JOIN documents d ON d.doc_id = cs.doc_id
        WHERE cs.category_suggestion_id = ?
        """,
        (category_suggestion_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Category suggestion not found: {category_suggestion_id}")
    if row["status"] != "pending":
        raise ValueError(f"Category suggestion is not pending: {category_suggestion_id}")
    suggested = row["suggested_category_id"]
    if not suggested:
        raise ValueError("Suggestion has no category to accept.")

    set_document_category(connection, str(row["doc_id"]), str(suggested))
    connection.execute(
        """
        UPDATE documents
        SET classification_status = 'accepted', updated_at = ?
        WHERE doc_id = ?
        """,
        (utc_now_iso(), row["doc_id"]),
    )
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE category_suggestions
        SET status = 'accepted', updated_at = ?
        WHERE category_suggestion_id = ?
        """,
        (now, category_suggestion_id),
    )
    feedback_id = new_prefixed_id("catfb")
    connection.execute(
        """
        INSERT INTO category_feedback(
          category_feedback_id, doc_id, revision_id, category_result_id,
          category_suggestion_id, previous_category_id, corrected_category_id,
          action, reason, classifier_version, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'accepted', ?, ?, ?)
        """,
        (
            feedback_id,
            row["doc_id"],
            row["revision_id"],
            row["category_result_id"],
            category_suggestion_id,
            row["current_category_id"],
            suggested,
            reason,
            CLASSIFIER_VERSION,
            now,
        ),
    )
    connection.commit()
    return feedback_id


def reject_category_suggestion(
    connection: sqlite3.Connection,
    category_suggestion_id: str,
    *,
    reason: str | None = None,
) -> str:
    row = connection.execute(
        "SELECT * FROM category_suggestions WHERE category_suggestion_id = ?",
        (category_suggestion_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Category suggestion not found: {category_suggestion_id}")
    if row["status"] != "pending":
        raise ValueError(f"Category suggestion is not pending: {category_suggestion_id}")
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE category_suggestions
        SET status = 'rejected', updated_at = ?
        WHERE category_suggestion_id = ?
        """,
        (now, category_suggestion_id),
    )
    connection.execute(
        """
        UPDATE documents
        SET classification_status = 'rejected', updated_at = ?
        WHERE doc_id = ?
        """,
        (now, row["doc_id"]),
    )
    feedback_id = new_prefixed_id("catfb")
    connection.execute(
        """
        INSERT INTO category_feedback(
          category_feedback_id, doc_id, revision_id, category_result_id,
          category_suggestion_id, previous_category_id, corrected_category_id,
          action, reason, classifier_version, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, NULL, 'rejected', ?, ?, ?)
        """,
        (
            feedback_id,
            row["doc_id"],
            row["revision_id"],
            row["category_result_id"],
            category_suggestion_id,
            None,
            reason,
            CLASSIFIER_VERSION,
            now,
        ),
    )
    connection.commit()
    return feedback_id


def record_category_feedback(
    connection: sqlite3.Connection,
    doc_id: str,
    corrected_category_id: str,
    *,
    reason: str | None = None,
    action: str = "manual_override",
) -> str:
    document = connection.execute(
        """
        SELECT doc_id, current_revision_id, category_id
        FROM documents
        WHERE doc_id = ? AND deleted_at IS NULL
        """,
        (doc_id,),
    ).fetchone()
    if document is None:
        raise ValueError(f"Document not found: {doc_id}")
    previous = document["category_id"]
    set_document_category(connection, doc_id, corrected_category_id)
    connection.execute(
        """
        UPDATE documents
        SET classification_status = 'manual', updated_at = ?
        WHERE doc_id = ?
        """,
        (utc_now_iso(), doc_id),
    )
    feedback_id = new_prefixed_id("catfb")
    connection.execute(
        """
        INSERT INTO category_feedback(
          category_feedback_id, doc_id, revision_id,
          previous_category_id, corrected_category_id,
          action, reason, profile_version, classifier_version, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            feedback_id,
            doc_id,
            document["current_revision_id"],
            previous,
            corrected_category_id,
            action,
            reason,
            PROFILE_VERSION_V1,
            CLASSIFIER_VERSION,
            utc_now_iso(),
        ),
    )
    connection.commit()
    return feedback_id


def list_category_suggestions(
    connection: sqlite3.Connection,
    *,
    status: str | None = "pending",
    doc_id: str | None = None,
    limit: int = 50,
) -> list[sqlite3.Row]:
    clauses = ["1=1"]
    values: list[object] = []
    if status is not None:
        clauses.append("cs.status = ?")
        values.append(status)
    if doc_id is not None:
        clauses.append("cs.doc_id = ?")
        values.append(doc_id)
    values.append(limit)
    return list(
        connection.execute(
            f"""
            SELECT cs.*, c.name AS suggested_category_name, d.title
            FROM category_suggestions cs
            LEFT JOIN categories c ON c.category_id = cs.suggested_category_id
            JOIN documents d ON d.doc_id = cs.doc_id
            WHERE {' AND '.join(clauses)}
            ORDER BY cs.created_at DESC
            LIMIT ?
            """,
            values,
        )
    )


def resolve_category_filter(
    connection: sqlite3.Connection,
    category_ref: str,
) -> str | None:
    """Resolve category ID or localized label/name to a category_id."""
    clean = category_ref.strip()
    if not clean:
        return None
    by_id = connection.execute(
        """
        SELECT category_id
        FROM categories
        WHERE category_id = ?
          AND deleted_at IS NULL
        """,
        (clean,),
    ).fetchone()
    if by_id is not None:
        return str(by_id["category_id"])
    folded = clean.casefold()
    for row in connection.execute(
        """
        SELECT c.category_id, c.name, cl.label, cl.locale
        FROM categories c
        LEFT JOIN category_localizations cl ON cl.category_id = c.category_id
        WHERE c.deleted_at IS NULL
        """
    ):
        for candidate in (row["category_id"], row["name"], row["label"]):
            if candidate and str(candidate).casefold() == folded:
                return str(row["category_id"])
    return None


def parse_search_query(raw_query: str) -> tuple[str | None, str]:
    """Parse optional category:<ref> prefix from a search query."""
    text = raw_query.strip()
    if not text.lower().startswith("category:"):
        return None, text
    remainder = text[len("category:") :].lstrip()
    if not remainder:
        return None, text
    if remainder.startswith('"'):
        end = remainder.find('"', 1)
        if end > 0:
            category_ref = remainder[1:end]
            query = remainder[end + 1 :].strip()
            return category_ref, query
    parts = remainder.split(None, 1)
    category_ref = parts[0]
    query = parts[1].strip() if len(parts) > 1 else ""
    return category_ref, query


def _classify_document(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    document: sqlite3.Row,
    profiles: list[sqlite3.Row],
    force: bool,
    confident_threshold: float,
    margin_threshold: float,
) -> str:
    doc_id = str(document["doc_id"])
    revision_id = str(document["current_revision_id"])
    status = str(document["classification_status"] or "")
    previous_category = document["category_id"]

    if status in _PRESERVE_STATUSES and not force:
        outcome = "preserved_manual" if status == "manual" else "preserved_accepted"
        _insert_result(
            connection,
            run_id=run_id,
            doc_id=doc_id,
            revision_id=revision_id,
            previous_category_id=previous_category,
            proposed_category_id=None,
            final_category_id=previous_category,
            outcome=outcome,
            confidence=0.0,
            margin=0.0,
            evidence={"preserved": status},
            warnings=[],
        )
        return outcome

    evidence_text = _document_evidence_text(connection, document)
    scores = _score_profiles(evidence_text, profiles)
    if not scores:
        return _abstain(
            connection,
            run_id,
            document,
            previous_category,
            evidence={"reason": "no_classification_ready_profiles"},
        )

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    top_id, top_score = ranked[0]
    if len(ranked) == 1:
        margin = 1.0
    else:
        margin = top_score - ranked[1][1]
    alternatives = [item[0] for item in ranked[1:4]]
    evidence = {
        "matched_terms": _matched_terms(evidence_text, profiles, top_id),
        "scores": {key: round(value, 4) for key, value in ranked[:5]},
        "revision_id": revision_id,
    }

    if top_score >= confident_threshold and margin >= margin_threshold:
        set_document_category(connection, doc_id, top_id)
        connection.execute(
            """
            UPDATE documents
            SET classification_status = 'auto_classified', updated_at = ?
            WHERE doc_id = ?
            """,
            (utc_now_iso(), doc_id),
        )
        _insert_result(
            connection,
            run_id=run_id,
            doc_id=doc_id,
            revision_id=revision_id,
            previous_category_id=previous_category,
            proposed_category_id=top_id,
            final_category_id=top_id,
            outcome="confident_assigned",
            confidence=top_score,
            margin=margin,
            evidence=evidence,
            warnings=[],
        )
        return "confident_assigned"

    if top_score >= 0.45:
        return _suggest(
            connection,
            run_id=run_id,
            document=document,
            previous_category=previous_category,
            suggested_category_id=top_id,
            alternatives=alternatives,
            confidence=top_score,
            margin=margin,
            evidence=evidence,
        )

    return _abstain(
        connection,
        run_id,
        document,
        previous_category,
        evidence=evidence,
    )


def _abstain(
    connection: sqlite3.Connection,
    run_id: str,
    document: sqlite3.Row,
    previous_category: str | None,
    *,
    evidence: dict[str, object],
) -> str:
    doc_id = str(document["doc_id"])
    revision_id = str(document["current_revision_id"])
    final_category = previous_category or UNCATEGORIZED_ID
    if not previous_category or previous_category == UNCATEGORIZED_ID:
        connection.execute(
            """
            UPDATE documents
            SET category_id = ?, classification_status = 'abstained', updated_at = ?
            WHERE doc_id = ?
            """,
            (UNCATEGORIZED_ID, utc_now_iso(), doc_id),
        )
        refresh_document_fts_metadata(connection, doc_id)
        final_category = UNCATEGORIZED_ID
    else:
        connection.execute(
            """
            UPDATE documents
            SET classification_status = 'abstained', updated_at = ?
            WHERE doc_id = ?
            """,
            (utc_now_iso(), doc_id),
        )
    _insert_result(
        connection,
        run_id=run_id,
        doc_id=doc_id,
        revision_id=revision_id,
        previous_category_id=previous_category,
        proposed_category_id=None,
        final_category_id=final_category,
        outcome="abstained",
        confidence=0.0,
        margin=0.0,
        evidence=evidence,
        warnings=[],
    )
    return "abstained"


def _suggest(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    document: sqlite3.Row,
    previous_category: str | None,
    suggested_category_id: str,
    alternatives: list[str],
    confidence: float,
    margin: float,
    evidence: dict[str, object],
) -> str:
    doc_id = str(document["doc_id"])
    revision_id = str(document["current_revision_id"])
    connection.execute(
        """
        UPDATE documents
        SET classification_status = 'needs_review', updated_at = ?
        WHERE doc_id = ?
        """,
        (utc_now_iso(), doc_id),
    )
    result_id = _insert_result(
        connection,
        run_id=run_id,
        doc_id=doc_id,
        revision_id=revision_id,
        previous_category_id=previous_category,
        proposed_category_id=suggested_category_id,
        final_category_id=previous_category or UNCATEGORIZED_ID,
        outcome="suggested",
        confidence=confidence,
        margin=margin,
        evidence=evidence,
        warnings=[],
    )
    suggestion_id = new_prefixed_id("catsug")
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO category_suggestions(
          category_suggestion_id, category_result_id, doc_id, revision_id,
          suggested_category_id, alternative_category_ids_json,
          confidence, margin, reason, evidence_json, status, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """,
        (
            suggestion_id,
            result_id,
            doc_id,
            revision_id,
            suggested_category_id,
            json.dumps(alternatives, ensure_ascii=False),
            confidence,
            margin,
            "confidence_or_margin_below_threshold",
            json.dumps(evidence, ensure_ascii=False),
            now,
        ),
    )
    create_review_item(
        connection,
        review_type="category_suggestion",
        target_type="category_suggestion",
        target_id=suggestion_id,
        reason=f"Category suggestion for {doc_id}: {suggested_category_id}",
        priority=55,
    )
    return "suggested"


def _insert_result(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    doc_id: str,
    revision_id: str,
    previous_category_id: str | None,
    proposed_category_id: str | None,
    final_category_id: str | None,
    outcome: str,
    confidence: float,
    margin: float,
    evidence: dict[str, object],
    warnings: list[str],
) -> str:
    result_id = new_prefixed_id("catres")
    connection.execute(
        """
        INSERT INTO category_classification_results(
          category_result_id, category_run_id, doc_id, revision_id,
          previous_category_id, proposed_category_id, final_category_id,
          outcome, confidence, margin, evidence_json, warnings_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            result_id,
            run_id,
            doc_id,
            revision_id,
            previous_category_id,
            proposed_category_id,
            final_category_id,
            outcome,
            confidence,
            margin,
            json.dumps(evidence, ensure_ascii=False),
            json.dumps(warnings, ensure_ascii=False),
            utc_now_iso(),
        ),
    )
    return result_id


def _insert_error_result(
    connection: sqlite3.Connection,
    run_id: str,
    document: sqlite3.Row,
    message: str,
) -> None:
    _insert_result(
        connection,
        run_id=run_id,
        doc_id=str(document["doc_id"]),
        revision_id=str(document["current_revision_id"]),
        previous_category_id=document["category_id"],
        proposed_category_id=None,
        final_category_id=document["category_id"],
        outcome="error",
        confidence=0.0,
        margin=0.0,
        evidence={"error": message},
        warnings=[],
    )


def _load_documents(connection: sqlite3.Connection, *, doc_id: str | None) -> list[sqlite3.Row]:
    if doc_id is not None:
        row = connection.execute(
            """
            SELECT d.doc_id, d.current_revision_id, d.category_id, d.classification_status, d.title
            FROM documents d
            WHERE d.doc_id = ?
              AND d.deleted_at IS NULL
              AND d.status = 'active'
              AND d.current_revision_id IS NOT NULL
            """,
            (doc_id,),
        ).fetchone()
        return [row] if row is not None else []
    return list(
        connection.execute(
            """
            SELECT d.doc_id, d.current_revision_id, d.category_id, d.classification_status, d.title
            FROM documents d
            WHERE d.deleted_at IS NULL
              AND d.status = 'active'
              AND d.current_revision_id IS NOT NULL
              AND d.ingest_status = 'revisioned'
            ORDER BY d.created_at
            """
        )
    )


def _document_evidence_text(connection: sqlite3.Connection, document: sqlite3.Row) -> str:
    doc_id = str(document["doc_id"])
    revision_id = str(document["current_revision_id"])
    parts: list[str] = [str(document["title"] or "")]
    rows = connection.execute(
        """
        SELECT heading_path_json, text
        FROM chunks
        WHERE doc_id = ?
          AND revision_id = ?
          AND is_current = 1
          AND deleted_at IS NULL
        ORDER BY sequence
        LIMIT 32
        """,
        (doc_id, revision_id),
    ).fetchall()
    for row in rows:
        if row["heading_path_json"]:
            try:
                headings = json.loads(row["heading_path_json"])
                if isinstance(headings, list):
                    parts.extend(str(item) for item in headings)
            except json.JSONDecodeError:
                pass
        parts.append(str(row["text"] or ""))
    return "\n".join(parts).casefold()


def _score_profiles(evidence_text: str, profiles: list[sqlite3.Row]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for profile in profiles:
        category_id = str(profile["category_id"])
        positive, negative = parse_profile_cues(profile)
        pos_hits = sum(1 for cue in positive if cue.casefold() in evidence_text)
        neg_hits = sum(1 for cue in negative if cue.casefold() in evidence_text)
        if pos_hits == 0:
            continue
        base = 1 - math.exp(-0.55 * pos_hits)
        penalty = 1 / (1 + (0.35 * neg_hits))
        scores[category_id] = min(0.99, base * penalty)
    return scores


def _matched_terms(
    evidence_text: str,
    profiles: list[sqlite3.Row],
    category_id: str,
) -> list[str]:
    profile = next((row for row in profiles if row["category_id"] == category_id), None)
    if profile is None:
        return []
    positive, _ = parse_profile_cues(profile)
    return [cue for cue in positive if cue.casefold() in evidence_text]
