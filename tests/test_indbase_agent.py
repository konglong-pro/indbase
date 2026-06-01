from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_CONSOLER_SDK = Path(__file__).resolve().parents[2] / "consoler" / "sdks" / "python"
if not _CONSOLER_SDK.is_dir():
    pytest.skip(
        "consoler_agent_sdk not available (expected at ../consoler/sdks/python)",
        allow_module_level=True,
    )
sys.path.insert(0, str(_CONSOLER_SDK))

from consoler_agent_sdk import AgentCancelled, AgentError, CancelFlag, EventEmitter  # noqa: E402

from indbase_agent.adapter import IndbaseAgentAdapter  # noqa: E402
from indbase_agent.ingest_probe import probe_ingest_file  # noqa: E402
from indbase_core.ingest import run_m3_ingest_pipeline  # noqa: E402
from indbase_core.vault import init_vault  # noqa: E402


class FakeInteraction:
    def __init__(self, choice: str) -> None:
        self.choice = choice
        self.requests: list[dict] = []

    def request(self, **kwargs) -> str:
        self.requests.append(kwargs)
        return self.choice


def _seed_duplicate_ingest(tmp_path: Path) -> tuple[Path, Path]:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "note.md"
    source.write_text("# Note\n\nBody.\n", encoding="utf-8")
    run_m3_ingest_pipeline(vault, source)
    preview = probe_ingest_file(vault, source)
    assert preview["duplicates"]["is_duplicate"] is True
    return vault, source


def test_preview_is_static_and_does_not_probe_vault(tmp_path) -> None:
    adapter = IndbaseAgentAdapter()
    missing_vault = tmp_path / "definitely-not-a-vault"
    result = adapter.preview("indbase.doctor", {"vault_path": str(missing_vault)})
    assert result["preview_kind"] == "static"
    assert "vault check" in result["summary"]
    assert "indbase.doctor" not in result["summary"]
    assert result["details"]["reads_vault"] is False
    assert "Validate vault path" in result["details"]["steps"]


def test_validate_still_checks_vault_marker(tmp_path) -> None:
    adapter = IndbaseAgentAdapter()
    missing_vault = tmp_path / "definitely-not-a-vault"
    with pytest.raises(AgentError, match="vault"):
        adapter.validate("indbase.doctor", {"vault_path": str(missing_vault)})


def test_doctor_execute_uses_product_terms_in_blocks(tmp_path) -> None:
    adapter = IndbaseAgentAdapter()
    vault = tmp_path / "vault"
    init_vault(vault)
    args = {"vault_path": str(vault)}
    plan = adapter.plan("indbase.doctor", args, "act_doctor")
    emitter = EventEmitter(
        "run_doctor",
        "act_doctor",
        "indbase",
        "indbase.doctor",
        lambda _event: None,
    )

    result = adapter.execute(
        "indbase.doctor",
        args,
        plan,
        action_id="act_doctor",
        run_id="run_doctor",
        emitter=emitter,
        cancel_flag=CancelFlag(),
    )

    markdown = "\n".join(
        str(block["content"]) for block in result["blocks"] if block["type"] == "markdown"
    )
    assert "# Vault check" in markdown
    assert "indbase.doctor" not in markdown


def test_ingest_manifest_command_present() -> None:
    adapter = IndbaseAgentAdapter()
    manifest = adapter.load_manifest()
    names = [command["name"] for command in manifest["commands"]]
    assert "indbase.ingest_file" in names
    ingest = next(command for command in manifest["commands"] if command["name"] == "indbase.ingest_file")
    assert ingest["preview_policy"]["preview_kind"] == "probe_readonly"
    assert ingest["preview_policy"]["requires_approval_before_preview"] is True


def test_ingest_preview_reads_source_without_validate(tmp_path) -> None:
    adapter = IndbaseAgentAdapter()
    vault = tmp_path / "vault"
    marker = vault / ".indbase"
    marker.mkdir(parents=True)
    (marker / "config").mkdir(parents=True)
    (marker / "config" / "config.toml").write_text("x = 1\n", encoding="utf-8")
    source = tmp_path / "note.txt"
    source.write_text("hello ingest probe\n", encoding="utf-8")

    preview = adapter.preview(
        "indbase.ingest_file",
        {"vault_path": str(vault), "source_path": str(source)},
    )
    assert preview["preview_kind"] == "probe_readonly"
    assert preview["inspection"]["source_hash"].startswith("sha256:")
    assert "duplicates" in preview


def test_ingest_validate_rejects_missing_source(tmp_path) -> None:
    adapter = IndbaseAgentAdapter()
    vault = tmp_path / "vault"
    marker = vault / ".indbase"
    marker.mkdir(parents=True)
    missing_source = tmp_path / "missing.txt"
    with pytest.raises(AgentError) as exc_info:
        adapter.validate(
            "indbase.ingest_file",
            {"vault_path": str(vault), "source_path": str(missing_source)},
        )
    assert exc_info.value.code == "source.not_found"


def test_ingest_execute_emits_diff_and_logical_artifact_blocks(tmp_path) -> None:
    adapter = IndbaseAgentAdapter()
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "note.md"
    source.write_text("# Note\n\nBody.\n", encoding="utf-8")
    args = {"vault_path": str(vault), "source_path": str(source)}
    plan = adapter.plan("indbase.ingest_file", args, "act_ingest")

    published: list[dict] = []
    emitter = EventEmitter(
        "run_ingest",
        "act_ingest",
        "indbase",
        "indbase.ingest_file",
        published.append,
    )
    result = adapter.execute(
        "indbase.ingest_file",
        args,
        plan,
        action_id="act_ingest",
        run_id="run_ingest",
        emitter=emitter,
        cancel_flag=CancelFlag(),
    )

    blocks = result["blocks"]
    block_types = [block["type"] for block in blocks]
    assert block_types[:3] == ["markdown", "table", "json"]
    assert "diff" in block_types
    assert block_types.count("artifact") >= 1

    diff = next(block for block in blocks if block["type"] == "diff")
    assert diff["title"] == "Vault state diff"
    assert diff["content"]["language"] == "json"
    assert diff["content"]["from_label"] == "before ingest"
    assert diff["content"]["to_label"] == "after ingest"
    assert "@@" in diff["content"]["unified_diff"]

    for artifact in (block for block in blocks if block["type"] == "artifact"):
        uri = artifact["content"]["uri"]
        assert uri.startswith("indbase://")
        assert not uri.startswith("file://")
        assert ":\\" not in uri

    ingest_run = next(
        block
        for block in blocks
        if block["type"] == "artifact" and block["content"].get("kind") == "indbase.ingest_run"
    )
    assert ingest_run["content"]["uri"].startswith("indbase://ingest_runs/")

    markdown = "\n".join(str(block["content"]) for block in blocks if block["type"] == "markdown")
    assert "# File import" in markdown
    assert "indbase.ingest_file" not in markdown


def test_ingest_preview_unchanged_without_execute_blocks(tmp_path) -> None:
    adapter = IndbaseAgentAdapter()
    vault = tmp_path / "vault"
    marker = vault / ".indbase"
    marker.mkdir(parents=True)
    (marker / "config").mkdir(parents=True)
    (marker / "config" / "config.toml").write_text("x = 1\n", encoding="utf-8")
    source = tmp_path / "note.md"
    source.write_text("# Note\n", encoding="utf-8")

    preview = adapter.preview(
        "indbase.ingest_file",
        {"vault_path": str(vault), "source_path": str(source)},
    )
    assert preview["preview_kind"] == "probe_readonly"
    assert "blocks" not in preview


def test_probe_duplicate_fields_shape(tmp_path) -> None:
    vault = tmp_path / "vault"
    marker = vault / ".indbase"
    marker.mkdir(parents=True)
    source = tmp_path / "doc.md"
    source.write_text("# Doc\n", encoding="utf-8")
    preview = probe_ingest_file(vault, source)
    assert "by_source_hash" in preview["duplicates"]
    assert "by_normalized_source_uri" in preview["duplicates"]
    assert preview["duplicates"]["is_duplicate"] is False


def test_ingest_execute_duplicate_without_interaction_raises(tmp_path) -> None:
    adapter = IndbaseAgentAdapter()
    vault, source = _seed_duplicate_ingest(tmp_path)
    args = {"vault_path": str(vault), "source_path": str(source)}
    plan = adapter.plan("indbase.ingest_file", args, "act_dup")
    emitter = EventEmitter(
        "run_dup",
        "act_dup",
        "indbase",
        "indbase.ingest_file",
        lambda _event: None,
    )
    with pytest.raises(AgentError) as exc_info:
        adapter.execute(
            "indbase.ingest_file",
            args,
            plan,
            action_id="act_dup",
            run_id="run_dup",
            emitter=emitter,
            cancel_flag=CancelFlag(),
        )
    assert exc_info.value.code == "interaction.required"


def test_ingest_execute_duplicate_skip_does_not_run_pipeline(tmp_path) -> None:
    adapter = IndbaseAgentAdapter()
    vault, source = _seed_duplicate_ingest(tmp_path)
    args = {"vault_path": str(vault), "source_path": str(source)}
    plan = adapter.plan("indbase.ingest_file", args, "act_skip")
    interaction = FakeInteraction("skip")
    emitter = EventEmitter(
        "run_skip",
        "act_skip",
        "indbase",
        "indbase.ingest_file",
        lambda _event: None,
    )
    with patch("indbase_agent.adapter.run_m3_ingest_pipeline") as pipeline:
        result = adapter.execute(
            "indbase.ingest_file",
            args,
            plan,
            action_id="act_skip",
            run_id="run_skip",
            emitter=emitter,
            cancel_flag=CancelFlag(),
            interaction=interaction,
        )
        pipeline.assert_not_called()

    assert len(interaction.requests) == 1
    request = interaction.requests[0]
    assert request["interaction_id"] == "duplicate-act_skip"
    assert request["title"] == "Duplicate source detected"
    assert [choice["id"] for choice in request["choices"]] == ["skip", "continue"]

    blocks = result["blocks"]
    skip_json = next(block for block in blocks if block.get("title") == "Skip result")
    assert skip_json["content"]["status"] == "skipped"
    assert skip_json["content"]["reason"] == "duplicate_source"
    assert "diff" not in [block["type"] for block in blocks]
    markdown = "\n".join(str(block["content"]) for block in blocks if block["type"] == "markdown")
    assert "# File import skipped" in markdown
    assert "indbase.ingest_file" not in markdown


def test_ingest_execute_passes_cancel_checkpoint_to_pipeline(tmp_path) -> None:
    adapter = IndbaseAgentAdapter()
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "note.md"
    source.write_text("# Note\n\nBody.\n", encoding="utf-8")
    args = {"vault_path": str(vault), "source_path": str(source)}
    plan = adapter.plan("indbase.ingest_file", args, "act_cancel_hook")
    cancel_flag = CancelFlag()
    emitter = EventEmitter(
        "run_cancel_hook",
        "act_cancel_hook",
        "indbase",
        "indbase.ingest_file",
        lambda _event: None,
    )

    with patch("indbase_agent.adapter.run_m3_ingest_pipeline") as pipeline:
        pipeline.return_value = type(
            "Result",
            (),
            {
                "ingest_id": "ing_test",
                "task_id": "task_test",
                "status": "succeeded",
                "total_items": 1,
                "succeeded_items": 1,
                "failed_items": 0,
                "unsupported_items": 0,
                "duplicate_items": 0,
                "review_items_count": 0,
                "written_revisions": 1,
                "searchable": True,
                "chunked_documents": 1,
                "indexed_documents": 1,
                "indexed_chunks": 1,
                "index_failed_documents": 0,
            },
        )()
        adapter.execute(
            "indbase.ingest_file",
            args,
            plan,
            action_id="act_cancel_hook",
            run_id="run_cancel_hook",
            emitter=emitter,
            cancel_flag=cancel_flag,
        )
        _, kwargs = pipeline.call_args
        assert kwargs["checkpoint"] == cancel_flag.check


def test_ingest_execute_pipeline_cancel_propagates(tmp_path) -> None:
    adapter = IndbaseAgentAdapter()
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "note.md"
    source.write_text("# Note\n\nBody.\n", encoding="utf-8")
    args = {"vault_path": str(vault), "source_path": str(source)}
    plan = adapter.plan("indbase.ingest_file", args, "act_cancel_raise")
    cancel_flag = CancelFlag()
    emitter = EventEmitter(
        "run_cancel_raise",
        "act_cancel_raise",
        "indbase",
        "indbase.ingest_file",
        lambda _event: None,
    )

    def fake_pipeline(
        vault_path: Path,
        source_path: Path,
        *,
        recursive: bool = False,
        checkpoint=None,
        **kwargs,
    ):
        assert checkpoint == cancel_flag.check
        checkpoint("archive")
        raise AgentCancelled("archive")

    with patch("indbase_agent.adapter.run_m3_ingest_pipeline", side_effect=fake_pipeline):
        with pytest.raises(AgentCancelled) as exc_info:
            adapter.execute(
                "indbase.ingest_file",
                args,
                plan,
                action_id="act_cancel_raise",
                run_id="run_cancel_raise",
                emitter=emitter,
                cancel_flag=cancel_flag,
            )

    assert exc_info.value.checkpoint == "archive"


def test_ingest_execute_duplicate_continue_runs_pipeline(tmp_path) -> None:
    adapter = IndbaseAgentAdapter()
    vault, source = _seed_duplicate_ingest(tmp_path)
    args = {"vault_path": str(vault), "source_path": str(source)}
    plan = adapter.plan("indbase.ingest_file", args, "act_continue")
    interaction = FakeInteraction("continue")
    emitter = EventEmitter(
        "run_continue",
        "act_continue",
        "indbase",
        "indbase.ingest_file",
        lambda _event: None,
    )
    result = adapter.execute(
        "indbase.ingest_file",
        args,
        plan,
        action_id="act_continue",
        run_id="run_continue",
        emitter=emitter,
        cancel_flag=CancelFlag(),
        interaction=interaction,
    )

    block_types = [block["type"] for block in result["blocks"]]
    assert "diff" in block_types
    assert block_types.count("artifact") >= 1


def test_manifest_declares_artifact_retrieval() -> None:
    adapter = IndbaseAgentAdapter()
    manifest = adapter.load_manifest()
    capability = manifest["artifact_retrieval"]
    assert capability["uri_schemes"] == ["indbase"]
    assert "indbase.ingest_run" in capability["kinds"]


def test_get_artifact_view_ingest_run_after_ingest(tmp_path) -> None:
    adapter = IndbaseAgentAdapter()
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "note.md"
    source.write_text("# Note\n\nBody.\n", encoding="utf-8")
    args = {"vault_path": str(vault), "source_path": str(source)}
    plan = adapter.plan("indbase.ingest_file", args, "act_view")
    emitter = EventEmitter(
        "run_view",
        "act_view",
        "indbase",
        "indbase.ingest_file",
        lambda _event: None,
    )
    execute_result = adapter.execute(
        "indbase.ingest_file",
        args,
        plan,
        action_id="act_view",
        run_id="run_view",
        emitter=emitter,
        cancel_flag=CancelFlag(),
    )
    ingest_artifact = next(
        block
        for block in execute_result["blocks"]
        if block["type"] == "artifact" and block["content"]["kind"] == "indbase.ingest_run"
    )
    view = adapter.get_artifact_view(
        artifact_uri=ingest_artifact["content"]["uri"],
        kind=ingest_artifact["content"]["kind"],
        block_id=ingest_artifact["block_id"],
        action_id="act_view",
        metadata=ingest_artifact["content"].get("metadata"),
    )
    assert view["artifact_uri"] == ingest_artifact["content"]["uri"]
    assert view["title"] == "Ingest run"
    assert view["blocks"]
    assert all(block["type"] != "artifact" for block in view["blocks"])


def test_get_artifact_view_unknown_kind(tmp_path) -> None:
    adapter = IndbaseAgentAdapter()
    vault = tmp_path / "vault"
    init_vault(vault)
    with pytest.raises(AgentError, match="Unsupported artifact kind"):
        adapter.get_artifact_view(
            artifact_uri="indbase://documents/missing",
            kind="indbase.unknown",
            block_id="blk_x",
            action_id="act_x",
            metadata={"vault_path": str(vault)},
        )


def test_get_artifact_view_missing_entity(tmp_path) -> None:
    adapter = IndbaseAgentAdapter()
    vault = tmp_path / "vault"
    init_vault(vault)
    with pytest.raises(AgentError, match="not found"):
        adapter.get_artifact_view(
            artifact_uri="indbase://documents/doc_missing",
            kind="indbase.document",
            block_id="blk_x",
            action_id="act_x",
            metadata={"vault_path": str(vault)},
        )


def test_get_artifact_view_missing_ingest_run_returns_agent_error(tmp_path) -> None:
    adapter = IndbaseAgentAdapter()
    vault = tmp_path / "vault"
    init_vault(vault)
    with pytest.raises(AgentError, match="Ingest run not found") as exc_info:
        adapter.get_artifact_view(
            artifact_uri="indbase://ingest_runs/ingest_missing",
            kind="indbase.ingest_run",
            block_id="blk_x",
            action_id="act_x",
            metadata={"vault_path": str(vault)},
        )
    assert exc_info.value.code == "artifact.not_found"
