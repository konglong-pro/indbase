from pathlib import Path

from indbase_core.db import connect
from indbase_core.errors import get_error, list_errors, record_error
from indbase_core.reviews import (
    create_review_item,
    get_review_item,
    list_review_items,
    resolve_review_item,
    resolve_review_items,
)
from indbase_core.vault import init_vault


def test_review_helpers_list_show_and_resolve(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        review_id = create_review_item(
            connection,
            review_type="unsupported_source",
            target_type="ingest_item",
            target_id="ingest_item_test",
            reason="Unsupported source extension: .pdf",
            priority=50,
        )
        pending = list_review_items(connection)
        shown = get_review_item(connection, review_id)
        resolved = resolve_review_item(connection, review_id, note="handled", resolved_by="test")
        pending_after_resolve = list_review_items(connection)
        all_items = list_review_items(connection, status=None)
    finally:
        connection.close()

    assert pending[0]["review_id"] == review_id
    assert shown["type"] == "unsupported_source"
    assert resolved["status"] == "resolved"
    assert resolved["resolved_at"] is not None
    assert resolved["resolution_note"] == "handled"
    assert resolved["resolved_by"] == "test"
    assert pending_after_resolve == []
    assert all_items[0]["review_id"] == review_id


def test_review_helpers_filter_and_resolve_many(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        first = create_review_item(
            connection,
            review_type="unsupported_source",
            target_type="ingest_item",
            target_id="item_a",
            reason="unsupported",
        )
        second = create_review_item(
            connection,
            review_type="conversion_low_quality",
            target_type="converter_run",
            target_id="run_a",
            reason="low quality",
        )
        connection.commit()

        unsupported = list_review_items(connection, review_type="unsupported_source")
        converter = list_review_items(connection, target_type="converter_run")
        resolved = resolve_review_items(
            connection,
            [first, second],
            note="batch accepted",
            resolved_by="operator",
        )
        pending = list_review_items(connection)
    finally:
        connection.close()

    assert [row["review_id"] for row in unsupported] == [first]
    assert [row["review_id"] for row in converter] == [second]
    assert {row["review_id"] for row in resolved} == {first, second}
    assert {row["resolution_note"] for row in resolved} == {"batch accepted"}
    assert {row["resolved_by"] for row in resolved} == {"operator"}
    assert pending == []


def test_error_helpers_list_show_and_filter(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        error_id = record_error(
            connection,
            component="converter",
            error_type="ConversionFailed",
            message="failed",
            severity="error",
            retryable=False,
            payload={"doc_id": "doc_test"},
        )
        all_errors = list_errors(connection)
        filtered = list_errors(connection, component="converter", severity="error")
        missing = list_errors(connection, component="doctor")
        shown = get_error(connection, error_id)
    finally:
        connection.close()

    assert all_errors[0]["error_id"] == error_id
    assert filtered[0]["error_type"] == "ConversionFailed"
    assert missing == []
    assert shown["payload_json"] == '{"doc_id": "doc_test"}'
