"""Category suggestions from document profiles (v0.3.1)."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import sqlite3

from indbase_core.profile import show_document_profile
from indbase_core.taxonomy_suggestions import insert_category_assign_suggestion, mark_stale_taxonomy_suggestions
from indbase_core.tasks import add_task_event, create_task, finish_task, start_task
from indbase_core.time import utc_now_iso

_WORD_RE = re.compile(r"[\w]+", re.UNICODE)

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
class CategorySuggestionRun:
    task_id: str
    scanned_documents: int
    suggested_documents: int
    skipped_documents: int
    review_items: int


def suggest_category_assignments(
    connection: sqlite3.Connection,
    *,
    doc_id: str | None = None,
    all_uncategorized: bool = False,
    min_confidence: float = 0.65,
    limit: int = 50,
    force: bool = False,
) -> CategorySuggestionRun:
    if not 0 <= min_confidence <= 1:
        raise ValueError("min_confidence must be between 0 and 1")
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if all_uncategorized and doc_id is not None:
        raise ValueError("Provide either doc_id or all_uncategorized, not both.")

    mark_stale_taxonomy_suggestions(connection)
    task_id = create_task(
        connection,
        "category_suggest",
        input_data={
            "doc_id": doc_id,
            "all_uncategorized": all_uncategorized,
            "min_confidence": min_confidence,
            "limit": limit,
        },
    )
    start_task(connection, task_id)
    add_task_event(connection, task_id, "category_suggest_started", "Category suggestion run started.", {})

    documents = _load_profiled_documents(connection, doc_id=doc_id, all_uncategorized=all_uncategorized, limit=limit)
    if doc_id is not None and not documents:
        raise ValueError(
            f"No active profile for document {doc_id}. Run `indb profile build {doc_id}` first."
        )
    suggested = 0
    skipped = 0
    reviews = 0
    for document in documents:
        current_doc_id = str(document["doc_id"])
        revision_id = str(document["revision_id"])
        profile = show_document_profile(connection, current_doc_id)
        if profile is None:
            skipped += 1
            continue
        if profile["revision_id"] != revision_id:
            skipped += 1
            continue
        if _has_pending_category_suggestion(connection, current_doc_id, revision_id) and not force:
            skipped += 1
            continue
        if force:
            _supersede_pending_category_suggestions(connection, current_doc_id, revision_id)

        built = _build_category_suggestion(connection, document, profile)
        if built is None or float(built["confidence"]) < min_confidence:
            skipped += 1
            continue

        insert_category_assign_suggestion(
            connection,
            doc_id=current_doc_id,
            revision_id=revision_id,
            category_id=str(built["category_id"]),
            confidence=float(built["confidence"]),
            reason=str(built["reason"]),
            alternative_category_ids=list(built.get("alternative_category_ids") or []),
        )
        connection.execute(
            """
            UPDATE documents
            SET classification_status = 'suggested', updated_at = ?
            WHERE doc_id = ?
            """,
            (utc_now_iso(), current_doc_id),
        )
        suggested += 1
        reviews += 1

    result_data = {
        "scanned_documents": len(documents),
        "suggested_documents": suggested,
        "skipped_documents": skipped,
        "review_items": reviews,
    }
    finish_task(connection, task_id, "succeeded", result_data=result_data)
    add_task_event(connection, task_id, "category_suggest_finished", "Category suggestion run finished.", result_data)
    connection.commit()
    return CategorySuggestionRun(
        task_id=task_id,
        scanned_documents=len(documents),
        suggested_documents=suggested,
        skipped_documents=skipped,
        review_items=reviews,
    )


def _load_profiled_documents(
    connection: sqlite3.Connection,
    *,
    doc_id: str | None,
    all_uncategorized: bool,
    limit: int,
) -> list[sqlite3.Row]:
    clauses = [
        "d.status = 'active'",
        "d.deleted_at IS NULL",
        "d.current_revision_id IS NOT NULL",
        "d.ingest_status = 'revisioned'",
        "dp.status = 'active'",
        "dp.revision_id = d.current_revision_id",
    ]
    params: list[object] = []
    if doc_id is not None:
        clauses.append("d.doc_id = ?")
        params.append(doc_id)
    if all_uncategorized:
        clauses.append("(d.category_id IS NULL OR d.category_id = '' OR d.category_id = 'cat_uncategorized')")
    params.append(limit)
    return list(
        connection.execute(
            f"""
            SELECT d.doc_id, d.title, d.category_id, d.current_revision_id AS revision_id,
                   dp.summary_for_classification AS profile_summary
            FROM documents d
            JOIN document_profiles dp ON dp.doc_id = d.doc_id
            WHERE {" AND ".join(clauses)}
            ORDER BY COALESCE(d.updated_at, d.created_at), d.doc_id
            LIMIT ?
            """,
            params,
        )
    )


def _build_category_suggestion(
    connection: sqlite3.Connection,
    document: sqlite3.Row,
    profile: sqlite3.Row,
) -> dict[str, object] | None:
    text = "\n".join(
        [
            str(document["title"] or ""),
            str(profile["summary_for_classification"] or ""),
        ]
    )
    lowered = text.casefold()
    scores = _category_scores(connection, lowered)
    if not scores:
        return None
    category_id, confidence = scores[0]
    alternatives = [item[0] for item in scores[1:4]]
    return {
        "category_id": category_id,
        "confidence": round(confidence, 3),
        "reason": f"profile rules matched category={category_id}",
        "alternative_category_ids": alternatives,
    }


def _category_scores(connection: sqlite3.Connection, lowered_text: str) -> list[tuple[str, float]]:
    rows = connection.execute(
        """
        SELECT category_id, name, description, include_rules, exclude_rules
        FROM categories
        WHERE is_active = 1
          AND deleted_at IS NULL
          AND category_id != 'cat_uncategorized'
        """
    ).fetchall()
    feedback_boost = _accepted_category_feedback(connection)
    scores: list[tuple[str, float]] = []
    for row in rows:
        category_id = str(row["category_id"])
        keywords = set(_CATEGORY_KEYWORDS_BY_ID.get(category_id, ()))
        keywords.update(_tokens(str(row["name"] or "")))
        keywords.update(_rule_tokens(str(row["include_rules"] or "")))
        exclude = _rule_tokens(str(row["exclude_rules"] or ""))
        if exclude and _match_count(lowered_text, tuple(exclude)):
            continue
        matches = _match_count(lowered_text, tuple(keywords))
        if not matches and not keywords:
            continue
        score = min(0.95, 0.55 + matches * 0.08)
        score += feedback_boost.get(category_id, 0.0)
        if matches or feedback_boost.get(category_id):
            scores.append((category_id, score))
    return sorted(scores, key=lambda item: (-item[1], item[0]))


def _accepted_category_feedback(connection: sqlite3.Connection) -> dict[str, float]:
    rows = connection.execute(
        """
        SELECT new_category_id, COUNT(*) AS count
        FROM classification_feedback
        WHERE action IN ('accepted', 'accepted_forced')
          AND new_category_id IS NOT NULL
        GROUP BY new_category_id
        """
    ).fetchall()
    accepted_taxonomy = connection.execute(
        """
        SELECT json_extract(payload_json, '$.category_id') AS category_id, COUNT(*) AS count
        FROM taxonomy_suggestions
        WHERE type = 'category_assign'
          AND status = 'accepted'
        GROUP BY category_id
        """
    ).fetchall()
    boost: dict[str, float] = {}
    for row in rows:
        category_id = str(row["new_category_id"])
        boost[category_id] = boost.get(category_id, 0.0) + min(0.12, int(row["count"]) * 0.02)
    for row in accepted_taxonomy:
        category_id = str(row["category_id"] or "")
        if category_id:
            boost[category_id] = boost.get(category_id, 0.0) + min(0.12, int(row["count"]) * 0.02)
    return boost


def _has_pending_category_suggestion(connection: sqlite3.Connection, doc_id: str, revision_id: str) -> bool:
    row = connection.execute(
        """
        SELECT suggestion_id
        FROM taxonomy_suggestions
        WHERE doc_id = ?
          AND revision_id = ?
          AND type = 'category_assign'
          AND status = 'pending'
        LIMIT 1
        """,
        (doc_id, revision_id),
    ).fetchone()
    return row is not None


def _supersede_pending_category_suggestions(connection: sqlite3.Connection, doc_id: str, revision_id: str) -> None:
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE taxonomy_suggestions
        SET status = 'superseded', updated_at = ?
        WHERE doc_id = ?
          AND revision_id = ?
          AND type = 'category_assign'
          AND status = 'pending'
        """,
        (now, doc_id, revision_id),
    )


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(token.casefold() for token in _WORD_RE.findall(value) if len(token) >= 3)


def _rule_tokens(raw_rules: str) -> tuple[str, ...]:
    if not raw_rules.strip():
        return ()
    parts = re.split(r"[\s,;|]+", raw_rules.strip())
    return tuple(part.casefold() for part in parts if len(part) >= 3)


def _match_count(lowered_text: str, keywords: tuple[str, ...]) -> int:
    return sum(1 for keyword in keywords if keyword and keyword.casefold() in lowered_text)
