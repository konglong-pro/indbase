"""Normalized search filter model for v0.3.2.2 tag/search governance."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from indbase_core.category_taxonomy import parse_search_query, resolve_category_filter
from indbase_core.tag_search import (
    SearchFilterError,
    parse_tag_search_query,
    resolve_governed_tag_filter,
)


@dataclass(frozen=True)
class AppliedCategoryFilter:
    input: str
    category_id: str


@dataclass(frozen=True)
class AppliedTagFilter:
    input: str
    canonical_tag_id: str
    filter_tag_ids: tuple[str, ...]
    via_alias: bool
    resolved_from_merged: bool
    deprecated: bool


@dataclass(frozen=True)
class GovernedSearchFilters:
    original_query: str
    text_query: str
    category: AppliedCategoryFilter | None
    tag: AppliedTagFilter | None
    filter_errors: tuple[dict[str, str], ...]
    warnings: tuple[str, ...]

    @property
    def has_valid_filters(self) -> bool:
        return self.category is not None or self.tag is not None

    @property
    def category_id(self) -> str | None:
        return self.category.category_id if self.category else None

    @property
    def tag_filter_ids(self) -> tuple[str, ...] | None:
        if self.tag is None:
            return None
        return self.tag.filter_tag_ids


def _normalize_ref(value: str | None) -> str | None:
    if value is None:
        return None
    clean = " ".join(value.strip().split())
    return clean or None


def build_governed_search_filters(
    connection: sqlite3.Connection,
    query: str,
    *,
    category_flag: str | None = None,
    tag_flag: str | None = None,
) -> GovernedSearchFilters:
    """Parse CLI flags and query prefixes into one governed filter model."""
    original_query = query
    category_ref, remainder = parse_search_query(query)
    tag_ref, remainder = parse_tag_search_query(remainder)
    text_query = remainder.strip()

    cli_category = _normalize_ref(category_flag)
    cli_tag = _normalize_ref(tag_flag)

    filter_errors: list[dict[str, str]] = []
    warnings: list[str] = []

    if cli_category and category_ref and cli_category.casefold() != category_ref.casefold():
        filter_errors.append(
            {
                "code": "conflicting_category_filter",
                "message": f"Conflicting category filters: --category {cli_category!r} and category:{category_ref!r}",
            }
        )
    if cli_tag and tag_ref and cli_tag.casefold() != tag_ref.casefold():
        filter_errors.append(
            {
                "code": "conflicting_tag_filter",
                "message": f"Conflicting tag filters: --tag {cli_tag!r} and tag:{tag_ref!r}",
            }
        )

    final_category_ref = cli_category or category_ref
    final_tag_ref = cli_tag or tag_ref

    category: AppliedCategoryFilter | None = None
    tag: AppliedTagFilter | None = None

    if final_category_ref and not filter_errors:
        category_id = resolve_category_filter(connection, final_category_ref)
        if category_id is None:
            filter_errors.append(
                {
                    "code": "unknown_category",
                    "message": f"Unknown category filter: {final_category_ref}",
                }
            )
        else:
            category = AppliedCategoryFilter(input=final_category_ref, category_id=category_id)

    if final_tag_ref and not filter_errors:
        try:
            resolved = resolve_governed_tag_filter(connection, final_tag_ref)
        except SearchFilterError as exc:
            filter_errors.append({"code": exc.code, "message": exc.message})
        else:
            tag = AppliedTagFilter(
                input=resolved.tag_ref,
                canonical_tag_id=resolved.canonical_tag_id,
                filter_tag_ids=resolved.filter_tag_ids,
                via_alias=resolved.via_alias,
                resolved_from_merged=resolved.resolved_from_merged,
                deprecated=resolved.deprecated,
            )
            if resolved.deprecated:
                warnings.append("tag_filter_deprecated")

    return GovernedSearchFilters(
        original_query=original_query,
        text_query=text_query,
        category=category,
        tag=tag,
        filter_errors=tuple(filter_errors),
        warnings=tuple(warnings),
    )


def require_valid_filters(filters: GovernedSearchFilters) -> None:
    if filters.filter_errors:
        first = filters.filter_errors[0]
        raise SearchFilterError(first["code"], first["message"])
