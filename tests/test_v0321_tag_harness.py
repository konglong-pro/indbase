"""v0.3.2.1 tag harness hardening tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from indbase_core.tag_harness_eval import (
    REASON_CODES,
    TagHarnessFailure,
    TagHarnessSummary,
    default_cases_path,
    load_tag_harness_cases,
    run_tag_harness_eval,
    validate_tag_harness_case,
)


def _minimal_case(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "case_id": "taghcase_test",
        "source": "synthetic",
        "title": "Test",
        "content": "sqlite storage body",
        "seed_tags": ["sqlite"],
        "manual_tags": [],
        "raw_candidates": [],
        "expected_auto_attached": ["sqlite"],
        "expected_candidates": [],
        "expected_blocked": [],
        "expected_search_hits": [],
        "expected_warnings": [],
    }
    base.update(overrides)
    return base


def test_fixture_schema_requires_explicit_fields() -> None:
    errors = validate_tag_harness_case({"case_id": "x", "source": "synthetic"})
    assert any("missing required fields" in error for error in errors)


def test_fixture_schema_rejects_unsupported_keys() -> None:
    case = _minimal_case(extra_field=True)
    errors = validate_tag_harness_case(case)
    assert any("unsupported keys" in error for error in errors)


def test_fixture_schema_rejects_unsanitized_source() -> None:
    case = _minimal_case(source="production_export")
    errors = validate_tag_harness_case(case)
    assert any("invalid source" in error for error in errors)


def test_fixture_corpus_loads() -> None:
    cases = load_tag_harness_cases()
    assert len(cases) >= 8
    for case in cases:
        assert validate_tag_harness_case(case) == []


def test_failure_record_shape() -> None:
    failure = TagHarnessFailure(
        case_id="taghcase_x",
        layer="document",
        severity="error",
        reason_code="wrong_auto_attach",
        raw_candidate="sqlite",
        expected={"expected_auto_attached": ["sqlite"]},
        actual={"attached": []},
        governance_reasons=["missing"],
    )
    payload = failure.to_dict()
    assert payload["reason_code"] in REASON_CODES
    assert payload["layer"] == "document"


def test_summary_contract_shape() -> None:
    summary = TagHarnessSummary(case_count=1, passed_case_count=1, status="passed")
    payload = summary.to_dict()
    assert payload["phase"] == "v0.3.2.1"
    assert "hard_gates" in payload
    assert "report_metrics" in payload
    assert payload["hard_gates"]["wrong_auto_attached_tags"] == 0


def test_run_tag_harness_eval_passes_fixture_suite() -> None:
    summary = run_tag_harness_eval(cases_path=default_cases_path())
    assert summary.status == "passed", json.dumps(summary.to_dict(), indent=2)
    assert summary.failed_case_count == 0
    assert summary.hard_gates.wrong_auto_attached_tags == 0


def test_invalid_fixture_case_fails_harness(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(
        json.dumps(_minimal_case(source="production_export")) + "\n",
        encoding="utf-8",
    )
    summary = run_tag_harness_eval(cases_path=cases_path)
    assert summary.status == "failed"
    assert summary.failed_case_count >= 1
