"""Deterministic retrieval evaluation and answer-readiness for v0.3.3."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from pathlib import Path
from typing import Any

from indbase_core.ids import new_prefixed_id
from indbase_core.retrieval import (
    RetrievalRunResult,
    get_retrieval_run,
    list_retrieval_items,
    retrieve_chunks,
)
from indbase_core.search import SearchOptions
from indbase_core.time import utc_now_iso

EVALUATOR_VERSION = "deterministic-v1"
READINESS_POLICY_VERSION = "answer-readiness-v1"

SUPPORTED_OPTIONS = frozenset({"mode", "top_k", "candidate_k", "per_doc_limit"})
SUPPORTED_EXPECTATIONS = frozenset(
    {
        "expected_doc_ids",
        "expected_quote_contains",
        "forbidden_doc_ids",
        "min_result_count",
        "min_unique_docs",
        "expected_readiness",
        "expected_warnings",
        "forbidden_warnings",
    }
)
VALID_READINESS_VERDICTS = frozenset({"ready", "needs_more_evidence", "not_ready"})

DEFAULT_READINESS_THRESHOLDS = {
    "min_items": 2,
    "min_unique_docs": 1,
    "max_duplicate_doc_ratio": 0.75,
    "max_profile_missing_ratio": 0.80,
    "max_warning_count": 10,
    "min_quote_chars": 20,
}


@dataclass(frozen=True)
class EvalCaseRecord:
    eval_case_id: str
    suite: str
    name: str
    query_text: str
    options: dict[str, Any]
    expectations: dict[str, Any]
    notes: str | None
    status: str
    source: str


@dataclass(frozen=True)
class ImportResult:
    imported: int
    updated: int
    rejected: int
    errors: tuple[str, ...]


@dataclass(frozen=True)
class ReadinessReportResult:
    readiness_report_id: str
    retrieval_run_id: str
    policy_version: str
    verdict: str
    score: float
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    metrics: dict[str, Any]


@dataclass(frozen=True)
class EvalResultRecord:
    eval_result_id: str
    eval_run_id: str
    eval_case_id: str
    retrieval_run_id: str | None
    readiness_report_id: str | None
    status: str
    metrics: dict[str, Any]
    failures: tuple[str, ...]
    warnings: tuple[str, ...]
    error: str | None


@dataclass(frozen=True)
class EvalRunResult:
    eval_run_id: str
    suite: str
    status: str
    case_count: int
    passed_count: int
    failed_count: int
    error_count: int
    results: tuple[EvalResultRecord, ...]


def parse_jsonl_case_line(raw_line: str, line_number: int) -> dict[str, Any]:
    stripped = raw_line.strip()
    if not stripped:
        raise ValueError(f"line {line_number}: empty row")
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"line {line_number}: invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"line {line_number}: row must be a JSON object")
    return payload


def validate_case_payload(payload: dict[str, Any], *, line_number: int | None = None) -> dict[str, Any]:
    prefix = f"line {line_number}: " if line_number is not None else ""
    for field in ("suite", "name", "query"):
        if field not in payload or not str(payload[field]).strip():
            raise ValueError(f"{prefix}missing required field {field!r}")
    if "expect" not in payload or not isinstance(payload["expect"], dict):
        raise ValueError(f"{prefix}missing required object field 'expect'")
    options = payload.get("options") or {}
    if not isinstance(options, dict):
        raise ValueError(f"{prefix}options must be an object")
    unsupported_options = set(options) - SUPPORTED_OPTIONS
    if unsupported_options:
        raise ValueError(f"{prefix}unsupported options: {sorted(unsupported_options)}")
    expectations = payload["expect"]
    unsupported_expectations = set(expectations) - SUPPORTED_EXPECTATIONS
    if unsupported_expectations:
        raise ValueError(f"{prefix}unsupported expectations: {sorted(unsupported_expectations)}")
    if "expected_readiness" in expectations:
        verdict = str(expectations["expected_readiness"])
        if verdict not in VALID_READINESS_VERDICTS:
            raise ValueError(f"{prefix}invalid expected_readiness: {verdict!r}")
    return {
        "case_id": str(payload["case_id"]).strip() if payload.get("case_id") else None,
        "suite": str(payload["suite"]).strip(),
        "name": str(payload["name"]).strip(),
        "query": str(payload["query"]).strip(),
        "options": options,
        "expectations": expectations,
        "notes": str(payload["notes"]).strip() if payload.get("notes") else None,
    }


def import_eval_cases_from_jsonl(
    connection: sqlite3.Connection,
    jsonl_path: Path,
    *,
    source: str = "fixture",
) -> ImportResult:
    if source not in {"fixture", "dogfood", "manual"}:
        raise ValueError(f"unsupported source: {source}")
    text = jsonl_path.read_text(encoding="utf-8")
    imported = 0
    updated = 0
    rejected = 0
    errors: list[str] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            payload = parse_jsonl_case_line(raw_line, line_number)
            validated = validate_case_payload(payload, line_number=line_number)
            changed = upsert_eval_case(
                connection,
                suite=validated["suite"],
                name=validated["name"],
                query_text=validated["query"],
                options=validated["options"],
                expectations=validated["expectations"],
                notes=validated["notes"],
                source=source,
                eval_case_id=validated["case_id"],
            )
            if changed == "imported":
                imported += 1
            else:
                updated += 1
        except ValueError as exc:
            rejected += 1
            errors.append(str(exc))
    connection.commit()
    return ImportResult(imported=imported, updated=updated, rejected=rejected, errors=tuple(errors))


def upsert_eval_case(
    connection: sqlite3.Connection,
    *,
    suite: str,
    name: str,
    query_text: str,
    options: dict[str, Any],
    expectations: dict[str, Any],
    notes: str | None,
    source: str,
    eval_case_id: str | None = None,
    status: str = "active",
) -> str:
    if status not in {"active", "archived"}:
        raise ValueError(f"invalid case status: {status}")
    now = utc_now_iso()
    options_json = json.dumps(options, ensure_ascii=False, sort_keys=True)
    expectations_json = json.dumps(expectations, ensure_ascii=False, sort_keys=True)
    case_id = eval_case_id
    if case_id:
        existing = connection.execute(
            "SELECT eval_case_id FROM retrieval_eval_cases WHERE eval_case_id = ?",
            (case_id,),
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO retrieval_eval_cases(
                  eval_case_id, suite, name, query_text, options_json, expectations_json,
                  notes, status, source, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    suite,
                    name,
                    query_text,
                    options_json,
                    expectations_json,
                    notes,
                    status,
                    source,
                    now,
                    now,
                ),
            )
            return "imported"
        connection.execute(
            """
            UPDATE retrieval_eval_cases
            SET suite = ?, name = ?, query_text = ?, options_json = ?, expectations_json = ?,
                notes = ?, status = ?, source = ?, updated_at = ?
            WHERE eval_case_id = ?
            """,
            (
                suite,
                name,
                query_text,
                options_json,
                expectations_json,
                notes,
                status,
                source,
                now,
                case_id,
            ),
        )
        return "updated"

    row = connection.execute(
        """
        SELECT eval_case_id
        FROM retrieval_eval_cases
        WHERE suite = ? AND name = ? AND status = 'active'
        """,
        (suite, name),
    ).fetchone()
    if row is not None:
        case_id = str(row["eval_case_id"])
        connection.execute(
            """
            UPDATE retrieval_eval_cases
            SET query_text = ?, options_json = ?, expectations_json = ?, notes = ?,
                source = ?, updated_at = ?
            WHERE eval_case_id = ?
            """,
            (query_text, options_json, expectations_json, notes, source, now, case_id),
        )
        return "updated"

    case_id = new_prefixed_id("retrcase")
    connection.execute(
        """
        INSERT INTO retrieval_eval_cases(
          eval_case_id, suite, name, query_text, options_json, expectations_json,
          notes, status, source, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            case_id,
            suite,
            name,
            query_text,
            options_json,
            expectations_json,
            notes,
            status,
            source,
            now,
            now,
        ),
    )
    return "imported"


def export_eval_cases_jsonl(connection: sqlite3.Connection, *, suite: str) -> str:
    rows = connection.execute(
        """
        SELECT eval_case_id, suite, name, query_text, options_json, expectations_json, notes
        FROM retrieval_eval_cases
        WHERE suite = ? AND status = 'active'
        ORDER BY suite, name, eval_case_id
        """,
        (suite,),
    ).fetchall()
    lines: list[str] = []
    for row in rows:
        payload: dict[str, Any] = {
            "case_id": row["eval_case_id"],
            "suite": row["suite"],
            "name": row["name"],
            "query": row["query_text"],
            "options": json.loads(str(row["options_json"])),
            "expect": json.loads(str(row["expectations_json"])),
        }
        if row["notes"]:
            payload["notes"] = row["notes"]
        lines.append(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return "\n".join(lines) + ("\n" if lines else "")


def list_eval_cases(
    connection: sqlite3.Connection,
    *,
    suite: str | None = None,
    status: str = "active",
) -> list[EvalCaseRecord]:
    query = """
        SELECT eval_case_id, suite, name, query_text, options_json, expectations_json,
               notes, status, source
        FROM retrieval_eval_cases
        WHERE status = ?
    """
    params: list[Any] = [status]
    if suite is not None:
        query += " AND suite = ?"
        params.append(suite)
    query += " ORDER BY suite, name, eval_case_id"
    return [_row_to_case(row) for row in connection.execute(query, params).fetchall()]


def assess_answer_readiness(
    connection: sqlite3.Connection,
    retrieval_run_id: str,
    *,
    policy_version: str = READINESS_POLICY_VERSION,
    thresholds: dict[str, float | int] | None = None,
) -> ReadinessReportResult:
    run = get_retrieval_run(connection, retrieval_run_id)
    if run is None:
        raise ValueError(f"Retrieval run not found: {retrieval_run_id}")
    items = list_retrieval_items(connection, retrieval_run_id)
    limits = {**DEFAULT_READINESS_THRESHOLDS, **(thresholds or {})}
    blockers: list[str] = []
    cautions: list[str] = []
    run_warnings = _load_json_list(run["warnings_json"])
    run_status = str(run["status"])

    if run_status == "failed":
        blockers.append("retrieval_run_failed")
    if not items:
        blockers.append("zero_items")

    quote_valid = 0
    quote_short = 0
    profile_missing_items = 0
    doc_ids: list[str] = []
    for row in items:
        doc_id = str(row["doc_id"])
        revision_id = str(row["revision_id"])
        chunk_id = str(row["chunk_id"])
        doc_ids.append(doc_id)
        if not doc_id or not revision_id or not chunk_id:
            blockers.append("missing_source_binding")
            continue
        quote = str(row["quote"] or "")
        if not quote.strip():
            blockers.append("empty_quote")
            continue
        chunk = connection.execute(
            "SELECT text FROM chunks WHERE chunk_id = ?",
            (chunk_id,),
        ).fetchone()
        if chunk is None:
            blockers.append("missing_chunk")
            continue
        chunk_text = str(chunk["text"])
        if quote not in chunk_text:
            blockers.append("quote_not_in_chunk")
        else:
            quote_valid += 1
        if len(quote.strip()) < int(limits["min_quote_chars"]):
            quote_short += 1
        reasons = _load_json_list(row["reasons_json"])
        if "profile_missing" in reasons:
            profile_missing_items += 1

    item_count = len(items)
    unique_docs = len(set(doc_ids))
    duplicate_doc_ratio = 0.0
    if item_count > 0:
        duplicate_doc_ratio = 1.0 - (unique_docs / item_count)
    profile_missing_ratio = profile_missing_items / item_count if item_count else 0.0

    if run_status == "partial":
        cautions.append("retrieval_run_partial")
    if item_count < int(limits["min_items"]):
        cautions.append("low_item_count")
    if unique_docs < int(limits["min_unique_docs"]):
        cautions.append("low_unique_doc_count")
    if duplicate_doc_ratio > float(limits["max_duplicate_doc_ratio"]):
        cautions.append("duplicate_doc_ratio_high")
    if profile_missing_ratio > float(limits["max_profile_missing_ratio"]):
        cautions.append("profile_missing_ratio_high")
    if len(run_warnings) > int(limits["max_warning_count"]):
        cautions.append("warning_count_high")
    if quote_short > 0 and "quote_not_in_chunk" not in blockers:
        cautions.append("short_quotes")

    blocker_codes = tuple(dict.fromkeys(blockers))
    caution_codes = tuple(dict.fromkeys(cautions))
    if blocker_codes:
        verdict = "not_ready"
    elif caution_codes:
        verdict = "needs_more_evidence"
    else:
        verdict = "ready"

    score = _readiness_score(
        item_count=item_count,
        quote_valid=quote_valid,
        unique_docs=unique_docs,
        blocker_count=len(blocker_codes),
        caution_count=len(caution_codes),
    )
    metrics = {
        "item_count": item_count,
        "unique_doc_count": unique_docs,
        "quote_valid_count": quote_valid,
        "duplicate_doc_ratio": round(duplicate_doc_ratio, 6),
        "profile_missing_ratio": round(profile_missing_ratio, 6),
        "run_warning_count": len(run_warnings),
        "run_status": run_status,
        "short_quote_count": quote_short,
    }
    report_id = new_prefixed_id("answerready")
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO answer_readiness_reports(
          readiness_report_id, retrieval_run_id, policy_version, verdict, score,
          blockers_json, warnings_json, metrics_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            report_id,
            retrieval_run_id,
            policy_version,
            verdict,
            score,
            json.dumps(list(blocker_codes), ensure_ascii=False),
            json.dumps(list(caution_codes), ensure_ascii=False),
            json.dumps(metrics, ensure_ascii=False),
            now,
        ),
    )
    connection.commit()
    return ReadinessReportResult(
        readiness_report_id=report_id,
        retrieval_run_id=retrieval_run_id,
        policy_version=policy_version,
        verdict=verdict,
        score=score,
        blockers=blocker_codes,
        warnings=caution_codes,
        metrics=metrics,
    )


def evaluate_expectations(
    *,
    retrieval: RetrievalRunResult,
    readiness: ReadinessReportResult | None,
    expectations: dict[str, Any],
) -> tuple[str, tuple[str, ...], tuple[str, ...], dict[str, Any]]:
    failures: list[str] = []
    warnings: list[str] = []
    doc_ids = [item.doc_id for item in retrieval.items]
    unique_docs = set(doc_ids)
    quotes = [item.quote for item in retrieval.items]
    run_warnings = list(retrieval.warnings)

    for expected_doc in expectations.get("expected_doc_ids", []):
        if expected_doc not in doc_ids:
            failures.append(f"missing_expected_doc:{expected_doc}")
    for forbidden_doc in expectations.get("forbidden_doc_ids", []):
        if forbidden_doc in doc_ids:
            failures.append(f"forbidden_doc_present:{forbidden_doc}")
    for fragment in expectations.get("expected_quote_contains", []):
        needle = str(fragment)
        if not any(needle.casefold() in quote.casefold() for quote in quotes):
            failures.append(f"missing_quote_fragment:{needle}")
    min_results = expectations.get("min_result_count")
    if min_results is not None and len(retrieval.items) < int(min_results):
        failures.append(f"min_result_count:{len(retrieval.items)}<{int(min_results)}")
    min_unique = expectations.get("min_unique_docs")
    if min_unique is not None and len(unique_docs) < int(min_unique):
        failures.append(f"min_unique_docs:{len(unique_docs)}<{int(min_unique)}")
    expected_readiness = expectations.get("expected_readiness")
    if expected_readiness is not None:
        if readiness is None:
            failures.append("missing_readiness_report")
        elif readiness.verdict != str(expected_readiness):
            failures.append(
                f"expected_readiness:{expected_readiness}!={readiness.verdict}"
            )
    for code in expectations.get("expected_warnings", []):
        if code not in run_warnings:
            failures.append(f"missing_warning:{code}")
    for code in expectations.get("forbidden_warnings", []):
        if code in run_warnings:
            failures.append(f"forbidden_warning:{code}")

    metrics = {
        "result_count": len(retrieval.items),
        "unique_doc_count": len(unique_docs),
        "retrieval_status": retrieval.status,
        "readiness_verdict": readiness.verdict if readiness else None,
    }
    status = "failed" if failures else "passed"
    return status, tuple(failures), tuple(warnings), metrics


def run_eval_suite(
    connection: sqlite3.Connection,
    *,
    suite: str,
    case_id: str | None = None,
    limit: int | None = None,
    fail_fast: bool = False,
    search_options: SearchOptions | None = None,
) -> EvalRunResult:
    cases = list_eval_cases(connection, suite=suite, status="active")
    if case_id is not None:
        cases = [case for case in cases if case.eval_case_id == case_id]
    if limit is not None:
        cases = cases[: max(limit, 0)]
    if not cases:
        raise ValueError(f"No active eval cases for suite: {suite}")

    eval_run_id = new_prefixed_id("retrievaleval")
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO retrieval_eval_runs(
          eval_run_id, suite, evaluator_version, policy_version,
          case_count, passed_count, failed_count, error_count,
          status, error_json, created_at, finished_at
        )
        VALUES (?, ?, ?, ?, ?, 0, 0, 0, 'partial', NULL, ?, NULL)
        """,
        (
            eval_run_id,
            suite,
            EVALUATOR_VERSION,
            READINESS_POLICY_VERSION,
            len(cases),
            now,
        ),
    )
    connection.commit()

    results: list[EvalResultRecord] = []
    passed = failed = errors = 0
    run_errors: list[str] = []

    for case in cases:
        try:
            retrieval = _run_case_retrieval(connection, case, search_options=search_options)
            readiness = assess_answer_readiness(connection, retrieval.retrieval_run_id)
            status, failures, case_warnings, metrics = evaluate_expectations(
                retrieval=retrieval,
                readiness=readiness,
                expectations=case.expectations,
            )
            result_id = new_prefixed_id("retrievalevalresult")
            created = utc_now_iso()
            connection.execute(
                """
                INSERT INTO retrieval_eval_results(
                  eval_result_id, eval_run_id, eval_case_id, retrieval_run_id,
                  readiness_report_id, status, metrics_json, failures_json, warnings_json,
                  error_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    result_id,
                    eval_run_id,
                    case.eval_case_id,
                    retrieval.retrieval_run_id,
                    readiness.readiness_report_id,
                    status,
                    json.dumps(metrics, ensure_ascii=False),
                    json.dumps(list(failures), ensure_ascii=False),
                    json.dumps(list(case_warnings), ensure_ascii=False),
                    created,
                ),
            )
            record = EvalResultRecord(
                eval_result_id=result_id,
                eval_run_id=eval_run_id,
                eval_case_id=case.eval_case_id,
                retrieval_run_id=retrieval.retrieval_run_id,
                readiness_report_id=readiness.readiness_report_id,
                status=status,
                metrics=metrics,
                failures=failures,
                warnings=case_warnings,
                error=None,
            )
            if status == "passed":
                passed += 1
            else:
                failed += 1
                if fail_fast:
                    run_errors.append(f"{case.name}: {', '.join(failures)}")
                    break
        except Exception as exc:
            errors += 1
            result_id = new_prefixed_id("retrievalevalresult")
            created = utc_now_iso()
            connection.execute(
                """
                INSERT INTO retrieval_eval_results(
                  eval_result_id, eval_run_id, eval_case_id, retrieval_run_id,
                  readiness_report_id, status, metrics_json, failures_json, warnings_json,
                  error_json, created_at
                )
                VALUES (?, ?, ?, NULL, NULL, 'error', '{}', '[]', '[]', ?, ?)
                """,
                (
                    result_id,
                    eval_run_id,
                    case.eval_case_id,
                    json.dumps({"message": str(exc)}, ensure_ascii=False),
                    created,
                ),
            )
            record = EvalResultRecord(
                eval_result_id=result_id,
                eval_run_id=eval_run_id,
                eval_case_id=case.eval_case_id,
                retrieval_run_id=None,
                readiness_report_id=None,
                status="error",
                metrics={},
                failures=(),
                warnings=(),
                error=str(exc),
            )
            if fail_fast:
                run_errors.append(f"{case.name}: {exc}")
                results.append(record)
                break
        results.append(record)
        connection.commit()

    if errors > 0 and passed + failed > 0:
        run_status = "partial"
    elif failed > 0 or errors > 0:
        run_status = "failed"
    else:
        run_status = "succeeded"
    finished = utc_now_iso()
    error_json = json.dumps({"messages": run_errors}, ensure_ascii=False) if run_errors else None
    connection.execute(
        """
        UPDATE retrieval_eval_runs
        SET passed_count = ?, failed_count = ?, error_count = ?, status = ?,
            error_json = ?, finished_at = ?
        WHERE eval_run_id = ?
        """,
        (passed, failed, errors, run_status, error_json, finished, eval_run_id),
    )
    connection.commit()
    return EvalRunResult(
        eval_run_id=eval_run_id,
        suite=suite,
        status=run_status,
        case_count=len(cases),
        passed_count=passed,
        failed_count=failed,
        error_count=errors,
        results=tuple(results),
    )


def list_eval_runs(connection: sqlite3.Connection, *, limit: int = 20) -> list[sqlite3.Row]:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    return list(
        connection.execute(
            """
            SELECT eval_run_id, suite, evaluator_version, policy_version,
                   case_count, passed_count, failed_count, error_count,
                   status, created_at, finished_at
            FROM retrieval_eval_runs
            ORDER BY created_at DESC, eval_run_id
            LIMIT ?
            """,
            (limit,),
        )
    )


def get_eval_run(connection: sqlite3.Connection, eval_run_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT eval_run_id, suite, evaluator_version, policy_version,
               case_count, passed_count, failed_count, error_count,
               status, error_json, created_at, finished_at
        FROM retrieval_eval_runs
        WHERE eval_run_id = ?
        """,
        (eval_run_id,),
    ).fetchone()


def list_eval_results(connection: sqlite3.Connection, eval_run_id: str) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT eval_result_id, eval_run_id, eval_case_id, retrieval_run_id,
                   readiness_report_id, status, metrics_json, failures_json,
                   warnings_json, error_json, created_at
            FROM retrieval_eval_results
            WHERE eval_run_id = ?
            ORDER BY created_at, eval_result_id
            """,
            (eval_run_id,),
        )
    )


def get_readiness_report(connection: sqlite3.Connection, readiness_report_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT readiness_report_id, retrieval_run_id, policy_version, verdict, score,
               blockers_json, warnings_json, metrics_json, created_at
        FROM answer_readiness_reports
        WHERE readiness_report_id = ?
        """,
        (readiness_report_id,),
    ).fetchone()


def _run_case_retrieval(
    connection: sqlite3.Connection,
    case: EvalCaseRecord,
    *,
    search_options: SearchOptions | None = None,
) -> RetrievalRunResult:
    options = case.options
    return retrieve_chunks(
        connection,
        case.query_text,
        top_k=int(options.get("top_k", 20)),
        candidate_k=options.get("candidate_k"),
        per_doc_limit=int(options.get("per_doc_limit", 3)),
        mode=str(options.get("mode", "hybrid")),
        search_options=search_options,
    )


def _row_to_case(row: sqlite3.Row) -> EvalCaseRecord:
    return EvalCaseRecord(
        eval_case_id=str(row["eval_case_id"]),
        suite=str(row["suite"]),
        name=str(row["name"]),
        query_text=str(row["query_text"]),
        options=json.loads(str(row["options_json"])),
        expectations=json.loads(str(row["expectations_json"])),
        notes=row["notes"],
        status=str(row["status"]),
        source=str(row["source"]),
    )


def _load_json_list(raw: object) -> list[str]:
    try:
        payload = json.loads(str(raw or "[]"))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload]


def _readiness_score(
    *,
    item_count: int,
    quote_valid: int,
    unique_docs: int,
    blocker_count: int,
    caution_count: int,
) -> float:
    if blocker_count:
        return 0.0
    base = 0.2
    if item_count:
        base += min(0.4, item_count * 0.1)
    if quote_valid:
        base += min(0.2, quote_valid * 0.05)
    if unique_docs:
        base += min(0.1, unique_docs * 0.05)
    if caution_count:
        base -= min(0.3, caution_count * 0.08)
    return round(max(0.0, min(1.0, base)), 4)
