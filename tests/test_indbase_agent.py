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
from indbase_core.tasks import create_task, finish_task  # noqa: E402
from indbase_core.vault import init_vault  # noqa: E402


EXPECTED_COMMANDS = [
    "indbase.doctor",
    "indbase.ingest_file",
    "indbase.search_sources",
    "indbase.review_list",
    "indbase.review_show",
    "indbase.task_list",
    "indbase.task_show",
    "indbase.error_list",
    "indbase.error_show",
    "indbase.doc_show",
]


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


def _insert_current_chunk(
    connection,
    doc_id: str,
    revision_id: str,
    *,
    text: str,
    sequence: int = 1,
) -> str:
    chunk_id = f"chk_agent_{doc_id}_{sequence}"
    connection.execute(
        """
        INSERT INTO chunks (
          chunk_id, doc_id, revision_id, sequence, heading_path_json, text,
          token_count, content_hash, is_current, created_at, updated_at
        ) VALUES (?, ?, ?, ?, '[]', ?, 10, 'hash', 1, '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')
        """,
        (chunk_id, doc_id, revision_id, sequence, text),
    )
    connection.commit()
    return chunk_id


def _seed_search_document(tmp_path: Path, *, text: str = "sqlite rag source body") -> tuple[Path, str, str]:
    vault = tmp_path / "vault"
    init_vault(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc_id = insert_minimal_document(connection, vault, body=f"# Doc\n\n{text}\n")
        revision_id = connection.execute(
            "SELECT current_revision_id FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()["current_revision_id"]
        _insert_current_chunk(connection, doc_id, str(revision_id), text=text)
        rebuild_fts_index(connection, vault)
    return vault, doc_id, str(revision_id)


def test_manifest_exposes_exact_first_version_command_surface() -> None:
    adapter = IndbaseAgentAdapter()
    manifest = adapter.load_manifest()

    names = [command["name"] for command in manifest["commands"]]
    assert names == EXPECTED_COMMANDS
    assert "indbase.review_resolve" not in names
    assert "indbase.doc_set_category" not in names
    assert "indbase.doc_add_tag" not in names
    assert all("permissions" in command for command in manifest["commands"])
    assert all(command["permissions"] for command in manifest["commands"])

    write_commands = [
        command["name"]
        for command in manifest["commands"]
        if any(str(effect).startswith("write_") for effect in command.get("side_effects", []))
    ]
    assert write_commands == ["indbase.ingest_file"]
    assert manifest["artifact_retrieval"]["kinds"] == [
        "indbase.ingest_run",
        "indbase.document",
        "indbase.review_item",
        "indbase.task",
        "indbase.error",
        "indbase.doctor_report",
    ]


def test_indbase_core_does_not_import_consoler_sdk() -> None:
    hits = []
    for path in Path("src/indbase_core").rglob("*.py"):
        if "consoler_agent_sdk" in path.read_text(encoding="utf-8"):
            hits.append(path.as_posix())
    assert hits == []


def test_doc_show_accepts_doc_id_only(tmp_path: Path) -> None:
    vault, doc_id, _revision_id = _seed_search_document(tmp_path)
    adapter = IndbaseAgentAdapter()
    manifest = adapter.load_manifest()
    doc_show = next(command for command in manifest["commands"] if command["name"] == "indbase.doc_show")
    assert set(doc_show["args_schema"]["properties"]) == {"vault_path", "doc_id"}

    with pytest.raises(AgentError) as exc_info:
        adapter.validate(
            "indbase.doc_show",
            {"vault_path": str(vault), "doc_id": doc_id, "title": "Demo"},
        )
    assert exc_info.value.code == "args.unsupported"


def test_search_sources_text_search_returns_json_and_document_artifact(tmp_path: Path) -> None:
    vault, doc_id, _revision_id = _seed_search_document(tmp_path)
    adapter = IndbaseAgentAdapter()

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        before = connection.execute("SELECT COUNT(*) AS count FROM search_queries").fetchone()["count"]

    result = _execute(
        adapter,
        "indbase.search_sources",
        {"vault_path": str(vault), "query": "sqlite", "top_k": 5},
    )

    payload = _json_block(result, "Search JSON")["content"]
    assert payload["result_count"] >= 1
    assert payload["results"][0]["doc_id"] == doc_id
    assert payload["results"][0]["revision_id"]
    assert payload["results"][0]["chunk_id"]
    assert "snippet" in payload["results"][0]

    artifacts = [block for block in result["blocks"] if block["type"] == "artifact"]
    assert len(artifacts) == 1
    assert artifacts[0]["content"]["kind"] == "indbase.document"
    assert artifacts[0]["content"]["metadata"]["vault_path"] == vault.as_posix()

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        after = connection.execute("SELECT COUNT(*) AS count FROM search_queries").fetchone()["count"]
    assert after == before


def test_search_sources_filter_only_search_uses_trusted_tag_relation(tmp_path: Path) -> None:
    vault, doc_id, revision_id = _seed_search_document(tmp_path)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_tag(connection, "rag", tag_type="topic")
        add_document_tag(connection, doc_id, "rag", source="manual", revision_id=revision_id)
        rebuild_fts_index(connection, vault)

    result = _execute(
        IndbaseAgentAdapter(),
        "indbase.search_sources",
        {"vault_path": str(vault), "query": "", "tag": "rag"},
    )
    payload = _json_block(result, "Search JSON")["content"]
    assert payload["normalized_query"] == ""
    assert payload["applied_filters"]["tag"]["input"] == "rag"
    assert payload["result_count"] >= 1


def test_search_sources_invalid_filter_has_structured_details(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    adapter = IndbaseAgentAdapter()

    with pytest.raises(AgentError) as exc_info:
        adapter.validate(
            "indbase.search_sources",
            {"vault_path": str(vault), "query": "body", "tag": "missing-formal-tag"},
        )
    assert exc_info.value.code == "search.filter_invalid"
    assert exc_info.value.details["filter_errors"][0]["code"] == "unknown_tag"


def test_search_sources_empty_query_without_filter_is_rejected(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    adapter = IndbaseAgentAdapter()

    with pytest.raises(AgentError) as exc_info:
        adapter.validate("indbase.search_sources", {"vault_path": str(vault), "query": ""})
    assert exc_info.value.code == "search.query_required"


def test_search_sources_valid_filter_with_no_matches_succeeds(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_tag(connection, "empty-filter", tag_type="topic")

    result = _execute(
        IndbaseAgentAdapter(),
        "indbase.search_sources",
        {"vault_path": str(vault), "query": "", "tag": "empty-filter"},
    )
    payload = _json_block(result, "Search JSON")["content"]
    assert payload["result_count"] == 0
    assert payload["results"] == []


def test_doc_show_returns_bounded_trusted_metadata_tags_and_preview(tmp_path: Path) -> None:
    long_body = "trusted source preview " * 300
    vault = tmp_path / "vault"
    init_vault(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc_id = insert_minimal_document(connection, vault, body=f"# Doc\n\n{long_body}\n")
        revision_id = connection.execute(
            "SELECT current_revision_id FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()["current_revision_id"]
        connection.execute(
            """
            UPDATE documents
            SET category_id = 'cat_computer_science',
                classification_status = 'manual',
                needs_review = 1
            WHERE doc_id = ?
            """,
            (doc_id,),
        )
        add_tag(connection, "trusted-tag", tag_type="topic")
        add_document_tag(connection, doc_id, "trusted-tag", source="manual", revision_id=str(revision_id))
        connection.commit()

    result = _execute(
        IndbaseAgentAdapter(),
        "indbase.doc_show",
        {"vault_path": str(vault), "doc_id": doc_id},
    )
    payload = _json_block(result, "Document JSON")["content"]
    assert payload["document"]["doc_id"] == doc_id
    assert payload["classification"]["classification_status"] == "manual"
    assert payload["classification"]["needs_review"] is True
    assert payload["current_revision"]["revision_id"] == revision_id
    assert payload["tags"][0]["name"] == "trusted-tag"
    assert payload["tags"][0]["source"] == "manual"
    assert payload["view_semantics"] == "current_vault_state"
    assert payload["source_preview"]["max_chars"] == 4000
    assert payload["source_preview"]["truncated"] is True
    assert len(payload["source_preview"]["text"]) <= 4000


def test_review_task_error_commands_are_read_only_views(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        review_id = create_review_item(
            connection,
            review_type="unsupported_source",
            target_type="document",
            target_id="doc_test",
            reason="needs inspection",
        )
        task_id = create_task(connection, "agent_test", {"ok": True})
        finish_task(connection, task_id, "succeeded", result_data={"done": True})
        error_id = record_error(
            connection,
            component="agent_test",
            error_type="TestError",
            message="visible failure",
        )
        connection.commit()

    adapter = IndbaseAgentAdapter()
    review_show = _execute(adapter, "indbase.review_show", {"vault_path": str(vault), "review_id": review_id})
    task_show = _execute(adapter, "indbase.task_show", {"vault_path": str(vault), "task_id": task_id})
    error_show = _execute(adapter, "indbase.error_show", {"vault_path": str(vault), "error_id": error_id})
    review_list = _execute(adapter, "indbase.review_list", {"vault_path": str(vault), "status": "all"})
    task_list = _execute(adapter, "indbase.task_list", {"vault_path": str(vault)})
    error_list = _execute(adapter, "indbase.error_list", {"vault_path": str(vault)})

    assert _json_block(review_show, "Review JSON")["content"]["status"] == "pending"
    assert _json_block(task_show, "Task JSON")["content"]["task"]["task_id"] == task_id
    assert _json_block(error_show, "Error JSON")["content"]["error_id"] == error_id
    assert _json_block(review_list, "Review JSON")["content"]["review_items"]
    assert _json_block(task_list, "Task JSON")["content"]["tasks"]
    assert _json_block(error_list, "Error JSON")["content"]["errors"]

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        status = connection.execute(
            "SELECT status FROM review_items WHERE review_id = ?",
            (review_id,),
        ).fetchone()["status"]
    assert status == "pending"


def test_ingest_file_emits_only_ingest_and_document_artifacts_with_vault_metadata(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "note.md"
    source.write_text("# Note\n\nAgent ingest body.\n", encoding="utf-8")

    result = _execute(
        IndbaseAgentAdapter(),
        "indbase.ingest_file",
        {"vault_path": str(vault), "source_path": str(source)},
    )

    artifacts = [block for block in result["blocks"] if block["type"] == "artifact"]
    kinds = [block["content"]["kind"] for block in artifacts]
    assert kinds == ["indbase.ingest_run", "indbase.document"]
    assert all(block["content"]["metadata"]["vault_path"] == vault.as_posix() for block in artifacts)


def test_ingest_file_then_search_sources_doc_show_and_artifact_view(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    token = "SOURCE_TRUST_PROBE_TOKEN_0323A"
    source = tmp_path / "source-trust-note.md"
    source.write_text(
        "# Source Trust Note\n\n"
        f"This synthetic source contains the unique token {token}. "
        "It is long enough to produce a trusted current revision, chunks, and FTS rows. "
        "The content is sanitized and deterministic for the consoler probe stabilization gate.\n",
        encoding="utf-8",
    )
    adapter = IndbaseAgentAdapter()

    ingest_result = _execute(
        adapter,
        "indbase.ingest_file",
        {"vault_path": str(vault), "source_path": str(source)},
    )
    ingest_payload = _json_block(ingest_result, "Raw result")["content"]
    assert ingest_payload["status"] == "succeeded"
    assert ingest_payload["succeeded_items"] == 1
    assert ingest_payload["written_revisions"] == 1
    assert ingest_payload["indexed_chunks"] >= 1

    search_result = _execute(
        adapter,
        "indbase.search_sources",
        {"vault_path": str(vault), "query": token, "top_k": 5},
    )
    search_payload = _json_block(search_result, "Search JSON")["content"]
    assert search_payload["filter_errors"] == []
    assert search_payload["result_count"] >= 1
    first_hit = search_payload["results"][0]
    assert first_hit["doc_id"]
    assert first_hit["revision_id"]
    assert first_hit["chunk_id"]
    assert token in first_hit["snippet"]

    document_artifacts = [
        block
        for block in search_result["blocks"]
        if block["type"] == "artifact" and block["content"]["kind"] == "indbase.document"
    ]
    assert 1 <= len(document_artifacts) <= 5
    assert all(
        block["content"]["metadata"]["vault_path"] == vault.as_posix()
        for block in document_artifacts
    )

    doc_show = _execute(
        adapter,
        "indbase.doc_show",
        {"vault_path": str(vault), "doc_id": first_hit["doc_id"]},
    )
    doc_payload = _json_block(doc_show, "Document JSON")["content"]
    assert doc_payload["document"]["doc_id"] == first_hit["doc_id"]
    assert doc_payload["current_revision"]["revision_id"] == first_hit["revision_id"]
    assert doc_payload["source_preview"]["max_chars"] == 4000
    assert len(doc_payload["source_preview"]["text"]) <= 4000

    artifact = document_artifacts[0]["content"]
    view = adapter.get_artifact_view(
        artifact_uri=artifact["uri"],
        kind=artifact["kind"],
        block_id=document_artifacts[0].get("id", "blk_test"),
        action_id="act_test",
        metadata=artifact["metadata"],
    )
    view_payload = _json_block(view, "Document JSON")["content"]
    assert view_payload["document"]["doc_id"] == first_hit["doc_id"]
    assert all(block["type"] != "artifact" for block in view["blocks"])
    assert view_payload["source_preview"]["max_chars"] == 4000

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        retired_errors = connection.execute(
            "SELECT COUNT(*) AS count FROM errors WHERE error_type = 'legacy_conversion_retired'"
        ).fetchone()["count"]
    assert retired_errors == 0


@pytest.mark.no_fake_swallow_conversion
def test_ingest_file_keeps_disabled_swallow_legacy_conversion_visible(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "disabled-swallow.md"
    source.write_text(
        "# Disabled Swallow\n\n"
        "This source should not be converted when features.swallow_ingest is false.\n",
        encoding="utf-8",
    )

    result = _execute(
        IndbaseAgentAdapter(),
        "indbase.ingest_file",
        {"vault_path": str(vault), "source_path": str(source)},
    )
    payload = _json_block(result, "Raw result")["content"]
    assert payload["status"] == "completed_with_issues"
    assert payload["succeeded_items"] == 0
    assert payload["failed_items"] == 1
    assert payload["written_revisions"] == 0
    assert payload["indexed_chunks"] == 0

    artifacts = [block for block in result["blocks"] if block["type"] == "artifact"]
    assert artifacts
    assert artifacts[0]["content"]["kind"] == "indbase.ingest_run"
    assert all(block["content"]["metadata"]["vault_path"] == vault.as_posix() for block in artifacts)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        row = connection.execute(
            "SELECT error_type FROM errors WHERE error_type = 'legacy_conversion_retired'"
        ).fetchone()
        indexed_chunks = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()["count"]
    assert row is not None
    assert indexed_chunks == 0


def test_document_artifact_view_is_bounded_and_not_nested(tmp_path: Path) -> None:
    vault, doc_id, _revision_id = _seed_search_document(tmp_path)
    adapter = IndbaseAgentAdapter()

    view = adapter.get_artifact_view(
        artifact_uri=f"indbase://documents/{doc_id}",
        kind="indbase.document",
        block_id="blk_test",
        action_id="act_test",
        metadata={"vault_path": vault.as_posix()},
    )

    assert view["metadata"]["vault_path"] == vault.as_posix()
    assert view["blocks"]
    assert all(block["type"] != "artifact" for block in view["blocks"])
    payload = _json_block(view, "Document JSON")["content"]
    assert payload["document"]["doc_id"] == doc_id
    assert payload["view_semantics"] == "current_vault_state"
    assert payload["limits"]["source_preview_chars"] == 4000
    assert payload["source_preview"]["max_chars"] == 4000
