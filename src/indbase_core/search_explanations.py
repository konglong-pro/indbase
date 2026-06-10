"""Search JSON contract helpers for v0.3.2.2."""

from __future__ import annotations

from typing import Any

from indbase_core.search import GovernedSearchResult, SearchResult
from indbase_core.search_filters import GovernedSearchFilters


def applied_filters_payload(filters: GovernedSearchFilters) -> dict[str, Any]:
    category_payload: dict[str, Any] | None = None
    if filters.category is not None:
        category_payload = {
            "input": filters.category.input,
            "category_id": filters.category.category_id,
        }
    tag_payload: dict[str, Any] | None = None
    if filters.tag is not None:
        tag_payload = {
            "input": filters.tag.input,
            "canonical_tag_id": filters.tag.canonical_tag_id,
            "filter_tag_ids": list(filters.tag.filter_tag_ids),
            "via_alias": filters.tag.via_alias,
            "resolved_from_merged": filters.tag.resolved_from_merged,
            "deprecated": filters.tag.deprecated,
        }
    return {"category": category_payload, "tag": tag_payload}


def result_to_json_dict(result: SearchResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "rank": result.rank,
        "doc_id": result.doc_id,
        "revision_id": result.revision_id,
        "chunk_id": result.chunk_id,
        "title": result.title,
        "source_path": result.source_path,
        "snippet": result.snippet,
        "score": result.score,
        "match_source": result.match_source,
    }
    if result.explanation is not None:
        payload["explanation"] = {
            "text_match": result.explanation.text_match,
            "filter_match": result.explanation.filter_match,
            "applied_filters": list(result.explanation.applied_filters),
            "warnings": list(result.explanation.warnings),
        }
    return payload


def governed_search_to_json(governed: GovernedSearchResult) -> dict[str, Any]:
    return {
        "query_id": governed.query_id,
        "query": governed.filters.original_query,
        "normalized_query": governed.normalized_query,
        "applied_filters": applied_filters_payload(governed.filters),
        "filter_errors": [dict(item) for item in governed.filters.filter_errors],
        "warnings": list(governed.filters.warnings),
        "result_count": governed.result_count,
        "results": [result_to_json_dict(row) for row in governed.results],
    }
