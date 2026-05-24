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

from indbase_agent.adapter import IndbaseAgentAdapter  # noqa: E402
from indbase_agent.ingest_probe import probe_ingest_file  # noqa: E402
from indbase_core.vault import init_vault  # noqa: E402


def test_preview_is_static_and_does_not_probe_vault(tmp_path) -> None:
    adapter = IndbaseAgentAdapter()
    missing_vault = tmp_path / "definitely-not-a-vault"
    result = adapter.preview("indbase.doctor", {"vault_path": str(missing_vault)})
    assert result["preview_kind"] == "static"
    assert result["details"]["reads_vault"] is False
    assert "Validate vault path" in result["details"]["steps"]


def test_validate_still_checks_vault_marker(tmp_path) -> None:
    adapter = IndbaseAgentAdapter()
    missing_vault = tmp_path / "definitely-not-a-vault"
    with pytest.raises(AgentError, match="vault"):
        adapter.validate("indbase.doctor", {"vault_path": str(missing_vault)})


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
