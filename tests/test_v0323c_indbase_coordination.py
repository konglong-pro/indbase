from __future__ import annotations

import json
from pathlib import Path


SOURCE_TRUST_ACTION_SURFACE = {
    "indbase.doctor",
    "indbase.ingest_file",
    "indbase.search_sources",
    "indbase.doc_show",
    "indbase.review_list",
    "indbase.review_show",
    "indbase.task_list",
    "indbase.task_show",
    "indbase.error_list",
    "indbase.error_show",
}

READ_ONLY_ARTIFACT_KINDS = {
    "indbase.document",
    "indbase.review_item",
    "indbase.task",
    "indbase.error",
    "indbase.doctor_report",
}


def _manifest() -> dict:
    path = Path("src/indbase_agent/manifest.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_v0323c_manifest_keeps_source_trust_loop_contract_for_consoler_variant() -> None:
    manifest = _manifest()
    commands = {command["name"]: command for command in manifest["commands"]}

    assert set(commands) == SOURCE_TRUST_ACTION_SURFACE
    assert commands["indbase.ingest_file"]["side_effects"] == [
        "read_source_file",
        "write_vault_database",
        "write_vault_files",
        "read_vault_config",
    ]

    write_commands = [
        name
        for name, command in commands.items()
        if any(str(effect).startswith("write_") for effect in command.get("side_effects", []))
    ]
    assert write_commands == ["indbase.ingest_file"]

    assert set(commands["indbase.doc_show"]["args_schema"]["properties"]) == {
        "vault_path",
        "doc_id",
    }
    assert "indbase.ask" not in commands
    assert "indbase.review_resolve" not in commands


def test_v0323c_manifest_exposes_readonly_artifact_kinds_for_open_back_ux() -> None:
    manifest = _manifest()
    retrieval = manifest["artifact_retrieval"]

    assert retrieval["uri_schemes"] == ["indbase"]
    assert READ_ONLY_ARTIFACT_KINDS.issubset(set(retrieval["kinds"]))
    assert "indbase.ingest_run" in retrieval["kinds"]
    assert "indbase.output_artifact" not in retrieval["kinds"]
    assert "indbase.document_revision" not in retrieval["kinds"]
