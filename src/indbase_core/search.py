"""Source snippet search over current chunks."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import sqlite3

from indbase_core.config import SearchConfig
from indbase_core.embeddings import DeterministicEmbeddingAdapter
from indbase_core.ids import new_prefixed_id
from indbase_core.search_text import (
    ascii_query_terms,
    build_fts_text,
    cjk_query_terms,
    cjk_substring_score,
    prepare_cjk_fallback_query,
)
from indbase_core.time import utc_now_iso


@dataclass(frozen=True)
class SearchOptions:
    top_k: int = 20
    log_queries: bool = True
    persist_search_results: bool = False
    cjk_strategy: str = "substring_fallback"
    mode: str = "fts"
    category_id: str | None = None

    @classmethod
    def from_config(cls, config: SearchConfig) -> "SearchOptions":
        return cls(
            top_k=config.top_k,
            log_queries=config.log_queries,
            persist_search_results=config.persist_search_results,
            cjk_strategy=config.cjk_strategy,
        )


@dataclass(frozen=True)
class SearchResult:
    rank: int
    doc_id: str
    revision_id: str
    chunk_id: str
    title: str | None
    source_path: str | None
    snippet: str
    score: float
    match_source: str


@dataclass(frozen=True)
class SearchResultSet:
    query_id: str | None
    query_text: str
    result_count: int
    results: tuple[SearchResult, ...]


@dataclass
class _Candidate:
    chunk_id: str
    fts_score: float | None = None
    fts_rank: int | None = None
    cjk_score: int = 0
    cjk_rank: int | None = None
    vector_score: float | None = None
    vector_rank: int | None = None
    match_source: str = ""


def search_chunks(
    connection: sqlite3.Connection,
    query: str,
    *,
    options: SearchOptions | None = None,
) -> SearchResultSet:
    opts = options or SearchOptions()
    from indbase_core.category_taxonomy import parse_search_query

    category_ref, remainder = parse_search_query(query)
    normalized_query = remainder.strip()
    category_id = opts.category_id
    if category_ref and not category_id:
        from indbase_core.category_taxonomy import resolve_category_filter

        category_id = resolve_category_filter(connection, category_ref)
        if category_id is None:
            raise ValueError(f"Unknown category filter: {category_ref}")
    if not normalized_query and category_id:
        normalized_query = "*"
    if not normalized_query:
        return SearchResultSet(query_id=None, query_text=query, result_count=0, results=())
    if opts.mode not in {"fts", "vector", "hybrid"}:
        raise ValueError("search mode must be fts, vector, or hybrid")

    candidates: dict[str, _Candidate] = {}
    if opts.mode in {"fts", "hybrid"}:
        for rank, (chunk_id, score) in enumerate(
            _fts_candidates(
                connection,
                normalized_query,
                limit=max(opts.top_k * 5, 25),
                category_id=category_id,
            ),
            start=1,
        ):
            candidates[chunk_id] = _Candidate(
                chunk_id=chunk_id,
                fts_score=score,
                fts_rank=rank,
                match_source="fts",
            )

        cjk_query = prepare_cjk_fallback_query(normalized_query)
        if opts.cjk_strategy == "substring_fallback" and cjk_query.has_cjk:
            for rank, (chunk_id, score) in enumerate(
                _cjk_fallback_candidates(connection, normalized_query, category_id=category_id),
                start=1,
            ):
                candidate = candidates.get(chunk_id)
                if candidate is None:
                    candidates[chunk_id] = _Candidate(
                        chunk_id=chunk_id,
                        cjk_score=score,
                        cjk_rank=rank,
                        match_source="cjk",
                    )
                else:
                    candidate.cjk_score = score
                    candidate.cjk_rank = rank
                    candidate.match_source = "fts+cjk"

    if opts.mode in {"vector", "hybrid"}:
        for rank, (chunk_id, score) in enumerate(
            _vector_candidates(connection, normalized_query, limit=max(opts.top_k * 5, 25)),
            start=1,
        ):
            candidate = candidates.get(chunk_id)
            if candidate is None:
                candidates[chunk_id] = _Candidate(
                    chunk_id=chunk_id,
                    vector_score=score,
                    vector_rank=rank,
                    match_source="vector",
                )
            else:
                candidate.vector_score = score
                candidate.vector_rank = rank
                candidate.match_source = _combined_match_source(candidate)

    ordered_candidates = sorted(candidates.values(), key=_candidate_sort_key)[: opts.top_k]
    rows_by_chunk_id = _load_result_rows(connection, [candidate.chunk_id for candidate in ordered_candidates])
    results: list[SearchResult] = []
    for candidate in ordered_candidates:
        row = rows_by_chunk_id.get(candidate.chunk_id)
        if row is None:
            continue
        results.append(
            SearchResult(
                rank=len(results) + 1,
                doc_id=str(row["doc_id"]),
                revision_id=str(row["revision_id"]),
                chunk_id=str(row["chunk_id"]),
                title=row["title"],
                source_path=row["source_path"],
                snippet=build_snippet(str(row["text"]), normalized_query),
                score=_display_score(candidate),
                match_source=candidate.match_source,
            )
        )

    query_id = _record_search_query(connection, normalized_query, opts, len(results)) if opts.log_queries else None
    if query_id is not None and opts.persist_search_results:
        _persist_search_results(connection, query_id, results)
    connection.commit()
    return SearchResultSet(
        query_id=query_id,
        query_text=normalized_query,
        result_count=len(results),
        results=tuple(results),
    )


def build_snippet(text: str, query: str, *, max_chars: int = 220) -> str:
    clean_text = " ".join(text.split())
    if len(clean_text) <= max_chars:
        return clean_text

    terms = _snippet_terms(query)
    folded = clean_text.casefold()
    match_index = -1
    for term in terms:
        match_index = folded.find(term.casefold())
        if match_index >= 0:
            break

    if match_index < 0:
        start = 0
    else:
        start = max(0, match_index - max_chars // 3)
    end = min(len(clean_text), start + max_chars)
    if end - start < max_chars:
        start = max(0, end - max_chars)
    snippet = clean_text[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(clean_text):
        snippet += "..."
    return snippet


def _fts_candidates(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int,
    category_id: str | None = None,
) -> list[tuple[str, float]]:
    match_query = _fts_match_query(query)
    if query != "*" and not match_query:
        return []
    if query == "*":
        match_query = None
    category_clause = ""
    params: list[object] = []
    if category_id:
        category_clause = " AND d.category_id = ?"
        params.append(category_id)
    if match_query is None:
        sql = f"""
            SELECT c.chunk_id, 0.0 AS score
            FROM chunks c
            JOIN documents d ON d.doc_id = c.doc_id
            WHERE d.status = 'active'
              AND d.deleted_at IS NULL
              AND d.current_revision_id = c.revision_id
              AND c.is_current = 1
              AND c.deleted_at IS NULL
              {category_clause}
            ORDER BY d.created_at, c.sequence
            LIMIT ?
        """
        params.append(limit)
    else:
        sql = f"""
            SELECT f.chunk_id, bm25(chunks_fts) AS score
            FROM chunks_fts f
            JOIN chunks c ON c.chunk_id = f.chunk_id
            JOIN documents d ON d.doc_id = c.doc_id
            WHERE chunks_fts MATCH ?
              AND d.status = 'active'
              AND d.deleted_at IS NULL
              AND d.current_revision_id = c.revision_id
              AND c.is_current = 1
              AND c.deleted_at IS NULL
              {category_clause}
            ORDER BY score
            LIMIT ?
        """
        params = [match_query, *params, limit]
    try:
        rows = connection.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []
    return [(str(row["chunk_id"]), float(row["score"])) for row in rows]


def _cjk_fallback_candidates(
    connection: sqlite3.Connection,
    query: str,
    *,
    category_id: str | None = None,
) -> list[tuple[str, int]]:
    category_clause = ""
    params: list[object] = []
    if category_id:
        category_clause = " AND d.category_id = ?"
        params.append(category_id)
    rows = connection.execute(
        f"""
        SELECT c.chunk_id, c.heading_path_json, c.text, d.title
        FROM chunks c
        JOIN documents d ON d.doc_id = c.doc_id
        WHERE d.status = 'active'
          AND d.deleted_at IS NULL
          AND d.current_revision_id = c.revision_id
          AND c.is_current = 1
          AND c.deleted_at IS NULL
          {category_clause}
        ORDER BY d.created_at, c.sequence
        """,
        params,
    ).fetchall()
    scored: list[tuple[str, int]] = []
    for row in rows:
        score = cjk_substring_score(
            query,
            title=row["title"],
            heading_path=_heading_path_text(row["heading_path_json"]),
            text=row["text"],
        )
        if score > 0:
            scored.append((str(row["chunk_id"]), score))
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored


def _load_result_rows(connection: sqlite3.Connection, chunk_ids: list[str]) -> dict[str, sqlite3.Row]:
    if not chunk_ids:
        return {}
    placeholders = ", ".join("?" for _ in chunk_ids)
    rows = connection.execute(
        f"""
        SELECT c.chunk_id, c.doc_id, c.revision_id, c.text, d.title,
               d.canonical_path AS source_path
        FROM chunks c
        JOIN documents d ON d.doc_id = c.doc_id
        WHERE c.chunk_id IN ({placeholders})
          AND d.status = 'active'
          AND d.deleted_at IS NULL
          AND d.current_revision_id = c.revision_id
          AND c.is_current = 1
          AND c.deleted_at IS NULL
        """,
        chunk_ids,
    ).fetchall()
    return {str(row["chunk_id"]): row for row in rows}


def _record_search_query(
    connection: sqlite3.Connection,
    query: str,
    options: SearchOptions,
    result_count: int,
) -> str:
    query_id = new_prefixed_id("query")
    connection.execute(
        """
        INSERT INTO search_queries(query_id, query_text, mode, filters_json, top_k, result_count, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            query_id,
            query,
            options.mode,
            json.dumps(
                {"scope": "sources_current", "cjk_strategy": options.cjk_strategy},
                sort_keys=True,
            ),
            options.top_k,
            result_count,
            utc_now_iso(),
        ),
    )
    return query_id


def _persist_search_results(
    connection: sqlite3.Connection,
    query_id: str,
    results: list[SearchResult],
) -> None:
    now = utc_now_iso()
    for result in results:
        connection.execute(
            """
            INSERT INTO search_results(query_id, rank, doc_id, revision_id, chunk_id, snippet, score, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                query_id,
                result.rank,
                result.doc_id,
                result.revision_id,
                result.chunk_id,
                result.snippet,
                result.score,
                now,
            ),
        )


def _fts_match_query(query: str) -> str:
    terms = list(ascii_query_terms(query))
    terms.extend(cjk_query_terms(query))
    terms.extend(build_fts_text(query).split())
    unique_terms = []
    seen: set[str] = set()
    for term in terms:
        if not term or term in seen:
            continue
        seen.add(term)
        unique_terms.append(term)
    return " OR ".join(f'"{term.replace(chr(34), chr(34) + chr(34))}"' for term in unique_terms)


def _snippet_terms(query: str) -> tuple[str, ...]:
    terms = list(cjk_query_terms(query))
    terms.extend(ascii_query_terms(query))
    terms.sort(key=len, reverse=True)
    return tuple(terms)


def _heading_path_text(heading_path_json: str | None) -> str:
    if not heading_path_json:
        return ""
    parsed = json.loads(heading_path_json)
    if not isinstance(parsed, list):
        return ""
    return " ".join(str(value) for value in parsed)


def _vector_candidates(connection: sqlite3.Connection, query: str, *, limit: int) -> list[tuple[str, float]]:
    query_vector = DeterministicEmbeddingAdapter().embed(query)
    rows = connection.execute(
        """
        SELECT e.chunk_id, e.vector_ref
        FROM embeddings e
        JOIN chunks c ON c.chunk_id = e.chunk_id
        JOIN documents d ON d.doc_id = e.doc_id
        WHERE e.status = 'indexed'
          AND e.deleted_at IS NULL
          AND e.content_hash = c.content_hash
          AND c.deleted_at IS NULL
          AND d.status = 'active'
          AND d.deleted_at IS NULL
          AND d.current_revision_id = e.revision_id
          AND d.current_revision_id = c.revision_id
          AND c.is_current = 1
        """
    ).fetchall()
    scored: list[tuple[str, float]] = []
    for row in rows:
        vector = _parse_vector_ref(row["vector_ref"])
        if vector is None:
            continue
        scored.append((str(row["chunk_id"]), _cosine_similarity(query_vector, vector)))
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored[:limit]


def _candidate_sort_key(candidate: _Candidate) -> tuple[int, float, float, str]:
    if candidate.vector_score is not None and candidate.fts_score is None and candidate.cjk_score == 0:
        return (0, -candidate.vector_score, 0.0, candidate.chunk_id)
    if candidate.vector_score is not None:
        return (0, -_hybrid_score(candidate), candidate.fts_score or 0.0, candidate.chunk_id)
    if candidate.fts_score is not None:
        return (0, candidate.fts_score, float(-candidate.cjk_score), candidate.chunk_id)
    return (1, 0.0, float(-candidate.cjk_score), candidate.chunk_id)


def _display_score(candidate: _Candidate) -> float:
    if candidate.vector_score is not None and (candidate.fts_score is not None or candidate.cjk_score):
        return _hybrid_score(candidate)
    if candidate.vector_score is not None:
        return candidate.vector_score
    if candidate.fts_score is not None:
        return candidate.fts_score
    return float(candidate.cjk_score)


def _hybrid_score(candidate: _Candidate) -> float:
    score = 0.0
    if candidate.fts_rank is not None:
        score += 1.0 / (60 + candidate.fts_rank)
    if candidate.cjk_rank is not None:
        score += 1.0 / (60 + candidate.cjk_rank)
    if candidate.vector_rank is not None:
        score += 1.0 / (60 + candidate.vector_rank)
    return score


def _combined_match_source(candidate: _Candidate) -> str:
    sources: list[str] = []
    if candidate.fts_score is not None:
        sources.append("fts")
    if candidate.cjk_score:
        sources.append("cjk")
    if candidate.vector_score is not None:
        sources.append("vector")
    return "+".join(sources)


def _parse_vector_ref(raw: str | None) -> tuple[float, ...] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    values = parsed.get("vector") if isinstance(parsed, dict) else None
    if not isinstance(values, list):
        return None
    try:
        return tuple(float(value) for value in values)
    except (TypeError, ValueError):
        return None


def _cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
