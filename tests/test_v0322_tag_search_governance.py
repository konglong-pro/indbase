"""v0.3.2.2 tag/search governance tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from indbase_core.search import governed_search_chunks, SearchOptions
from indbase_core.search_explanations import governed_search_to_json
from indbase_core.search_filters import build_governed_search_filters
from indbase_core.tag_search import SearchFilterError
from indbase_core.tag_search_governance_eval import (
    default_cases_path,
    load_tag_search_cases,
    run_tag_search_governance_eval,
    validate_tag_search_case,
)


def _minimal_case(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "case_id": "tagsearch_test",
        "source": "synthetic",
        "query": "needle",
        "title": "Test",
        "content": "needle body",
        "expected_filter_errors": [],
        "expected_warnings": [],
        "expect_hit": True,
    }
    base.update(overrides)
    return base


def test_fixture_schema_requires_explicit_fields() -> None:
    errors = validate_tag_search_case({"case_id": "x", "source": "synthetic"})
    assert any("missing required fields" in error for error in errors)


def test_fixture_schema_rejects_unsupported_keys() -> None:
    errors = validate_tag_search_case(_minimal_case(extra_field=True))
    assert any("unsupported keys" in error for error in errors)


def test_fixture_schema_rejects_unsanitized_source() -> None:
    errors = validate_tag_search_case(_minimal_case(source="production_export"))
    assert any("invalid source" in error for error in errors)


def test_fixture_corpus_loads() -> None:
    cases = load_tag_search_cases()
    assert len(cases) >= 10
    for case in cases:
        assert validate_tag_search_case(case) == []


def test_summary_contract_shape() -> None:
    summary = run_tag_search_governance_eval()
    payload = summary.to_dict()
    assert payload["phase"] == "v0.3.2.2"
    assert "hard_gates" in payload
    assert payload["status"] in {"passed", "failed"}


def test_governed_search_filter_conflict(tmp_path: Path) -> None:
    from indbase_core.db import connect
    from indbase_core.vault import init_vault

    vault = tmp_path / "vault"
    init_vault(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        filters = build_governed_search_filters(
            connection,
            "category:cat_humanities text",
            category_flag="cat_computer_science",
        )
    assert any(item["code"] == "conflicting_category_filter" for item in filters.filter_errors)


def test_governed_search_json_shape(tmp_path: Path) -> None:
    from indbase_core.db import connect
    from indbase_core.tags import add_document_tag, add_tag
    from indbase_core.vault import init_vault
    from tests.conftest_output import insert_minimal_document
    from tests.test_v032_tag_governance import _insert_current_chunk

    vault = tmp_path / "vault"
    init_vault(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_tag(connection, "rag", tag_type="topic")
        doc_id = insert_minimal_document(connection, vault, body="# Doc\n\nsqlite rag body\n")
        revision_id = connection.execute(
            "SELECT current_revision_id FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()["current_revision_id"]
        _insert_current_chunk(connection, doc_id, str(revision_id), text="sqlite rag body")
        add_document_tag(connection, doc_id, "rag", source="manual", revision_id=str(revision_id))
        from indbase_core.indexer import rebuild_fts_index

        rebuild_fts_index(connection, vault)
        governed = governed_search_chunks(
            connection,
            "sqlite",
            tag="rag",
            options=SearchOptions(log_queries=False),
        )
        payload = governed_search_to_json(governed)
    assert payload["normalized_query"] == "sqlite"
    assert payload["applied_filters"]["tag"]["input"] == "rag"
    assert payload["result_count"] >= 1
    assert "explanation" in payload["results"][0]


def test_release_gate_passes() -> None:
    summary = run_tag_search_governance_eval()
    if summary.status != "passed":
        pytest.fail(json.dumps(summary.to_dict(), indent=2))
