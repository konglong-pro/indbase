"""Deterministic tag/search governance evaluation for v0.3.2.2."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from indbase_core.chunker import chunk_markdown_body
from indbase_core.db import connect
from indbase_core.indexer import rebuild_fts_index, refresh_document_fts_metadata
from indbase_core.paths import vault_paths
from indbase_core.search import SearchOptions, governed_search_chunks
from indbase_core.search_explanations import governed_search_to_json
from indbase_core.search_filters import build_governed_search_filters
from indbase_core.tag_search import SearchFilterError
from indbase_core.tags import add_document_tag, add_tag
from indbase_core.time import utc_now_iso
from indbase_core.taxonomy_mutations import add_tag_alias, deprecate_tag
from indbase_core.vault import init_vault

HARNESS_PHASE = "v0.3.2.2"
ALLOWED_SOURCES = frozenset({"synthetic", "feedback_derived_sanitized"})

REQUIRED_CASE_FIELDS = frozenset(
    {
        "case_id",
        "source",
        "query",
        "title",
        "content",
        "expected_filter_errors",
        "expected_warnings",
    }
)

OPTIONAL_CASE_FIELDS = frozenset(
    {
        "category",
        "tag",
        "category_id",
        "trusted_tags",
        "trusted_tag_source",
        "fts_only_tags",
        "seed_aliases",
        "seed_lifecycle",
        "decoy",
        "document_status",
        "expect_hit",
        "expect_empty_success",
        "expect_json_fields",
        "expect_filter_only_order_stable",
        "forbidden_chunk_contains",
        "notes",
    }
)

KNOWN_CASE_FIELDS = REQUIRED_CASE_FIELDS | OPTIONAL_CASE_FIELDS


@dataclass
class TagSearchGovernanceFailure:
    case_id: str
    reason_code: str
    message: str
    expected: dict[str, Any] = field(default_factory=dict)
    actual: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TagSearchHardGates:
    source_safety_violations: int = 0
    tag_filter_pollution: int = 0
    category_tag_intersection_failures: int = 0
    invalid_filter_failures: int = 0
    empty_result_semantics_failures: int = 0
    json_contract_failures: int = 0
    ordering_instability: int = 0
    source_exact_hits: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TagSearchGovernanceSummary:
    phase: str = HARNESS_PHASE
    status: str = "passed"
    case_count: int = 0
    passed_case_count: int = 0
    failed_case_count: int = 0
    hard_gates: TagSearchHardGates = field(default_factory=TagSearchHardGates)
    warnings: list[str] = field(default_factory=list)
    failures: list[TagSearchGovernanceFailure] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "status": self.status,
            "case_count": self.case_count,
            "passed_case_count": self.passed_case_count,
            "failed_case_count": self.failed_case_count,
            "hard_gates": self.hard_gates.to_dict(),
            "warnings": self.warnings,
            "failures": [item.to_dict() for item in self.failures],
        }


def default_cases_path() -> Path:
    root = Path(__file__).resolve().parents[2]
    return root / "tests" / "fixtures" / "v0322_tag_search_governance" / "cases.jsonl"


def validate_tag_search_case(case: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    missing = sorted(REQUIRED_CASE_FIELDS - set(case))
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")
    unknown = sorted(set(case) - KNOWN_CASE_FIELDS)
    if unknown:
        errors.append(f"unsupported keys: {', '.join(unknown)}")
    if case.get("source") not in ALLOWED_SOURCES:
        errors.append(f"invalid source: {case.get('source')}")
    return errors


def load_tag_search_cases(path: Path | None = None) -> list[dict[str, Any]]:
    cases_path = path or default_cases_path()
    cases: list[dict[str, Any]] = []
    for line in cases_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def _record_failure(
    summary: TagSearchGovernanceSummary,
    *,
    case_id: str,
    reason_code: str,
    message: str,
    expected: dict[str, Any] | None = None,
    actual: dict[str, Any] | None = None,
) -> None:
    summary.failures.append(
        TagSearchGovernanceFailure(
            case_id=case_id,
            reason_code=reason_code,
            message=message,
            expected=expected or {},
            actual=actual or {},
        )
    )
    gates = summary.hard_gates
    if reason_code == "source_safety_violation":
        gates.source_safety_violations += 1
    elif reason_code == "tag_filter_pollution":
        gates.tag_filter_pollution += 1
    elif reason_code == "category_tag_intersection":
        gates.category_tag_intersection_failures += 1
    elif reason_code == "invalid_filter":
        gates.invalid_filter_failures += 1
    elif reason_code == "empty_result_semantics":
        gates.empty_result_semantics_failures += 1
    elif reason_code == "json_contract":
        gates.json_contract_failures += 1
    elif reason_code == "ordering_instability":
        gates.ordering_instability += 1
    elif reason_code == "source_exact_miss":
        gates.source_exact_hits = False


def _insert_case_document(
    connection: sqlite3.Connection,
    vault: Path,
    *,
    doc_suffix: str,
    title: str,
    content: str,
    category_id: str | None,
    status: str = "active",
) -> tuple[str, str]:
    paths = vault_paths(vault)
    doc_id = f"doc_20250601_{doc_suffix}"
    revision_id = f"rev_{doc_id}_0001"
    markdown_path = paths.source_markdown_path(doc_id, "searchgov", 1)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    rel = paths.relative_to_vault(markdown_path)
    body = f"# {title}\n\n{content}\n"
    markdown_path.write_text(body, encoding="utf-8")
    connection.execute(
        """
        INSERT INTO documents (
          doc_id, title, filename_slug, status, ingest_status, current_revision_id,
          category_id, canonical_path, created_at, updated_at
        ) VALUES (?, ?, 'searchgov', ?, 'revisioned', ?, ?, ?, '2025-06-01T00:00:00Z', '2025-06-01T00:00:00Z')
        """,
        (doc_id, title, status, revision_id, category_id, rel),
    )
    connection.execute(
        """
        INSERT INTO document_revisions (
          revision_id, doc_id, sequence, markdown_path, content_hash,
          converter_name, converter_version, promotion_status, created_at, updated_at
        ) VALUES (?, ?, 1, ?, 'hash', 'harness', 'harness', 'promoted', '2025-06-01T00:00:00Z', '2025-06-01T00:00:00Z')
        """,
        (revision_id, doc_id, rel),
    )
    connection.commit()
    for index, chunk in enumerate(chunk_markdown_body(body), start=1):
        connection.execute(
            """
            INSERT INTO chunks (
              chunk_id, doc_id, revision_id, sequence, heading_path_json, text,
              token_count, content_hash, is_current, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 10, 'hash', 1, '2025-06-01T00:00:00Z', '2025-06-01T00:00:00Z')
            """,
            (
                f"chk_{doc_id}_{index}",
                doc_id,
                revision_id,
                index,
                json.dumps(list(chunk.heading_path), ensure_ascii=False),
                chunk.text,
            ),
        )
    connection.commit()
    return doc_id, revision_id


def _insert_harness_document_tag(
    connection: sqlite3.Connection,
    doc_id: str,
    tag_id: str,
    *,
    revision_id: str,
    source: str,
) -> None:
    """Insert a trusted document-tag row for harness fixtures (including deprecated tags)."""
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO document_tags(
          doc_id, tag_id, revision_id, source, confidence,
          evidence_chunk_ids_json, suggestion_id, candidate_id, status, created_by,
          created_at, updated_at
        )
        VALUES (?, ?, ?, ?, 1.0, '[]', NULL, NULL, 'active', 'harness', ?, ?)
        """,
        (doc_id, tag_id, revision_id, source, now, now),
    )
    connection.commit()


def _seed_tags(connection: sqlite3.Connection, case: dict[str, Any]) -> dict[str, str]:
    tag_ids: dict[str, str] = {}
    for name in case.get("trusted_tags") or []:
        tag_name = str(name)
        tag_ids[tag_name] = add_tag(connection, tag_name, tag_type="topic")
    for alias_seed in case.get("seed_aliases") or []:
        tag_name = str(alias_seed["tag"])
        tag_id = tag_ids.setdefault(tag_name, add_tag(connection, tag_name, tag_type="topic"))
        add_tag_alias(connection, tag_id, str(alias_seed["alias"]))
    for lifecycle in case.get("seed_lifecycle") or []:
        tag_name = str(lifecycle["tag"])
        tag_id = tag_ids.setdefault(tag_name, add_tag(connection, tag_name, tag_type="topic"))
        status = str(lifecycle["status"])
        if status == "deprecated":
            deprecate_tag(connection, tag_id)
        elif status == "archived":
            connection.execute(
                "UPDATE tags SET status = 'archived', updated_at = '2025-06-01T00:00:00Z' WHERE tag_id = ?",
                (tag_id,),
            )
            connection.commit()
    return tag_ids


def run_tag_search_governance_eval(
    *,
    cases_path: Path | None = None,
    vault_root: Path | None = None,
) -> TagSearchGovernanceSummary:
    cases = load_tag_search_cases(cases_path)
    summary = TagSearchGovernanceSummary(case_count=len(cases))

    tmp_root: Path | None = None
    if vault_root is None:
        tmp_root = Path(tempfile.mkdtemp())
        vault_root = tmp_root / "vault"

    vault = vault_root
    init_vault(vault)
    db_path = vault / ".indbase" / "db.sqlite"

    try:
        with connect(db_path) as connection:
            case_doc_ids: dict[str, str] = {}
            decoy_doc_ids: dict[str, str] = {}

            for index, case in enumerate(cases):
                case_id = str(case.get("case_id", f"case_{index}"))
                errors = validate_tag_search_case(case)
                if errors:
                    _record_failure(
                        summary,
                        case_id=case_id,
                        reason_code="fixture_schema_invalid",
                        message="; ".join(errors),
                    )
                    summary.failed_case_count += 1
                    continue

                doc_id, revision_id = _insert_case_document(
                    connection,
                    vault,
                    doc_suffix=f"{index:06d}",
                    title=str(case["title"]),
                    content=str(case["content"]),
                    category_id=str(case["category_id"]) if case.get("category_id") else None,
                    status=str(case.get("document_status") or "active"),
                )
                case_doc_ids[case_id] = doc_id

                tag_ids = _seed_tags(connection, case)
                for tag_name in case.get("trusted_tags") or []:
                    tag_name_str = str(tag_name)
                    lifecycle = {
                        str(item["tag"]): str(item["status"])
                        for item in case.get("seed_lifecycle") or []
                    }
                    if lifecycle.get(tag_name_str) == "deprecated":
                        _insert_harness_document_tag(
                            connection,
                            doc_id,
                            tag_ids[tag_name_str],
                            revision_id=revision_id,
                            source=str(case.get("trusted_tag_source") or "manual"),
                        )
                    else:
                        add_document_tag(
                            connection,
                            doc_id,
                            tag_name_str,
                            source=str(case.get("trusted_tag_source") or "manual"),
                            revision_id=revision_id,
                        )

                if case.get("decoy"):
                    decoy = case["decoy"]
                    decoy_id, decoy_rev = _insert_case_document(
                        connection,
                        vault,
                        doc_suffix=f"d{index:05d}",
                        title=str(decoy["title"]),
                        content=str(decoy["content"]),
                        category_id=str(decoy["category_id"]) if decoy.get("category_id") else None,
                    )
                    decoy_doc_ids[case_id] = decoy_id
                    for tag_name in decoy.get("trusted_tags") or []:
                        tag_name_str = str(tag_name)
                        if tag_name_str not in tag_ids:
                            tag_ids[tag_name_str] = add_tag(connection, tag_name_str, tag_type="topic")
                        add_document_tag(
                            connection,
                            decoy_id,
                            tag_name_str,
                            source="manual",
                            revision_id=decoy_rev,
                        )

                for fts_name in case.get("fts_only_tags") or []:
                    add_tag(connection, str(fts_name), tag_type="topic")

                if case.get("fts_only_tags"):
                    refresh_document_fts_metadata(connection, doc_id)
                    fts_tags = ",".join(str(name) for name in case["fts_only_tags"])
                    connection.execute(
                        "UPDATE chunks_fts SET tags = ? WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE doc_id = ?)",
                        (fts_tags, doc_id),
                    )
                    connection.commit()

            rebuild_fts_index(connection, vault)

            for case in cases:
                case_id = str(case["case_id"])
                if any(item.case_id == case_id for item in summary.failures):
                    continue

                doc_id = case_doc_ids.get(case_id)
                decoy_id = decoy_doc_ids.get(case_id)
                query = str(case["query"])
                category = str(case["category"]) if case.get("category") else None
                tag = str(case["tag"]) if case.get("tag") else None
                expected_errors = [str(code) for code in case.get("expected_filter_errors") or []]

                if expected_errors:
                    filters = build_governed_search_filters(
                        connection,
                        query,
                        category_flag=category,
                        tag_flag=tag,
                    )
                    codes = [item["code"] for item in filters.filter_errors]
                    if not any(code in codes for code in expected_errors):
                        _record_failure(
                            summary,
                            case_id=case_id,
                            reason_code="invalid_filter",
                            message="expected filter error missing",
                            expected={"codes": expected_errors},
                            actual={"codes": codes},
                        )
                        summary.failed_case_count += 1
                    else:
                        summary.passed_case_count += 1
                    continue

                try:
                    governed = governed_search_chunks(
                        connection,
                        query,
                        category=category,
                        tag=tag,
                        options=SearchOptions(log_queries=False, top_k=10),
                    )
                except SearchFilterError as exc:
                    _record_failure(
                        summary,
                        case_id=case_id,
                        reason_code="invalid_filter",
                        message=str(exc),
                        actual={"code": exc.code},
                    )
                    summary.failed_case_count += 1
                    continue

                expected_warnings = [str(item) for item in case.get("expected_warnings") or []]
                for warning in expected_warnings:
                    if warning not in governed.filters.warnings:
                        _record_failure(
                            summary,
                            case_id=case_id,
                            reason_code="empty_result_semantics",
                            message=f"missing warning {warning}",
                            expected={"warnings": expected_warnings},
                            actual={"warnings": list(governed.filters.warnings)},
                        )

                hit_ids = {row.doc_id for row in governed.results}
                expect_hit = case.get("expect_hit")
                expect_empty = bool(case.get("expect_empty_success"))

                if expect_hit is True and doc_id and doc_id not in hit_ids:
                    _record_failure(
                        summary,
                        case_id=case_id,
                        reason_code="source_exact_miss",
                        message="expected document missing from results",
                        expected={"doc_id": doc_id},
                        actual={"hits": sorted(hit_ids)},
                    )
                if expect_empty and governed.result_count != 0:
                    _record_failure(
                        summary,
                        case_id=case_id,
                        reason_code="empty_result_semantics",
                        message="expected empty success",
                        actual={"result_count": governed.result_count},
                    )
                if decoy_id and decoy_id in hit_ids:
                    _record_failure(
                        summary,
                        case_id=case_id,
                        reason_code="category_tag_intersection",
                        message="decoy document matched combined filters",
                        actual={"decoy_id": decoy_id},
                    )

                forbidden = str(case.get("forbidden_chunk_contains") or "")
                if forbidden and any(forbidden in row.snippet for row in governed.results):
                    _record_failure(
                        summary,
                        case_id=case_id,
                        reason_code="tag_filter_pollution",
                        message="forbidden text appeared in results",
                        actual={"forbidden": forbidden},
                    )

                json_fields = case.get("expect_json_fields") or []
                if json_fields:
                    payload = governed_search_to_json(governed)
                    missing = [field for field in json_fields if field not in payload]
                    if missing:
                        _record_failure(
                            summary,
                            case_id=case_id,
                            reason_code="json_contract",
                            message="missing json fields",
                            expected={"fields": json_fields},
                            actual={"missing": missing},
                        )
                    if payload.get("results") and "explanation" not in payload["results"][0]:
                        _record_failure(
                            summary,
                            case_id=case_id,
                            reason_code="json_contract",
                            message="result explanation missing",
                        )

                if case.get("expect_filter_only_order_stable") and len(governed.results) > 1:
                    ordering = [row.doc_id for row in governed.results]
                    repeat = governed_search_chunks(
                        connection,
                        query,
                        category=category,
                        tag=tag,
                        options=SearchOptions(log_queries=False, top_k=10),
                    )
                    repeat_order = [row.doc_id for row in repeat.results]
                    if ordering != repeat_order:
                        _record_failure(
                            summary,
                            case_id=case_id,
                            reason_code="ordering_instability",
                            message="filter-only ordering changed between runs",
                            actual={"first": ordering, "second": repeat_order},
                        )

                if any(item.case_id == case_id for item in summary.failures):
                    summary.failed_case_count += 1
                else:
                    summary.passed_case_count += 1

    finally:
        if tmp_root is not None:
            shutil.rmtree(tmp_root, ignore_errors=True)

    if summary.failures or summary.failed_case_count:
        summary.status = "failed"
    return summary
