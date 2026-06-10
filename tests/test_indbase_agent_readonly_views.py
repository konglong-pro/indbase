from __future__ import annotations

import sys
from pathlib import Path

import pytest

_CONSOLER_SDK = Path(__file__).resolve().parents[2] / "consoler" / "sdks" / "python"
if not _CONSOLER_SDK.is_dir():
    pytest.skip(
        "consoler_agent_sdk not available (expected at ../consoler/sdks/python)",
        allow_module_level=True,
    )
sys.path.insert(0, str(_CONSOLER_SDK))

from consoler_agent_sdk import AgentError, CancelFlag, EventEmitter  # noqa: E402

from conftest_output import insert_minimal_document  # noqa: E402
from indbase_agent.adapter import IndbaseAgentAdapter  # noqa: E402
from indbase_core.db import connect  # noqa: E402
from indbase_core.errors import record_error  # noqa: E402
from indbase_core.indexer import rebuild_fts_index  # noqa: E402
from indbase_core.reviews import create_review_item  # noqa: E402
from indbase_core.tags import add_document_tag, add_tag  # noqa: E402
from indbase_core.tasks import add_task_event, create_task, finish_task  # noqa: E402
from indbase_core.vault import init_vault  # noqa: E402


def _emitter(command: str) -> EventEmitter:
    return EventEmitter(
        f"run_{command.replace('.', '_')}",
        f"act_{command.replace('.', '_')}",
        "indbase",
        command,
        lambda _event: None,
    )


def _execute(adapter: IndbaseAgentAdapter, command: str, args: dict) -> dict:
    plan = adapter.plan(command, args, "act_test")
    return adapter.execute(
        command,
        args,
        plan,
        action_id="act_test",
        run_id="run_test",
        emitter=_emitter(command),
        cancel_flag=CancelFlag(),
    )


def _json_block(result: dict, title: str) -> dict:
    return next(block for block in result["blocks"] if block.get("title") == title)


def _artifact_blocks(result: dict) -> list[dict]:
    return [block for block in result["blocks"] if block.get("type") == "artifact"]


def _insert_current_chunk(
    connection,
    doc_id: str,
    revision_id: str,
    *,
    text: str,
    sequence: int,
) -> str:
    chunk_id = f"chk_v0323b_{sequence:03d}"
    connection.execute(
        """
        INSERT INTO chunks (
          chunk_id, doc_id, revision_id, sequence, heading_path_json, text,
          token_count, content_hash, is_current, created_at, updated_at
        ) VALUES (?, ?, ?, ?, '[]', ?, 10, ?, 1, '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')
        """,
        (chunk_id, doc_id, revision_id, sequence, text, f"hash_{sequence}"),
    )
    connection.commit()
    return chunk_id


def _seed_operational_vault(tmp_path: Path, *, long_document: bool = False) -> dict[str, str | Path]:
    vault = tmp_path / "vault"
    init_vault(vault)
    body = "trusted readonly source " * (300 if long_document else 20)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc_id = insert_minimal_document(connection, vault, body=f"# Doc\n\n{body}\n")
        revision_id = connection.execute(
            "SELECT current_revision_id FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()["current_revision_id"]
        _insert_current_chunk(
            connection,
            doc_id,
            str(revision_id),
            text="trusted readonly source chunk",
            sequence=1,
        )
        review_id = create_review_item(
            connection,
            review_type="unsupported_source",
            target_type="document",
            target_id=doc_id,
            reason="needs inspection",
        )
        task_id = create_task(connection, "readonly_view_task", {"doc_id": doc_id})
        add_task_event(connection, task_id, "started", "started readonly view task")
        finish_task(connection, task_id, "succeeded", result_data={"doc_id": doc_id})
        error_id = record_error(
            connection,
            component="readonly_view",
            error_type="ReadonlyViewError",
            message="visible readonly error",
            task_id=task_id,
        )
        connection.commit()
        rebuild_fts_index(connection, vault)
    return {
        "vault": vault,
        "doc_id": doc_id,
        "revision_id": str(revision_id),
        "review_id": review_id,
        "task_id": task_id,
        "error_id": error_id,
    }


def test_show_commands_emit_focused_artifacts_and_list_commands_do_not(tmp_path: Path) -> None:
    seeded = _seed_operational_vault(tmp_path)
    vault = seeded["vault"]
    adapter = IndbaseAgentAdapter()

    doc_show = _execute(adapter, "indbase.doc_show", {"vault_path": str(vault), "doc_id": seeded["doc_id"]})
    review_show = _execute(
        adapter,
        "indbase.review_show",
        {"vault_path": str(vault), "review_id": seeded["review_id"]},
    )
    task_show = _execute(adapter, "indbase.task_show", {"vault_path": str(vault), "task_id": seeded["task_id"]})
    error_show = _execute(
        adapter,
        "indbase.error_show",
        {"vault_path": str(vault), "error_id": seeded["error_id"]},
    )
    doctor = _execute(adapter, "indbase.doctor", {"vault_path": str(vault)})

    assert [_artifact_blocks(doc_show)[0]["content"]["kind"]] == ["indbase.document"]
    assert [_artifact_blocks(review_show)[0]["content"]["kind"]] == ["indbase.review_item"]
    assert [_artifact_blocks(task_show)[0]["content"]["kind"]] == ["indbase.task"]
    assert [_artifact_blocks(error_show)[0]["content"]["kind"]] == ["indbase.error"]
    assert [_artifact_blocks(doctor)[0]["content"]["kind"]] == ["indbase.doctor_report"]
    assert all(
        _artifact_blocks(result)[0]["content"]["metadata"]["vault_path"] == Path(vault).as_posix()
        for result in (doc_show, review_show, task_show, error_show, doctor)
    )

    review_list = _execute(adapter, "indbase.review_list", {"vault_path": str(vault), "status": "all"})
    task_list = _execute(adapter, "indbase.task_list", {"vault_path": str(vault)})
    error_list = _execute(adapter, "indbase.error_list", {"vault_path": str(vault)})

    assert _artifact_blocks(review_list) == []
    assert _artifact_blocks(task_list) == []
    assert _artifact_blocks(error_list) == []


def test_artifact_views_have_current_state_envelope_and_no_nested_artifacts(tmp_path: Path) -> None:
    seeded = _seed_operational_vault(tmp_path)
    vault = Path(seeded["vault"])
    adapter = IndbaseAgentAdapter()
    cases = [
        (f"indbase://documents/{seeded['doc_id']}", "indbase.document", "Document JSON"),
        (f"indbase://reviews/{seeded['review_id']}", "indbase.review_item", "Review item JSON"),
        (f"indbase://tasks/{seeded['task_id']}", "indbase.task", "Task view JSON"),
        (f"indbase://errors/{seeded['error_id']}", "indbase.error", "Error view JSON"),
        ("indbase://doctor-reports/current", "indbase.doctor_report", "Doctor report JSON"),
    ]

    for artifact_uri, kind, json_title in cases:
        view = adapter.get_artifact_view(
            artifact_uri=artifact_uri,
            kind=kind,
            block_id="blk_test",
            action_id="act_test",
            metadata={"vault_path": vault.as_posix()},
        )
        payload = _json_block(view, json_title)["content"]
        assert "vault_path" not in view["metadata"]
        assert view["metadata"]["vault_ref"] == "current"
        assert "vault_path" not in payload
        assert view["truncated"] == payload["truncated"]
        assert payload["view_semantics"] == "current_vault_state"
        assert isinstance(payload["limits"], dict)
        assert isinstance(payload["truncated"], bool)
        assert all(block["type"] != "artifact" for block in view["blocks"])


def test_document_view_enforces_preview_chunk_and_tag_budgets(tmp_path: Path) -> None:
    seeded = _seed_operational_vault(tmp_path, long_document=True)
    vault = Path(seeded["vault"])
    doc_id = str(seeded["doc_id"])
    revision_id = str(seeded["revision_id"])
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        connection.execute("DELETE FROM index_build_entries WHERE doc_id = ?", (doc_id,))
        connection.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
        for sequence in range(1, 13):
            _insert_current_chunk(
                connection,
                doc_id,
                revision_id,
                text=("oversized chunk text " * 40) + str(sequence),
                sequence=sequence,
            )
        for index in range(52):
            tag_name = f"readonly-tag-{index:02d}"
            add_tag(connection, tag_name, tag_type="topic")
            add_document_tag(connection, doc_id, tag_name, source="manual", revision_id=revision_id)
        connection.commit()

    view = IndbaseAgentAdapter().get_artifact_view(
        artifact_uri=f"indbase://documents/{doc_id}",
        kind="indbase.document",
        block_id="blk_test",
        action_id="act_test",
        metadata={"vault_path": vault.as_posix()},
    )
    payload = _json_block(view, "Document JSON")["content"]

    assert payload["limits"]["source_preview_chars"] == 4000
    assert payload["limits"]["current_chunk_rows"] == 10
    assert payload["limits"]["formal_tag_rows"] == 50
    assert payload["truncated"] is True
    assert payload["source_preview"]["truncated"] is True
    assert len(payload["source_preview"]["text"]) <= 4000
    assert len(payload["current_chunks"]) == 10
    assert len(payload["tags"]) == 50


def test_task_and_error_views_enforce_budgets(tmp_path: Path) -> None:
    seeded = _seed_operational_vault(tmp_path)
    vault = Path(seeded["vault"])
    task_id = str(seeded["task_id"])
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        for index in range(60):
            add_task_event(connection, task_id, "progress", f"event {index}")
        long_message = "long error detail " * 400
        error_id = record_error(
            connection,
            component="readonly_view",
            error_type="LongError",
            message=long_message,
            developer_message=long_message,
            payload={"detail": long_message},
        )
        connection.commit()

    adapter = IndbaseAgentAdapter()
    task_view = adapter.get_artifact_view(
        artifact_uri=f"indbase://tasks/{task_id}",
        kind="indbase.task",
        block_id="blk_test",
        action_id="act_test",
        metadata={"vault_path": vault.as_posix()},
    )
    error_view = adapter.get_artifact_view(
        artifact_uri=f"indbase://errors/{error_id}",
        kind="indbase.error",
        block_id="blk_test",
        action_id="act_test",
        metadata={"vault_path": vault.as_posix()},
    )

    task_payload = _json_block(task_view, "Task view JSON")["content"]
    error_payload = _json_block(error_view, "Error view JSON")["content"]
    assert task_payload["limits"]["task_event_rows"] == 50
    assert len(task_payload["events"]) == 50
    assert task_payload["truncated"] is True
    assert error_payload["limits"]["message_chars"] == 4000
    assert len(error_payload["error"]["message"]) <= 4000
    assert error_payload["error"]["message_truncated"] is True
    assert error_payload["truncated"] is True


def test_artifact_uri_and_kind_errors_are_stable(tmp_path: Path) -> None:
    seeded = _seed_operational_vault(tmp_path)
    vault = Path(seeded["vault"])
    adapter = IndbaseAgentAdapter()

    error_cases = [
        ("file://documents/doc_1", "indbase.document", "invalid_artifact_uri"),
        ("indbase://documents/doc_1?path=foo", "indbase.document", "invalid_artifact_uri"),
        ("indbase://documents/doc_1/extra", "indbase.document", "invalid_artifact_uri"),
        ("indbase://documents/*.md", "indbase.document", "artifact_scope_rejected"),
        ("indbase://documents/title:Demo", "indbase.document", "artifact_scope_rejected"),
        (
            f"indbase://reviews/{seeded['review_id']}",
            "indbase.document",
            "unsupported_artifact_kind",
        ),
        ("indbase://documents/doc_missing", "indbase.document", "artifact_not_found"),
    ]
    for artifact_uri, kind, code in error_cases:
        with pytest.raises(AgentError) as exc_info:
            adapter.get_artifact_view(
                artifact_uri=artifact_uri,
                kind=kind,
                block_id="blk_test",
                action_id="act_test",
                metadata={"vault_path": vault.as_posix()},
            )
        assert exc_info.value.code == code

    with pytest.raises(AgentError) as exc_info:
        adapter.get_artifact_view(
            artifact_uri=f"indbase://documents/{seeded['doc_id']}",
            kind="indbase.document",
            block_id="blk_test",
            action_id="act_test",
            metadata={},
        )
    assert exc_info.value.code == "vault_not_initialized"


def test_doctor_report_artifact_view_is_ephemeral_and_read_only(tmp_path: Path) -> None:
    seeded = _seed_operational_vault(tmp_path)
    vault = Path(seeded["vault"])

    def counts() -> dict[str, int]:
        with connect(vault / ".indbase" / "db.sqlite") as connection:
            return {
                "tasks": connection.execute("SELECT COUNT(*) AS count FROM tasks").fetchone()["count"],
                "task_events": connection.execute("SELECT COUNT(*) AS count FROM task_events").fetchone()["count"],
                "errors": connection.execute("SELECT COUNT(*) AS count FROM errors").fetchone()["count"],
                "review_items": connection.execute("SELECT COUNT(*) AS count FROM review_items").fetchone()["count"],
            }

    before = counts()
    view = IndbaseAgentAdapter().get_artifact_view(
        artifact_uri="indbase://doctor-reports/current",
        kind="indbase.doctor_report",
        block_id="blk_test",
        action_id="act_test",
        metadata={"vault_path": vault.as_posix()},
    )
    after = counts()
    payload = _json_block(view, "Doctor report JSON")["content"]

    assert before == after
    assert payload["doctor_report"]["persistence"] == "ephemeral_diagnostic"
    assert payload["limits"]["doctor_finding_rows"] == 50
    assert len(payload["doctor_report"]["findings"]) <= 50
