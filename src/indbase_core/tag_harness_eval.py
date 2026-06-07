"""Deterministic tag harness evaluation for v0.3.2.1."""

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
from indbase_core.doctor import run_doctor
from indbase_core.indexer import rebuild_fts_index
from indbase_core.paths import vault_paths
from indbase_core.search import SearchOptions, search_chunks
from indbase_core.tag_blocklist import add_blocklist_entry
from indbase_core.tag_search import document_matches_tag_filter, resolve_tag_filter
from indbase_core.tag_tagger import run_deterministic_tagger
from indbase_core.tags import add_document_tag, add_tag, list_document_tags
from indbase_core.taxonomy_mutations import add_tag_alias, deprecate_tag
from indbase_core.vault import init_vault

HARNESS_PHASE = "v0.3.2.1"
HARNESS_TRIGGER = "fixture_gate"

TAG_GOVERNANCE_DOCTOR_CODES = frozenset(
    {
        "tag_alias_points_missing_tag",
        "tag_alias_duplicate_normalized",
        "tag_merged_target_missing",
        "tag_merge_cycle",
        "document_tag_points_missing_tag",
        "document_tag_points_missing_doc",
        "tag_candidate_missing_run",
        "tag_candidate_invalid_json",
        "tag_candidate_without_resolution",
        "auto_attached_tag_without_evidence",
        "auto_attached_deprecated_or_archived",
        "tag_filter_metadata_stale",
        "tag_governance_event_missing",
        "tag_blocklist_invalid_pattern",
    }
)

ALLOWED_SOURCES = frozenset({"synthetic", "feedback_derived_sanitized"})

REQUIRED_CASE_FIELDS = frozenset(
    {
        "case_id",
        "source",
        "title",
        "content",
        "seed_tags",
        "manual_tags",
        "raw_candidates",
        "expected_auto_attached",
        "expected_candidates",
        "expected_blocked",
        "expected_search_hits",
        "expected_warnings",
    }
)

OPTIONAL_CASE_FIELDS = frozenset(
    {
        "category_id",
        "seed_aliases",
        "seed_lifecycle",
        "seed_blocklist",
        "seed_scopes",
        "seed_feedback",
        "expected_failures",
        "expected_reason_codes",
        "expect_trusted_filter_unpolluted",
        "notes",
    }
)

KNOWN_CASE_FIELDS = REQUIRED_CASE_FIELDS | OPTIONAL_CASE_FIELDS

REASON_CODES = frozenset(
    {
        "wrong_auto_attach",
        "manual_tag_mutation",
        "trusted_filter_pollution",
        "budget_violation",
        "lifecycle_ineligible_auto_attach",
        "unresolved_candidate_persisted",
        "policy_mutation_by_harness",
        "expected_candidate_missing",
        "unexpected_candidate_created",
        "expected_block_missing",
        "expected_search_hit_missing",
        "unexpected_search_hit",
        "fixture_schema_invalid",
        "unsanitized_fixture",
        "doctor_hard_finding",
    }
)


@dataclass
class TagHarnessFailure:
    case_id: str
    layer: str
    severity: str
    reason_code: str
    raw_candidate: str | None = None
    expected: dict[str, Any] = field(default_factory=dict)
    actual: dict[str, Any] = field(default_factory=dict)
    governance_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TagHarnessHardGates:
    wrong_auto_attached_tags: int = 0
    manual_tag_mutations: int = 0
    trusted_filter_pollution: int = 0
    deprecated_merged_archived_auto_attach: int = 0
    unresolved_candidate_persisted: int = 0
    budget_violations: int = 0
    policy_mutations_by_harness: int = 0


@dataclass
class TagHarnessReportMetrics:
    candidate_precision: float | None = None
    candidate_recall: float | None = None
    candidate_count_by_doc: dict[str, int] = field(default_factory=dict)
    new_tag_proposals_by_run: int = 0
    blocked_sprawl_candidates: int = 0
    near_budget_docs: list[str] = field(default_factory=list)
    top_sprawl_reasons: list[str] = field(default_factory=list)


@dataclass
class TagHarnessSummary:
    phase: str = HARNESS_PHASE
    status: str = "failed"
    case_count: int = 0
    passed_case_count: int = 0
    failed_case_count: int = 0
    hard_gates: TagHarnessHardGates = field(default_factory=TagHarnessHardGates)
    report_metrics: TagHarnessReportMetrics = field(default_factory=TagHarnessReportMetrics)
    failures: list[TagHarnessFailure] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "status": self.status,
            "case_count": self.case_count,
            "passed_case_count": self.passed_case_count,
            "failed_case_count": self.failed_case_count,
            "hard_gates": asdict(self.hard_gates),
            "report_metrics": asdict(self.report_metrics),
            "failures": [failure.to_dict() for failure in self.failures],
        }


def default_cases_path() -> Path:
    root = Path(__file__).resolve().parents[2]
    return root / "tests" / "fixtures" / "v0321_tag_harness" / "cases.jsonl"


def validate_tag_harness_case(case: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(case, dict):
        return ["case must be an object"]

    unknown = set(case) - KNOWN_CASE_FIELDS
    if unknown:
        errors.append(f"unsupported keys: {', '.join(sorted(unknown))}")

    missing = REQUIRED_CASE_FIELDS - set(case)
    if missing:
        errors.append(f"missing required fields: {', '.join(sorted(missing))}")

    source = str(case.get("source", ""))
    if source and source not in ALLOWED_SOURCES:
        errors.append(f"invalid source {source!r}")

    for list_field in (
        "seed_tags",
        "manual_tags",
        "raw_candidates",
        "expected_auto_attached",
        "expected_candidates",
        "expected_blocked",
        "expected_warnings",
    ):
        value = case.get(list_field)
        if value is not None and not isinstance(value, list):
            errors.append(f"{list_field} must be a list")

    hits = case.get("expected_search_hits")
    if hits is not None and not isinstance(hits, list):
        errors.append("expected_search_hits must be a list")

    return errors


def load_tag_harness_cases(path: Path | None = None) -> list[dict[str, Any]]:
    cases_path = path or default_cases_path()
    cases: list[dict[str, Any]] = []
    for line in cases_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def _policy_fingerprint(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        """
        SELECT tag_id, name, status, merged_into_tag_id, scope, scope_category_id
        FROM tags
        WHERE deleted_at IS NULL
        ORDER BY tag_id
        """
    ).fetchall()
    blocklist = connection.execute(
        """
        SELECT blocked_id, pattern, match_type
        FROM tag_blocklist
        WHERE deleted_at IS NULL
        ORDER BY blocked_id
        """
    ).fetchall()
    aliases = connection.execute(
        """
        SELECT alias_id, tag_id, alias, normalized_alias
        FROM tag_aliases
        WHERE deleted_at IS NULL
        ORDER BY alias_id
        """
    ).fetchall()
    payload = {
        "tags": [dict(row) for row in rows],
        "blocklist": [dict(row) for row in blocklist],
        "aliases": [dict(row) for row in aliases],
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _insert_seed_document(
    connection: sqlite3.Connection,
    vault: Path,
    *,
    doc_suffix: str,
    title: str,
    content: str,
    category_id: str | None,
) -> tuple[str, str]:
    paths = vault_paths(vault)
    doc_id = f"doc_20250601_{doc_suffix}"
    revision_id = f"rev_{doc_id}_0001"
    markdown_path = paths.source_markdown_path(doc_id, "harness", 1)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    rel = paths.relative_to_vault(markdown_path)
    body = f"# {title}\n\n{content}\n"
    markdown_path.write_text(body, encoding="utf-8")
    connection.execute(
        """
        INSERT INTO documents (
          doc_id, title, filename_slug, status, ingest_status, current_revision_id,
          category_id, canonical_path, created_at, updated_at
        ) VALUES (?, ?, 'harness', 'active', 'revisioned', ?, ?, ?, '2025-06-01T00:00:00Z', '2025-06-01T00:00:00Z')
        """,
        (doc_id, title, revision_id, category_id, rel),
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


def _seed_case_tags(connection: sqlite3.Connection, case: dict[str, Any]) -> dict[str, str]:
    tag_ids: dict[str, str] = {}
    for name in case.get("seed_tags") or []:
        tag_ids[str(name)] = add_tag(connection, str(name), tag_type="topic")

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
        elif status == "merged":
            target_name = str(lifecycle["merged_into"])
            target_id = tag_ids.setdefault(target_name, add_tag(connection, target_name, tag_type="topic"))
            connection.execute(
                """
                UPDATE tags
                SET status = 'deprecated', merged_into_tag_id = ?, updated_at = '2025-06-01T00:00:00Z'
                WHERE tag_id = ?
                """,
                (target_id, tag_id),
            )
            connection.commit()
        elif status == "archived":
            connection.execute(
                "UPDATE tags SET status = 'archived', updated_at = '2025-06-01T00:00:00Z' WHERE tag_id = ?",
                (tag_id,),
            )
            connection.commit()

    for scope_seed in case.get("seed_scopes") or []:
        tag_name = str(scope_seed["tag"])
        tag_id = tag_ids.setdefault(tag_name, add_tag(connection, tag_name, tag_type="topic"))
        connection.execute(
            """
            UPDATE tags
            SET scope = ?, scope_category_id = ?, updated_at = '2025-06-01T00:00:00Z'
            WHERE tag_id = ?
            """,
            (str(scope_seed["scope"]), scope_seed.get("category_id"), tag_id),
        )
        connection.commit()

    for block_seed in case.get("seed_blocklist") or []:
        add_blocklist_entry(
            connection,
            str(block_seed["pattern"]),
            match_type=str(block_seed.get("match_type", "exact")),
            reason=str(block_seed.get("reason") or "harness"),
        )

    for manual_name in case.get("manual_tags") or []:
        manual = str(manual_name)
        tag_ids.setdefault(manual, add_tag(connection, manual, tag_type="topic"))

    return tag_ids


def _document_tag_names(connection: sqlite3.Connection, doc_id: str) -> set[str]:
    return {str(row["name"]) for row in list_document_tags(connection, doc_id)}


def _manual_tag_names(connection: sqlite3.Connection, doc_id: str) -> set[str]:
    rows = connection.execute(
        """
        SELECT t.name
        FROM document_tags dt
        JOIN tags t ON t.tag_id = dt.tag_id
        WHERE dt.doc_id = ?
          AND dt.deleted_at IS NULL
          AND dt.status = 'active'
          AND dt.source = 'manual'
        """,
        (doc_id,),
    ).fetchall()
    return {str(row["name"]) for row in rows}


def _pending_candidate_names(connection: sqlite3.Connection, doc_id: str) -> set[str]:
    rows = connection.execute(
        """
        SELECT name
        FROM tag_candidates
        WHERE doc_id = ?
          AND status = 'pending'
        """,
        (doc_id,),
    ).fetchall()
    return {str(row["name"]) for row in rows}


def _blocked_candidate_names(connection: sqlite3.Connection, doc_id: str) -> set[str]:
    names: set[str] = set()
    rows = connection.execute(
        """
        SELECT raw_name, name, resolution_status, admission_status
        FROM tag_candidates
        WHERE doc_id = ?
        """,
        (doc_id,),
    ).fetchall()
    for row in rows:
        if row["resolution_status"] == "blocked" or row["admission_status"] == "rejected":
            if row["raw_name"]:
                names.add(str(row["raw_name"]))
            if row["name"]:
                names.add(str(row["name"]))

    result_row = connection.execute(
        """
        SELECT blocked_candidates_json
        FROM tagger_results
        WHERE doc_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (doc_id,),
    ).fetchone()
    if result_row is not None:
        blocked_items = json.loads(str(result_row["blocked_candidates_json"] or "[]"))
        for item in blocked_items:
            if not isinstance(item, dict):
                continue
            for key in ("raw_name", "name", "raw_candidate"):
                value = item.get(key)
                if value:
                    names.add(str(value))
    return names


def _parse_search_hit(entry: Any) -> tuple[str, str]:
    if isinstance(entry, str):
        return entry, ""
    if isinstance(entry, dict):
        return str(entry["tag"]), str(entry.get("query") or "")
    raise ValueError(f"invalid expected_search_hits entry: {entry!r}")


def _record_failure(
    summary: TagHarnessSummary,
    *,
    case_id: str,
    layer: str,
    reason_code: str,
    expected: dict[str, Any] | None = None,
    actual: dict[str, Any] | None = None,
    raw_candidate: str | None = None,
    governance_reasons: list[str] | None = None,
) -> None:
    if reason_code not in REASON_CODES:
        raise ValueError(f"unknown reason code: {reason_code}")
    summary.failures.append(
        TagHarnessFailure(
            case_id=case_id,
            layer=layer,
            severity="error",
            reason_code=reason_code,
            raw_candidate=raw_candidate,
            expected=expected or {},
            actual=actual or {},
            governance_reasons=governance_reasons or [],
        )
    )
    gates = summary.hard_gates
    if reason_code == "wrong_auto_attach":
        gates.wrong_auto_attached_tags += 1
    elif reason_code == "manual_tag_mutation":
        gates.manual_tag_mutations += 1
    elif reason_code == "trusted_filter_pollution":
        gates.trusted_filter_pollution += 1
    elif reason_code == "lifecycle_ineligible_auto_attach":
        gates.deprecated_merged_archived_auto_attach += 1
    elif reason_code == "unresolved_candidate_persisted":
        gates.unresolved_candidate_persisted += 1
    elif reason_code == "budget_violation":
        gates.budget_violations += 1
    elif reason_code == "policy_mutation_by_harness":
        gates.policy_mutations_by_harness += 1


def _compute_report_metrics(
    cases: list[dict[str, Any]],
    summary: TagHarnessSummary,
    *,
    new_tag_proposals: int,
    blocked_count: int,
) -> None:
    expected_total = sum(len(case.get("expected_candidates") or []) for case in cases)
    matched = 0
    for case in cases:
        case_id = str(case["case_id"])
        pending = summary.report_metrics.candidate_count_by_doc.get(case_id, 0)
        matched += min(pending, len(case.get("expected_candidates") or []))
    if expected_total:
        summary.report_metrics.candidate_recall = round(matched / expected_total, 4)
    if matched:
        summary.report_metrics.candidate_precision = 1.0
    summary.report_metrics.new_tag_proposals_by_run = new_tag_proposals
    summary.report_metrics.blocked_sprawl_candidates = blocked_count


def run_tag_harness_eval(
    *,
    cases_path: Path | None = None,
    artifact_dir: Path | None = None,
    vault_root: Path | None = None,
) -> TagHarnessSummary:
    cases = load_tag_harness_cases(cases_path)
    summary = TagHarnessSummary(case_count=len(cases))

    tmp_root: Path | None = None
    if vault_root is None:
        tmp_root = Path(tempfile.mkdtemp())
        vault_root = tmp_root / "vault"

    vault = vault_root
    init_vault(vault)
    db_path = vault / ".indbase" / "db.sqlite"

    try:
        with connect(db_path) as connection:
            case_docs: list[tuple[dict[str, Any], str]] = []
            manual_before: dict[str, set[str]] = {}

            for index, case in enumerate(cases):
                case_id = str(case.get("case_id", f"case_{index}"))
                errors = validate_tag_harness_case(case)
                if errors:
                    _record_failure(
                        summary,
                        case_id=case_id,
                        layer="case",
                        reason_code="fixture_schema_invalid",
                        actual={"errors": errors},
                    )
                    summary.failed_case_count += 1
                    continue

                if str(case["source"]) not in ALLOWED_SOURCES:
                    _record_failure(
                        summary,
                        case_id=case_id,
                        layer="case",
                        reason_code="unsanitized_fixture",
                        actual={"source": case["source"]},
                    )
                    summary.failed_case_count += 1
                    continue

                _seed_case_tags(connection, case)
                doc_id, revision_id = _insert_seed_document(
                    connection,
                    vault,
                    doc_suffix=f"{len(case_docs):06d}",
                    title=str(case["title"]),
                    content=str(case["content"]),
                    category_id=str(case["category_id"]) if case.get("category_id") else None,
                )
                for manual_name in case.get("manual_tags") or []:
                    add_document_tag(
                        connection,
                        doc_id,
                        str(manual_name),
                        source="manual",
                        revision_id=revision_id,
                    )
                manual_before[doc_id] = _manual_tag_names(connection, doc_id)
                case_docs.append((case, doc_id))

            rebuild_fts_index(connection, vault)
            policy_before = _policy_fingerprint(connection)
            tagger_runs = [
                run_deterministic_tagger(connection, trigger=HARNESS_TRIGGER, doc_id=doc_id)
                for _, doc_id in case_docs
            ]
            new_tag_proposals = sum(run.new_tag_proposal_count for run in tagger_runs)
            blocked_count = sum(run.blocked_candidate_count for run in tagger_runs)

            unresolved = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM tag_candidates
                    WHERE candidate_type IS NOT NULL
                      AND (resolution_status IS NULL OR TRIM(resolution_status) = '')
                    """
                ).fetchone()["count"]
                or 0
            )
            if unresolved > 0:
                _record_failure(
                    summary,
                    case_id="__run__",
                    layer="run",
                    reason_code="unresolved_candidate_persisted",
                    actual={"count": unresolved},
                )

            for case, doc_id in case_docs:
                case_id = str(case["case_id"])
                if any(failure.case_id == case_id for failure in summary.failures):
                    continue

                attached = _document_tag_names(connection, doc_id)
                expected_attached = {str(name) for name in case.get("expected_auto_attached") or []}
                forbidden_lifecycle = {
                    str(lifecycle["tag"])
                    for lifecycle in case.get("seed_lifecycle") or []
                    if str(lifecycle["status"]) in {"deprecated", "merged", "archived"}
                }

                for name in forbidden_lifecycle:
                    if name in attached:
                        _record_failure(
                            summary,
                            case_id=case_id,
                            layer="document",
                            reason_code="lifecycle_ineligible_auto_attach",
                            raw_candidate=name,
                            actual={"attached": sorted(attached)},
                        )

                if not expected_attached.issubset(attached):
                    _record_failure(
                        summary,
                        case_id=case_id,
                        layer="document",
                        reason_code="wrong_auto_attach",
                        expected={"expected_auto_attached": sorted(expected_attached)},
                        actual={"attached": sorted(attached)},
                    )

                if not expected_attached:
                    auto_only = connection.execute(
                        """
                        SELECT t.name
                        FROM document_tags dt
                        JOIN tags t ON t.tag_id = dt.tag_id
                        WHERE dt.doc_id = ?
                          AND dt.deleted_at IS NULL
                          AND dt.status = 'active'
                          AND dt.source = 'auto'
                        """,
                        (doc_id,),
                    ).fetchall()
                    if auto_only:
                        _record_failure(
                            summary,
                            case_id=case_id,
                            layer="document",
                            reason_code="wrong_auto_attach",
                            expected={"expected_auto_attached": []},
                            actual={"unexpected_auto": sorted(str(row["name"]) for row in auto_only)},
                        )

                manual_after = _manual_tag_names(connection, doc_id)
                if manual_before.get(doc_id, set()) != manual_after:
                    _record_failure(
                        summary,
                        case_id=case_id,
                        layer="document",
                        reason_code="manual_tag_mutation",
                        expected={"manual_tags": sorted(manual_before.get(doc_id, set()))},
                        actual={"manual_tags": sorted(manual_after)},
                    )

                expected_candidates = {str(name) for name in case.get("expected_candidates") or []}
                pending = _pending_candidate_names(connection, doc_id)
                if not expected_candidates.issubset(pending):
                    _record_failure(
                        summary,
                        case_id=case_id,
                        layer="candidate",
                        reason_code="expected_candidate_missing",
                        expected={"expected_candidates": sorted(expected_candidates)},
                        actual={"pending": sorted(pending)},
                    )

                expected_blocked = {str(name) for name in case.get("expected_blocked") or []}
                blocked = _blocked_candidate_names(connection, doc_id)
                if expected_blocked and not expected_blocked.issubset(blocked):
                    _record_failure(
                        summary,
                        case_id=case_id,
                        layer="candidate",
                        reason_code="expected_block_missing",
                        expected={"expected_blocked": sorted(expected_blocked)},
                        actual={"blocked": sorted(blocked)},
                    )

                for hit in case.get("expected_search_hits") or []:
                    tag_ref, query = _parse_search_hit(hit)
                    resolution = resolve_tag_filter(connection, tag_ref)
                    if not document_matches_tag_filter(connection, doc_id, resolution.filter_tag_ids):
                        _record_failure(
                            summary,
                            case_id=case_id,
                            layer="document",
                            reason_code="expected_search_hit_missing",
                            expected={"tag": tag_ref},
                            actual={"attached": sorted(attached)},
                        )
                        continue
                    search_query = query or str(case["title"])
                    result = search_chunks(
                        connection,
                        f"tag:{tag_ref} {search_query}".strip(),
                        options=SearchOptions(
                            log_queries=False,
                            tag_filter_ids=resolution.filter_tag_ids,
                        ),
                    )
                    if doc_id not in {item.doc_id for item in result.results}:
                        _record_failure(
                            summary,
                            case_id=case_id,
                            layer="document",
                            reason_code="expected_search_hit_missing",
                            expected={"tag": tag_ref, "query": search_query},
                            actual={"result_count": result.result_count},
                        )

                if case.get("expect_trusted_filter_unpolluted"):
                    trusted = connection.execute(
                        """
                        SELECT COUNT(*) AS count
                        FROM document_tags dt
                        WHERE dt.doc_id = ?
                          AND dt.deleted_at IS NULL
                          AND dt.status = 'active'
                          AND dt.source IN ('manual', 'accepted_candidate', 'auto', 'legacy_classification')
                        """,
                        (doc_id,),
                    ).fetchone()["count"]
                    if int(trusted or 0) == 0:
                        for tag_name in case.get("seed_tags") or []:
                            try:
                                resolution = resolve_tag_filter(connection, str(tag_name))
                            except ValueError:
                                continue
                            if document_matches_tag_filter(
                                connection, doc_id, resolution.filter_tag_ids
                            ):
                                _record_failure(
                                    summary,
                                    case_id=case_id,
                                    layer="run",
                                    reason_code="trusted_filter_pollution",
                                    expected={"trusted_tags": 0},
                                    actual={"filter_tag": str(tag_name)},
                                )
                                break

                summary.report_metrics.candidate_count_by_doc[case_id] = len(pending)
                if any(failure.case_id == case_id for failure in summary.failures):
                    summary.failed_case_count += 1
                else:
                    summary.passed_case_count += 1

            if _policy_fingerprint(connection) != policy_before:
                _record_failure(
                    summary,
                    case_id="__run__",
                    layer="run",
                    reason_code="policy_mutation_by_harness",
                )

            _compute_report_metrics(
                cases,
                summary,
                new_tag_proposals=new_tag_proposals,
                blocked_count=blocked_count,
            )

        report = run_doctor(vault)
        for finding in report.findings:
            if finding.severity == "error" and finding.code in TAG_GOVERNANCE_DOCTOR_CODES:
                _record_failure(
                    summary,
                    case_id="__run__",
                    layer="run",
                    reason_code="doctor_hard_finding",
                    actual={"code": finding.code, "message": finding.message},
                )

    finally:
        if tmp_root is not None:
            shutil.rmtree(tmp_root, ignore_errors=True)

    gates = summary.hard_gates
    hard_pass = (
        gates.wrong_auto_attached_tags == 0
        and gates.manual_tag_mutations == 0
        and gates.trusted_filter_pollution == 0
        and gates.deprecated_merged_archived_auto_attach == 0
        and gates.unresolved_candidate_persisted == 0
        and gates.budget_violations == 0
        and gates.policy_mutations_by_harness == 0
        and summary.failed_case_count == 0
    )
    summary.status = "passed" if hard_pass else "failed"

    if artifact_dir is not None:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "tag-harness-summary.json").write_text(
            json.dumps(summary.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        with (artifact_dir / "tag-harness-failures.jsonl").open("w", encoding="utf-8") as handle:
            for failure in summary.failures:
                handle.write(json.dumps(failure.to_dict(), ensure_ascii=False) + "\n")

    return summary
