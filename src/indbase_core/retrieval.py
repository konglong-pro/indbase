"""Deterministic taxonomy-aware retrieval packages for v0.3.2."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import sqlite3

from indbase_core.ids import new_prefixed_id
from indbase_core.search import SearchOptions, SearchResult, build_snippet, search_chunks
from indbase_core.search_text import ascii_query_terms, cjk_query_terms
from indbase_core.tags import normalize_tag_name
from indbase_core.time import utc_now_iso

PLANNER_VERSION = "deterministic-v1"
MAX_TAXONOMY_BOOST = 0.25
FILTER_PATTERN = re.compile(
    r'(?:^|\s)(tag|category):(?:"([^"]+)"|(\S+))',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExplicitFilter:
    kind: str
    raw_value: str


@dataclass(frozen=True)
class ParsedRetrievalQuery:
    query_text: str
    normalized_query_text: str
    explicit_filters: tuple[ExplicitFilter, ...]


@dataclass(frozen=True)
class ResolvedTagFilter:
    tag_id: str
    display: str


@dataclass(frozen=True)
class ResolvedCategoryFilter:
    category_id: str
    display: str


@dataclass(frozen=True)
class ResolvedFilters:
    tags: tuple[ResolvedTagFilter, ...]
    categories: tuple[ResolvedCategoryFilter, ...]


@dataclass(frozen=True)
class RetrievalItemResult:
    retrieval_item_id: str
    rank: int
    doc_id: str
    revision_id: str
    chunk_id: str
    title: str | None
    source_path: str | None
    quote: str
    snippet: str
    base_score: float
    taxonomy_score: float
    final_score: float
    match_source: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalRunResult:
    retrieval_run_id: str
    query_text: str
    normalized_query_text: str
    linked_search_query_id: str | None
    filters: ResolvedFilters
    warnings: tuple[str, ...]
    top_k: int
    candidate_k: int
    per_doc_limit: int
    base_mode: str
    status: str
    result_count: int
    items: tuple[RetrievalItemResult, ...]


def parse_retrieval_query(query_text: str) -> ParsedRetrievalQuery:
    raw = query_text.strip()
    if not raw:
        raise ValueError("Query must not be empty.")
    filters: list[ExplicitFilter] = []
    spans: list[tuple[int, int]] = []
    for match in FILTER_PATTERN.finditer(raw):
        kind = match.group(1).casefold()
        value = (match.group(2) or match.group(3) or "").strip()
        if not value:
            raise ValueError(f"Explicit {kind} filter value must not be empty.")
        filters.append(ExplicitFilter(kind=kind, raw_value=value))
        spans.append((match.start(), match.end()))
    normalized_parts: list[str] = []
    cursor = 0
    for start, end in sorted(spans):
        normalized_parts.append(raw[cursor:start])
        cursor = end
    normalized_parts.append(raw[cursor:])
    normalized = " ".join(" ".join(normalized_parts).split())
    if not normalized and not filters:
        raise ValueError("Query must not be empty after removing explicit filters.")
    return ParsedRetrievalQuery(
        query_text=raw,
        normalized_query_text=normalized,
        explicit_filters=tuple(filters),
    )


def resolve_explicit_filters(
    connection: sqlite3.Connection,
    filters: tuple[ExplicitFilter, ...],
) -> ResolvedFilters:
    tags: list[ResolvedTagFilter] = []
    categories: list[ResolvedCategoryFilter] = []
    for item in filters:
        if item.kind == "tag":
            tags.append(_resolve_tag_filter(connection, item.raw_value))
        elif item.kind == "category":
            categories.append(_resolve_category_filter(connection, item.raw_value))
        else:
            raise ValueError(f"Unsupported explicit filter: {item.kind}")
    return ResolvedFilters(tags=tuple(tags), categories=tuple(categories))


def retrieve_chunks(
    connection: sqlite3.Connection,
    query_text: str,
    *,
    top_k: int = 20,
    candidate_k: int | None = None,
    per_doc_limit: int = 3,
    mode: str = "hybrid",
    search_options: SearchOptions | None = None,
) -> RetrievalRunResult:
    if top_k < 1:
        raise ValueError("top_k must be >= 1")
    if per_doc_limit < 1:
        raise ValueError("per_doc_limit must be >= 1")
    if mode not in {"fts", "vector", "hybrid"}:
        raise ValueError("mode must be fts, vector, or hybrid")

    parsed = parse_retrieval_query(query_text)
    resolved_filters = resolve_explicit_filters(connection, parsed.explicit_filters)
    effective_candidate_k = candidate_k if candidate_k is not None else max(top_k * 5, 25)
    if effective_candidate_k < 1:
        raise ValueError("candidate_k must be >= 1")

    run_id = new_prefixed_id("retrrun")
    now = utc_now_iso()
    warnings: list[str] = []
    planner_payload = {
        "planner_version": PLANNER_VERSION,
        "normalized_query_text": parsed.normalized_query_text,
        "query_terms": list(_query_terms(parsed.normalized_query_text)),
    }
    filters_payload = _filters_to_json(resolved_filters)

    if not parsed.normalized_query_text:
        _insert_retrieval_run(
            connection,
            retrieval_run_id=run_id,
            parsed=parsed,
            resolved_filters=resolved_filters,
            planner_payload=planner_payload,
            warnings=warnings,
            top_k=top_k,
            candidate_k=effective_candidate_k,
            per_doc_limit=per_doc_limit,
            mode=mode,
            linked_search_query_id=None,
            status="failed",
            result_count=0,
            created_at=now,
            finished_at=now,
        )
        connection.commit()
        return RetrievalRunResult(
            retrieval_run_id=run_id,
            query_text=parsed.query_text,
            normalized_query_text=parsed.normalized_query_text,
            linked_search_query_id=None,
            filters=resolved_filters,
            warnings=tuple(warnings),
            top_k=top_k,
            candidate_k=effective_candidate_k,
            per_doc_limit=per_doc_limit,
            base_mode=mode,
            status="failed",
            result_count=0,
            items=(),
        )

    base_options = search_options or SearchOptions()
    search_opts = SearchOptions(
        top_k=effective_candidate_k,
        log_queries=base_options.log_queries,
        persist_search_results=False,
        cjk_strategy=base_options.cjk_strategy,
        mode=mode,
    )
    search_result = search_chunks(
        connection,
        parsed.normalized_query_text,
        options=search_opts,
    )

    signals = _load_taxonomy_signals(connection, parsed.normalized_query_text)
    scored: list[tuple[SearchResult, sqlite3.Row, float, float, tuple[str, ...], list[str]]] = []
    for result in search_result.results:
        row = _load_chunk_row(connection, result.chunk_id)
        if row is None:
            continue
        if not _passes_hard_filters(connection, row, resolved_filters):
            continue
        base_score = float(result.score)
        taxonomy_score, reasons, item_warnings = _score_taxonomy_boost(
            connection,
            row,
            parsed.normalized_query_text,
            signals,
        )
        warnings.extend(item_warnings)
        final_score = base_score + taxonomy_score
        scored.append((result, row, base_score, taxonomy_score, reasons, item_warnings))

    scored.sort(key=lambda item: (-(item[2] + item[3]), item[0].chunk_id))

    diversified: list[tuple[SearchResult, sqlite3.Row, float, float, tuple[str, ...]]] = []
    per_doc_counts: dict[str, int] = {}
    for result, row, base_score, taxonomy_score, reasons, _ in scored:
        doc_id = str(row["doc_id"])
        if per_doc_counts.get(doc_id, 0) >= per_doc_limit:
            continue
        per_doc_counts[doc_id] = per_doc_counts.get(doc_id, 0) + 1
        diversified.append((result, row, base_score, taxonomy_score, reasons))

    items: list[RetrievalItemResult] = []
    for result, row, base_score, taxonomy_score, reasons in diversified[:top_k]:
        chunk_text = str(row["text"])
        quote = _extract_quote(chunk_text, parsed.normalized_query_text, row, signals)
        if not quote:
            continue
        item_id = new_prefixed_id("retritem")
        all_reasons = ("base_" + _base_reason(mode, result.match_source), *reasons)
        items.append(
            RetrievalItemResult(
                retrieval_item_id=item_id,
                rank=len(items) + 1,
                doc_id=str(row["doc_id"]),
                revision_id=str(row["revision_id"]),
                chunk_id=str(row["chunk_id"]),
                title=row["title"],
                source_path=row["source_path"],
                quote=quote,
                snippet=build_snippet(chunk_text, parsed.normalized_query_text),
                base_score=round(base_score, 6),
                taxonomy_score=round(taxonomy_score, 6),
                final_score=round(base_score + taxonomy_score, 6),
                match_source=result.match_source,
                reasons=all_reasons,
            )
        )

    status = "succeeded"
    if not items:
        status = "failed"
    elif len(items) < min(top_k, len(diversified)):
        status = "partial" if len(items) < top_k and len(scored) > 0 else status

    unique_warnings = tuple(dict.fromkeys(warnings))
    _insert_retrieval_run(
        connection,
        retrieval_run_id=run_id,
        parsed=parsed,
        resolved_filters=resolved_filters,
        planner_payload=planner_payload,
        warnings=list(unique_warnings),
        top_k=top_k,
        candidate_k=effective_candidate_k,
        per_doc_limit=per_doc_limit,
        mode=mode,
        linked_search_query_id=search_result.query_id,
        status=status,
        result_count=len(items),
        created_at=now,
        finished_at=utc_now_iso(),
    )
    for item in items:
        connection.execute(
            """
            INSERT INTO retrieval_items(
              retrieval_item_id, retrieval_run_id, rank, doc_id, revision_id, chunk_id,
              title, source_path, quote, snippet, base_score, taxonomy_score, final_score,
              match_source, reasons_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.retrieval_item_id,
                run_id,
                item.rank,
                item.doc_id,
                item.revision_id,
                item.chunk_id,
                item.title,
                item.source_path,
                item.quote,
                item.snippet,
                item.base_score,
                item.taxonomy_score,
                item.final_score,
                item.match_source,
                json.dumps(list(item.reasons), ensure_ascii=False),
                now,
            ),
        )
    connection.commit()
    return RetrievalRunResult(
        retrieval_run_id=run_id,
        query_text=parsed.query_text,
        normalized_query_text=parsed.normalized_query_text,
        linked_search_query_id=search_result.query_id,
        filters=resolved_filters,
        warnings=unique_warnings,
        top_k=top_k,
        candidate_k=effective_candidate_k,
        per_doc_limit=per_doc_limit,
        base_mode=mode,
        status=status,
        result_count=len(items),
        items=tuple(items),
    )


def list_retrieval_runs(connection: sqlite3.Connection, *, limit: int = 20) -> list[sqlite3.Row]:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    return list(
        connection.execute(
            """
            SELECT retrieval_run_id, query_text, normalized_query_text, base_mode,
                   result_count, status, created_at, finished_at
            FROM retrieval_runs
            ORDER BY created_at DESC, retrieval_run_id
            LIMIT ?
            """,
            (limit,),
        )
    )


def get_retrieval_run(connection: sqlite3.Connection, retrieval_run_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT retrieval_run_id, query_text, normalized_query_text, planner_version,
               base_mode, linked_search_query_id, filters_json, planner_json, warnings_json,
               top_k, candidate_k, per_doc_limit, result_count, status, created_at, finished_at
        FROM retrieval_runs
        WHERE retrieval_run_id = ?
        """,
        (retrieval_run_id,),
    ).fetchone()


def list_retrieval_items(connection: sqlite3.Connection, retrieval_run_id: str) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT retrieval_item_id, retrieval_run_id, rank, doc_id, revision_id, chunk_id,
                   title, source_path, quote, snippet, base_score, taxonomy_score, final_score,
                   match_source, reasons_json, created_at
            FROM retrieval_items
            WHERE retrieval_run_id = ?
            ORDER BY rank, retrieval_item_id
            """,
            (retrieval_run_id,),
        )
    )


def _resolve_tag_filter(connection: sqlite3.Connection, raw_value: str) -> ResolvedTagFilter:
    if raw_value.startswith("tag_"):
        row = connection.execute(
            """
            SELECT tag_id, name, status
            FROM tags
            WHERE tag_id = ?
              AND deleted_at IS NULL
            """,
            (raw_value,),
        ).fetchone()
        if row is None or str(row["status"]) != "active":
            raise ValueError(f"Active tag not found: {raw_value}")
        return ResolvedTagFilter(tag_id=str(row["tag_id"]), display=str(row["name"]))

    normalized = normalize_tag_name(raw_value)
    matches: dict[str, str] = {}
    for row in connection.execute(
        """
        SELECT tag_id, name
        FROM tags
        WHERE normalized_name = ?
          AND deleted_at IS NULL
          AND status = 'active'
        """,
        (normalized,),
    ).fetchall():
        matches[str(row["tag_id"])] = str(row["name"])
    for row in connection.execute(
        """
        SELECT t.tag_id, t.name
        FROM tag_aliases ta
        JOIN tags t ON t.tag_id = ta.tag_id
        WHERE ta.normalized_alias = ?
          AND ta.deleted_at IS NULL
          AND ta.status = 'active'
          AND t.deleted_at IS NULL
          AND t.status = 'active'
        """,
        (normalized,),
    ).fetchall():
        matches[str(row["tag_id"])] = str(row["name"])
    if not matches:
        raise ValueError(f"Active tag not found: {raw_value}")
    if len(matches) > 1:
        options = ", ".join(sorted(matches.values()))
        raise ValueError(f"Ambiguous tag filter {raw_value!r}; matches: {options}. Use a tag_id.")
    tag_id = next(iter(matches))
    return ResolvedTagFilter(tag_id=tag_id, display=matches[tag_id])


def _resolve_category_filter(connection: sqlite3.Connection, raw_value: str) -> ResolvedCategoryFilter:
    if raw_value.startswith("cat_"):
        row = connection.execute(
            """
            SELECT category_id, name
            FROM categories
            WHERE category_id = ?
              AND is_active = 1
              AND deleted_at IS NULL
            """,
            (raw_value,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Active category not found: {raw_value}")
        return ResolvedCategoryFilter(
            category_id=str(row["category_id"]),
            display=str(row["name"]),
        )

    normalized = raw_value.casefold().strip()
    rows = connection.execute(
        """
        SELECT DISTINCT c.category_id, c.name
        FROM categories c
        LEFT JOIN category_localizations cl ON cl.category_id = c.category_id
        WHERE c.is_active = 1
          AND c.deleted_at IS NULL
          AND (
            lower(c.name) = ?
            OR lower(COALESCE(cl.label, '')) = ?
          )
        """,
        (normalized, normalized),
    ).fetchall()
    if not rows:
        raise ValueError(f"Active category not found: {raw_value}")
    if len(rows) > 1:
        options = ", ".join(str(row["name"]) for row in rows)
        raise ValueError(
            f"Ambiguous category filter {raw_value!r}; matches: {options}. Use a category_id."
        )
    return ResolvedCategoryFilter(
        category_id=str(rows[0]["category_id"]),
        display=str(rows[0]["name"]),
    )


@dataclass
class _TaxonomySignals:
    tag_names: frozenset[str]
    category_names: frozenset[str]


def _load_taxonomy_signals(connection: sqlite3.Connection, normalized_query: str) -> _TaxonomySignals:
    terms = _query_terms(normalized_query)
    tag_names: set[str] = set()
    category_names: set[str] = set()
    for term in terms:
        for row in connection.execute(
            """
            SELECT name FROM tags
            WHERE deleted_at IS NULL AND status = 'active'
              AND (lower(name) LIKE ? OR normalized_name = ?)
            """,
            (f"%{term}%", normalize_tag_name(term)),
        ).fetchall():
            tag_names.add(str(row["name"]).casefold())
        for row in connection.execute(
            """
            SELECT t.name
            FROM tag_aliases ta
            JOIN tags t ON t.tag_id = ta.tag_id
            WHERE ta.deleted_at IS NULL AND ta.status = 'active'
              AND t.deleted_at IS NULL AND t.status = 'active'
              AND ta.normalized_alias = ?
            """,
            (normalize_tag_name(term),),
        ).fetchall():
            tag_names.add(str(row["name"]).casefold())
        for row in connection.execute(
            """
            SELECT name FROM categories
            WHERE is_active = 1 AND deleted_at IS NULL AND lower(name) LIKE ?
            """,
            (f"%{term}%",),
        ).fetchall():
            category_names.add(str(row["name"]).casefold())
    return _TaxonomySignals(tag_names=frozenset(tag_names), category_names=frozenset(category_names))


def _passes_hard_filters(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    resolved: ResolvedFilters,
) -> bool:
    doc_id = str(row["doc_id"])
    for tag in resolved.tags:
        assigned = connection.execute(
            """
            SELECT 1
            FROM document_tags dt
            WHERE dt.doc_id = ?
              AND dt.tag_id = ?
              AND dt.deleted_at IS NULL
              AND dt.status = 'active'
              AND (dt.revision_id IS NULL OR dt.revision_id = ?)
            LIMIT 1
            """,
            (doc_id, tag.tag_id, row["revision_id"]),
        ).fetchone()
        if assigned is None:
            return False
    for category in resolved.categories:
        if str(row["category_id"] or "") != category.category_id:
            return False
    return True


def _score_taxonomy_boost(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    normalized_query: str,
    signals: _TaxonomySignals,
) -> tuple[float, tuple[str, ...], list[str]]:
    reasons: list[str] = []
    warnings: list[str] = []
    boost = 0.0
    doc_id = str(row["doc_id"])
    revision_id = str(row["revision_id"])

    profile = connection.execute(
        """
        SELECT profile_id
        FROM document_profiles
        WHERE doc_id = ?
          AND revision_id = ?
          AND status = 'active'
        LIMIT 1
        """,
        (doc_id, revision_id),
    ).fetchone()
    if profile is None:
        warnings.append(f"profile_missing:{doc_id}")

    category_id = str(row["category_id"] or "")
    if category_id and category_id != "cat_uncategorized":
        cat = connection.execute(
            "SELECT name FROM categories WHERE category_id = ?",
            (category_id,),
        ).fetchone()
        if cat and str(cat["name"]).casefold() in signals.category_names:
            boost = min(MAX_TAXONOMY_BOOST, boost + 0.08)
            reasons.append("category_match")

    tag_rows = connection.execute(
        """
        SELECT t.name
        FROM document_tags dt
        JOIN tags t ON t.tag_id = dt.tag_id
        WHERE dt.doc_id = ?
          AND dt.deleted_at IS NULL
          AND dt.status = 'active'
          AND (dt.revision_id IS NULL OR dt.revision_id = ?)
          AND t.deleted_at IS NULL
          AND t.status = 'active'
        """,
        (doc_id, revision_id),
    ).fetchall()
    for tag_row in tag_rows:
        if str(tag_row["name"]).casefold() in signals.tag_names:
            boost = min(MAX_TAXONOMY_BOOST, boost + 0.08)
            if "tag_match" not in reasons:
                reasons.append("tag_match")
            break

    terms = _query_terms(normalized_query)
    for term in terms:
        feature = connection.execute(
            """
            SELECT 1
            FROM feature_atoms
            WHERE doc_id = ?
              AND revision_id = ?
              AND status = 'active'
              AND (normalized_text = ? OR lower(text) LIKE ?)
            LIMIT 1
            """,
            (doc_id, revision_id, normalize_tag_name(term), f"%{term.casefold()}%"),
        ).fetchone()
        if feature is not None:
            boost = min(MAX_TAXONOMY_BOOST, boost + 0.06)
            if "feature_atom_match" not in reasons:
                reasons.append("feature_atom_match")
            break

    reason_tuple = tuple(reasons)
    if profile is None:
        reason_tuple = (*reason_tuple, "profile_missing")
    return boost, reason_tuple, warnings


def _extract_quote(
    chunk_text: str,
    normalized_query: str,
    row: sqlite3.Row,
    signals: _TaxonomySignals,
) -> str:
    terms = _query_terms(normalized_query)
    folded = chunk_text.casefold()
    for term in sorted(terms, key=len, reverse=True):
        index = folded.find(term.casefold())
        if index >= 0:
            return _window_quote(chunk_text, index, index + len(term))
    if chunk_text.strip():
        return _window_quote(chunk_text, 0, min(len(chunk_text), 120))
    return ""


def _window_quote(text: str, start: int, end: int, *, radius: int = 80) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    quote = text[left:right]
    if not quote.strip():
        return ""
    return quote


def _load_chunk_row(connection: sqlite3.Connection, chunk_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT c.chunk_id, c.doc_id, c.revision_id, c.text,
               d.title, d.canonical_path AS source_path, d.category_id,
               d.current_revision_id, d.status, d.ingest_status
        FROM chunks c
        JOIN documents d ON d.doc_id = c.doc_id
        WHERE c.chunk_id = ?
          AND c.deleted_at IS NULL
          AND d.deleted_at IS NULL
          AND d.status = 'active'
          AND d.current_revision_id = c.revision_id
          AND c.is_current = 1
          AND d.ingest_status = 'revisioned'
        """,
        (chunk_id,),
    ).fetchone()


def _insert_retrieval_run(
    connection: sqlite3.Connection,
    *,
    retrieval_run_id: str,
    parsed: ParsedRetrievalQuery,
    resolved_filters: ResolvedFilters,
    planner_payload: dict[str, object],
    warnings: list[str],
    top_k: int,
    candidate_k: int,
    per_doc_limit: int,
    mode: str,
    linked_search_query_id: str | None,
    status: str,
    result_count: int,
    created_at: str,
    finished_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO retrieval_runs(
          retrieval_run_id, query_text, normalized_query_text, planner_version, base_mode,
          linked_search_query_id, filters_json, planner_json, warnings_json,
          top_k, candidate_k, per_doc_limit, result_count, status, created_at, finished_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            retrieval_run_id,
            parsed.query_text,
            parsed.normalized_query_text,
            PLANNER_VERSION,
            mode,
            linked_search_query_id,
            json.dumps(_filters_to_json(resolved_filters), ensure_ascii=False),
            json.dumps(planner_payload, ensure_ascii=False),
            json.dumps(warnings, ensure_ascii=False),
            top_k,
            candidate_k,
            per_doc_limit,
            result_count,
            status,
            created_at,
            finished_at,
        ),
    )


def _filters_to_json(resolved: ResolvedFilters) -> dict[str, object]:
    return {
        "tags": [{"tag_id": item.tag_id, "display": item.display} for item in resolved.tags],
        "categories": [
            {"category_id": item.category_id, "display": item.display} for item in resolved.categories
        ],
    }


def _query_terms(normalized_query: str) -> tuple[str, ...]:
    terms = list(cjk_query_terms(normalized_query))
    terms.extend(ascii_query_terms(normalized_query))
    unique: list[str] = []
    seen: set[str] = set()
    for term in terms:
        clean = term.strip()
        if len(clean) < 2 or clean.casefold() in seen:
            continue
        seen.add(clean.casefold())
        unique.append(clean)
    return tuple(unique)


def _base_reason(mode: str, match_source: str) -> str:
    if mode == "hybrid":
        return "hybrid_match"
    if mode == "vector":
        return "vector_match"
    return "fts_match"
