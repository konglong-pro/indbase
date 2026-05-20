"""Deterministic classification suggestions for M8."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import sqlite3

from indbase_core.documents import set_document_category
from indbase_core.ids import new_prefixed_id
from indbase_core.reviews import create_review_item
from indbase_core.tag_candidates import record_missing_tag_candidate
from indbase_core.tags import add_document_tag, list_document_tags, normalize_tag_name
from indbase_core.taxonomy import LEGACY_TAG_TYPE_BY_NORMALIZED_NAME
from indbase_core.tasks import add_task_event, create_task, finish_task, start_task
from indbase_core.time import utc_now_iso


CLASSIFICATION_MODEL = "local/rules-v1"
PROMPT_VERSION = "m8.1"
DEFAULT_MIN_CONFIDENCE = 0.65

_WORD_RE = re.compile(r"[\w]+", re.UNICODE)

_TAG_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ai", ("artificial intelligence", "人工智能", "大语言模型", "llm", "gpt", "agent", "智能体")),
    ("rag", ("rag", "retrieval augmented", "检索增强", "知识库", "知识数据库", "knowledge base")),
    ("embedding", ("embedding", "embeddings", "vector", "vectors", "向量", "hybrid search")),
    ("ocr", ("ocr", "optical character", "scanned", "扫描", "识别")),
    ("sqlite", ("sqlite", "fts", "database", "数据库")),
    ("python", ("python", "pytest", "typer", "rich")),
)

_CATEGORY_KEYWORDS_BY_ID: dict[str, tuple[str, ...]] = {
    "cat_computer_science": (
        "artificial intelligence",
        "computer science",
        "database",
        "embedding",
        "hybrid search",
        "llm",
        "python",
        "rag",
        "sqlite",
        "人工智能",
        "大语言模型",
    ),
    "cat_math_statistics": ("math", "statistics", "probability", "regression", "数学", "统计"),
    "cat_natural_science": ("physics", "biology", "chemistry", "自然科学"),
    "cat_social_science": ("society", "policy", "economics", "social science", "社会科学"),
    "cat_humanities": ("history", "philosophy", "literature", "humanities", "人文"),
    "cat_interdisciplinary": ("interdisciplinary", "cross discipline", "跨学科"),
    "cat_tools_reference": ("tool", "tools", "reference", "workflow", "cli", "工具"),
    "cat_tools": ("tool", "tools", "workflow", "cli", "工具"),
    "cat_reference": ("reference", "documentation", "docs", "manual", "资料"),
    "cat_learning": ("learning", "tutorial", "course", "study", "学习"),
    "cat_work": ("work", "project", "meeting", "工作", "项目"),
    "cat_personal": ("personal", "journal", "private", "个人"),
}


@dataclass(frozen=True)
class ClassificationSuggestionRun:
    task_id: str
    scanned_documents: int
    suggested_documents: int
    skipped_documents: int
    review_items: int


@dataclass(frozen=True)
class ClassificationApplyResult:
    suggestion_id: str
    doc_id: str
    status: str
    category_changed: bool
    tags_added: tuple[str, ...]
    feedback_id: str


def suggest_classifications(
    connection: sqlite3.Connection,
    *,
    doc_id: str | None = None,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    limit: int = 50,
    force: bool = False,
) -> ClassificationSuggestionRun:
    """Create pending local suggestions for active current documents."""
    if not 0 <= min_confidence <= 1:
        raise ValueError("min_confidence must be between 0 and 1")
    if limit < 1:
        raise ValueError("limit must be >= 1")
    mark_stale_classification_suggestions(connection)

    task_id = create_task(
        connection,
        "classification_suggest",
        input_data={
            "doc_id": doc_id,
            "min_confidence": min_confidence,
            "limit": limit,
            "model": CLASSIFICATION_MODEL,
            "prompt_version": PROMPT_VERSION,
        },
    )
    start_task(connection, task_id)
    add_task_event(
        connection,
        task_id,
        "classification_started",
        "Classification suggestion run started.",
        {"doc_id": doc_id, "min_confidence": min_confidence, "limit": limit},
    )

    documents = _load_candidate_documents(connection, doc_id=doc_id, limit=limit)
    suggested = 0
    skipped = 0
    reviews = 0
    for document in documents:
        if _has_existing_suggestion(connection, str(document["doc_id"]), str(document["revision_id"])) and not force:
            skipped += 1
            continue
        if force:
            _supersede_pending_suggestions(connection, str(document["doc_id"]), str(document["revision_id"]))

        suggestion = _build_local_suggestion(connection, document)
        if suggestion is None or float(suggestion["confidence"]) < min_confidence:
            _update_document_classification_status(connection, str(document["doc_id"]), "no_suggestion")
            skipped += 1
            continue

        suggestion_id = _insert_suggestion(connection, suggestion)
        create_review_item(
            connection,
            review_type="classification_suggestion",
            target_type="classification_suggestion",
            target_id=suggestion_id,
            reason=_review_reason(suggestion),
            priority=60,
        )
        _update_document_classification_status(connection, str(document["doc_id"]), "suggested")
        suggested += 1
        reviews += 1

    result_data = {
        "scanned_documents": len(documents),
        "suggested_documents": suggested,
        "skipped_documents": skipped,
        "review_items": reviews,
    }
    finish_task(connection, task_id, "succeeded", result_data=result_data)
    add_task_event(connection, task_id, "classification_finished", "Classification suggestion run finished.", result_data)
    connection.commit()
    return ClassificationSuggestionRun(
        task_id=task_id,
        scanned_documents=len(documents),
        suggested_documents=suggested,
        skipped_documents=skipped,
        review_items=reviews,
    )


def list_classification_suggestions(
    connection: sqlite3.Connection,
    *,
    status: str | None = "pending",
    doc_id: str | None = None,
    limit: int = 20,
    active_current_only: bool = True,
) -> list[sqlite3.Row]:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    mark_stale_classification_suggestions(connection)
    clauses = ["cs.deleted_at IS NULL"]
    params: list[object] = []
    if status is not None:
        clauses.append("cs.status = ?")
        params.append(status)
    if doc_id is not None:
        clauses.append("cs.doc_id = ?")
        params.append(doc_id)
    if active_current_only:
        clauses.extend(
            [
                "d.status = 'active'",
                "d.deleted_at IS NULL",
                "d.current_revision_id = cs.revision_id",
            ]
        )
    params.append(limit)
    return list(
        connection.execute(
            f"""
            SELECT cs.suggestion_id, cs.doc_id, cs.revision_id, d.title,
                   cs.suggested_category_id, c.name AS suggested_category_name,
                   cs.confidence, cs.reason, cs.suggested_tags_json,
                   cs.needs_user_confirmation, cs.model, cs.prompt_version,
                   cs.status, cs.created_at, cs.updated_at
            FROM classification_suggestions cs
            JOIN documents d ON d.doc_id = cs.doc_id
            LEFT JOIN categories c ON c.category_id = cs.suggested_category_id
            WHERE {" AND ".join(clauses)}
            ORDER BY cs.created_at DESC, cs.suggestion_id
            LIMIT ?
            """,
            params,
        )
    )


def get_classification_suggestion(connection: sqlite3.Connection, suggestion_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT cs.suggestion_id, cs.doc_id, cs.revision_id, d.title,
               d.current_revision_id, d.status AS document_status,
               d.category_id AS current_category_id,
               cs.suggested_category_id, c.name AS suggested_category_name,
               cs.confidence, cs.reason, cs.alternative_category_ids_json,
               cs.suggested_tags_json, cs.needs_user_confirmation,
               cs.model, cs.prompt_version, cs.status,
               cs.created_at, cs.updated_at, cs.deleted_at
        FROM classification_suggestions cs
        JOIN documents d ON d.doc_id = cs.doc_id
        LEFT JOIN categories c ON c.category_id = cs.suggested_category_id
        WHERE cs.suggestion_id = ?
        """,
        (suggestion_id,),
    ).fetchone()


def accept_classification_suggestion(
    connection: sqlite3.Connection,
    suggestion_id: str,
    *,
    apply_category: bool = True,
    apply_tags: bool = True,
    force_category: bool = False,
    reason: str | None = None,
) -> ClassificationApplyResult:
    row = _require_pending_suggestion(connection, suggestion_id)
    doc_id = str(row["doc_id"])
    _require_current_active_suggestion(row)

    old_category = str(row["current_category_id"] or "")
    old_tags = _document_tag_names(connection, doc_id)
    category_changed = False
    forced_category = False
    if apply_category and row["suggested_category_id"]:
        can_replace_category = old_category in {"", "cat_uncategorized"} or force_category
        if can_replace_category:
            result = set_document_category(connection, doc_id, str(row["suggested_category_id"]))
            category_changed = result.changed
            forced_category = (
                category_changed
                and bool(force_category)
                and old_category not in {"", "cat_uncategorized"}
            )

    tags_added: list[str] = []
    if apply_tags:
        for tag_name in _json_list(row["suggested_tags_json"]):
            try:
                change = add_document_tag(connection, doc_id, tag_name)
            except ValueError:
                normalized = normalize_tag_name(tag_name)
                tag_type = LEGACY_TAG_TYPE_BY_NORMALIZED_NAME.get(normalized, "topic")
                record_missing_tag_candidate(
                    connection,
                    doc_id=doc_id,
                    revision_id=str(row["revision_id"]),
                    tag_name=tag_name,
                    tag_type=tag_type,
                )
                continue
            if change.changed:
                tags_added.append(change.tag_name)

    new_category = _document_category_id(connection, doc_id)
    new_tags = _document_tag_names(connection, doc_id)
    feedback_id = _create_feedback(
        connection,
        suggestion_id=suggestion_id,
        doc_id=doc_id,
        revision_id=str(row["revision_id"]),
        action="accepted_forced" if forced_category else "accepted",
        old_category_id=old_category or None,
        new_category_id=new_category,
        old_tags=old_tags,
        new_tags=new_tags,
        reason=reason or f"accepted classification suggestion {suggestion_id}",
        forced_category=forced_category,
    )
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE classification_suggestions
        SET status = 'accepted', needs_user_confirmation = 0, updated_at = ?
        WHERE suggestion_id = ?
        """,
        (now, suggestion_id),
    )
    _update_document_classification_status(connection, doc_id, "accepted")
    _resolve_classification_review(connection, suggestion_id, reason or "accepted")
    connection.commit()
    return ClassificationApplyResult(
        suggestion_id=suggestion_id,
        doc_id=doc_id,
        status="accepted",
        category_changed=category_changed,
        tags_added=tuple(tags_added),
        feedback_id=feedback_id,
    )


def reject_classification_suggestion(
    connection: sqlite3.Connection,
    suggestion_id: str,
    *,
    reason: str | None = None,
) -> ClassificationApplyResult:
    row = _require_pending_suggestion(connection, suggestion_id)
    doc_id = str(row["doc_id"])
    old_category = str(row["current_category_id"] or "")
    old_tags = _document_tag_names(connection, doc_id)
    feedback_id = _create_feedback(
        connection,
        suggestion_id=suggestion_id,
        doc_id=doc_id,
        revision_id=str(row["revision_id"]),
        action="rejected",
        old_category_id=old_category or None,
        new_category_id=old_category or None,
        old_tags=old_tags,
        new_tags=old_tags,
        reason=reason or f"rejected classification suggestion {suggestion_id}",
        forced_category=False,
    )
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE classification_suggestions
        SET status = 'rejected', needs_user_confirmation = 0, updated_at = ?
        WHERE suggestion_id = ?
        """,
        (now, suggestion_id),
    )
    _update_document_classification_status(connection, doc_id, "rejected")
    _resolve_classification_review(connection, suggestion_id, reason or "rejected")
    connection.commit()
    return ClassificationApplyResult(
        suggestion_id=suggestion_id,
        doc_id=doc_id,
        status="rejected",
        category_changed=False,
        tags_added=(),
        feedback_id=feedback_id,
    )


def mark_stale_classification_suggestions(connection: sqlite3.Connection) -> int:
    """Mark pending suggestions stale when they no longer bind to the current revision."""
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE classification_suggestions
        SET status = 'stale',
            needs_user_confirmation = 0,
            updated_at = ?
        WHERE status = 'pending'
          AND deleted_at IS NULL
          AND NOT EXISTS (
            SELECT 1
            FROM documents d
            WHERE d.doc_id = classification_suggestions.doc_id
              AND d.deleted_at IS NULL
              AND d.current_revision_id = classification_suggestions.revision_id
          )
        """,
        (now,),
    )
    return int(connection.execute("SELECT changes() AS count").fetchone()["count"] or 0)


def _load_candidate_documents(
    connection: sqlite3.Connection,
    *,
    doc_id: str | None,
    limit: int,
) -> list[sqlite3.Row]:
    clauses = [
        "d.status = 'active'",
        "d.deleted_at IS NULL",
        "d.current_revision_id IS NOT NULL",
        "d.ingest_status = 'revisioned'",
        "c.is_current = 1",
        "c.deleted_at IS NULL",
        "c.revision_id = d.current_revision_id",
    ]
    params: list[object] = []
    if doc_id is not None:
        clauses.append("d.doc_id = ?")
        params.append(doc_id)
    params.append(limit)
    return list(
        connection.execute(
            f"""
            SELECT d.doc_id, d.title, d.category_id, d.current_revision_id AS revision_id,
                   GROUP_CONCAT(c.text, CHAR(10)) AS text
            FROM documents d
            JOIN chunks c ON c.doc_id = d.doc_id
            WHERE {" AND ".join(clauses)}
            GROUP BY d.doc_id
            ORDER BY COALESCE(d.updated_at, d.created_at), d.doc_id
            LIMIT ?
            """,
            params,
        )
    )


def _build_local_suggestion(connection: sqlite3.Connection, document: sqlite3.Row) -> dict[str, object] | None:
    text = f"{document['title'] or ''}\n{document['text'] or ''}"
    lowered = text.casefold()
    category_scores = _category_scores(connection, lowered)
    tag_scores = _tag_scores(lowered)
    suggested_category_id = category_scores[0][0] if category_scores else None
    alternative_category_ids = [category_id for category_id, _score in category_scores[1:4]]
    suggested_tags = [tag for tag, _score in tag_scores[:5]]
    confidence_values = [score for _category_id, score in category_scores[:1]] + [
        score for _tag, score in tag_scores[:1]
    ]
    if not suggested_category_id and not suggested_tags:
        return None
    confidence = round(max(confidence_values) if confidence_values else 0.0, 3)
    reason_parts: list[str] = []
    if suggested_category_id:
        reason_parts.append(f"category={suggested_category_id}")
    if suggested_tags:
        reason_parts.append(f"tags={', '.join(suggested_tags)}")
    return {
        "doc_id": str(document["doc_id"]),
        "revision_id": str(document["revision_id"]),
        "suggested_category_id": suggested_category_id,
        "confidence": confidence,
        "reason": "local rules matched " + "; ".join(reason_parts),
        "alternative_category_ids": alternative_category_ids,
        "suggested_tags": suggested_tags,
    }


def _category_scores(connection: sqlite3.Connection, lowered_text: str) -> list[tuple[str, float]]:
    rows = connection.execute(
        """
        SELECT category_id, name
        FROM categories
        WHERE is_active = 1
          AND deleted_at IS NULL
          AND category_id != 'cat_uncategorized'
        """
    ).fetchall()
    scores: list[tuple[str, float]] = []
    for row in rows:
        category_id = str(row["category_id"])
        keywords = set(_CATEGORY_KEYWORDS_BY_ID.get(category_id, ()))
        keywords.update(_tokens(str(row["name"] or "")))
        matches = _match_count(lowered_text, tuple(keywords))
        if matches:
            scores.append((category_id, min(0.95, 0.6 + matches * 0.08)))
    return sorted(scores, key=lambda item: (-item[1], item[0]))


def _tag_scores(lowered_text: str) -> list[tuple[str, float]]:
    scores: list[tuple[str, float]] = []
    for tag, keywords in _TAG_KEYWORDS:
        matches = _match_count(lowered_text, keywords)
        if matches:
            scores.append((tag, min(0.92, 0.62 + matches * 0.08)))
    return sorted(scores, key=lambda item: (-item[1], item[0]))


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(token.casefold() for token in _WORD_RE.findall(value) if len(token) >= 3)


def _match_count(lowered_text: str, keywords: tuple[str, ...]) -> int:
    return sum(1 for keyword in keywords if keyword and keyword.casefold() in lowered_text)


def _insert_suggestion(connection: sqlite3.Connection, suggestion: dict[str, object]) -> str:
    suggestion_id = new_prefixed_id("suggestion")
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO classification_suggestions(
          suggestion_id, doc_id, revision_id, suggested_category_id, confidence,
          reason, alternative_category_ids_json, suggested_tags_json,
          needs_user_confirmation, model, prompt_version, status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'pending', ?, ?)
        """,
        (
            suggestion_id,
            suggestion["doc_id"],
            suggestion["revision_id"],
            suggestion["suggested_category_id"],
            suggestion["confidence"],
            suggestion["reason"],
            _json_list_value(suggestion["alternative_category_ids"]),
            _json_list_value(suggestion["suggested_tags"]),
            CLASSIFICATION_MODEL,
            PROMPT_VERSION,
            now,
            now,
        ),
    )
    return suggestion_id


def _has_existing_suggestion(connection: sqlite3.Connection, doc_id: str, revision_id: str) -> bool:
    row = connection.execute(
        """
        SELECT suggestion_id
        FROM classification_suggestions
        WHERE doc_id = ?
          AND revision_id = ?
          AND model = ?
          AND prompt_version = ?
          AND deleted_at IS NULL
          AND status IN ('pending', 'accepted', 'rejected')
        LIMIT 1
        """,
        (doc_id, revision_id, CLASSIFICATION_MODEL, PROMPT_VERSION),
    ).fetchone()
    return row is not None


def _supersede_pending_suggestions(connection: sqlite3.Connection, doc_id: str, revision_id: str) -> None:
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE classification_suggestions
        SET status = 'superseded', deleted_at = ?, updated_at = ?
        WHERE doc_id = ?
          AND revision_id = ?
          AND model = ?
          AND prompt_version = ?
          AND status = 'pending'
          AND deleted_at IS NULL
        """,
        (now, now, doc_id, revision_id, CLASSIFICATION_MODEL, PROMPT_VERSION),
    )


def _require_pending_suggestion(connection: sqlite3.Connection, suggestion_id: str) -> sqlite3.Row:
    mark_stale_classification_suggestions(connection)
    row = get_classification_suggestion(connection, suggestion_id)
    if row is None or row["deleted_at"] is not None:
        raise ValueError(f"Classification suggestion not found: {suggestion_id}")
    if row["status"] != "pending":
        raise ValueError(f"Classification suggestion is not pending: {suggestion_id}")
    return row


def _require_current_active_suggestion(row: sqlite3.Row) -> None:
    if row["document_status"] != "active":
        raise ValueError(f"Document is not active: {row['doc_id']}")
    if row["current_revision_id"] != row["revision_id"]:
        raise ValueError(f"Classification suggestion is stale for document: {row['doc_id']}")


def _create_feedback(
    connection: sqlite3.Connection,
    *,
    suggestion_id: str,
    doc_id: str,
    revision_id: str,
    action: str,
    old_category_id: str | None,
    new_category_id: str | None,
    old_tags: tuple[str, ...],
    new_tags: tuple[str, ...],
    reason: str,
    forced_category: bool,
) -> str:
    feedback_id = new_prefixed_id("feedback")
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO classification_feedback(
          feedback_id, doc_id, suggestion_id, revision_id, action,
          old_category_id, new_category_id, old_tags_json, new_tags_json,
          reason, forced_category, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            feedback_id,
            doc_id,
            suggestion_id,
            revision_id,
            action,
            old_category_id,
            new_category_id,
            _json_list_value(old_tags),
            _json_list_value(new_tags),
            reason,
            1 if forced_category else 0,
            now,
            now,
        ),
    )
    return feedback_id


def _resolve_classification_review(connection: sqlite3.Connection, suggestion_id: str, note: str) -> None:
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE review_items
        SET status = 'resolved',
            resolved_at = COALESCE(resolved_at, ?),
            resolution_note = COALESCE(resolution_note, ?),
            resolved_by = COALESCE(resolved_by, 'classification'),
            updated_at = ?
        WHERE type = 'classification_suggestion'
          AND target_type = 'classification_suggestion'
          AND target_id = ?
          AND status = 'pending'
        """,
        (now, note, now, suggestion_id),
    )


def _update_document_classification_status(connection: sqlite3.Connection, doc_id: str, status: str) -> None:
    connection.execute(
        """
        UPDATE documents
        SET classification_status = ?, updated_at = ?
        WHERE doc_id = ?
        """,
        (status, utc_now_iso(), doc_id),
    )


def _document_category_id(connection: sqlite3.Connection, doc_id: str) -> str | None:
    row = connection.execute("SELECT category_id FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
    return None if row is None else row["category_id"]


def _document_tag_names(connection: sqlite3.Connection, doc_id: str) -> tuple[str, ...]:
    return tuple(str(row["name"]) for row in list_document_tags(connection, doc_id))


def _json_list(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    parsed = json.loads(str(value))
    if not isinstance(parsed, list):
        return ()
    return tuple(str(item) for item in parsed)


def _json_list_value(value: object) -> str:
    return json.dumps(list(value or []), ensure_ascii=False, sort_keys=True)


def _review_reason(suggestion: dict[str, object]) -> str:
    return (
        f"Confirm classification suggestion for {suggestion['doc_id']}: "
        f"{suggestion['reason']} confidence={suggestion['confidence']}"
    )
