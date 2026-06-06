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

OBJECT_SHOW_ID_FIELDS = {
    "indbase.doc_show": "doc_id",
    "indbase.review_show": "review_id",
    "indbase.task_show": "task_id",
    "indbase.error_show": "error_id",
}


def _manifest() -> dict:
    path = Path("src/indbase_agent/manifest.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_v0323d_manifest_keeps_intent_drafting_as_consoler_form_prefill() -> None:
    manifest = _manifest()
    commands = {command["name"]: command for command in manifest["commands"]}

    assert set(commands) == SOURCE_TRUST_ACTION_SURFACE
    assert "indbase.intent_draft" not in commands
    assert "indbase.ask" not in commands
    assert "indbase.search_natural_language" not in commands

    for command in commands.values():
        properties = command["args_schema"]["properties"]
        assert "natural_language" not in properties
        assert "intent" not in properties
        assert "raw_text" not in properties


def test_v0323d_manifest_exposes_only_low_risk_prefill_fields() -> None:
    manifest = _manifest()
    commands = {command["name"]: command for command in manifest["commands"]}

    search_properties = commands["indbase.search_sources"]["args_schema"]["properties"]
    assert {"vault_path", "query", "category", "tag", "top_k"}.issubset(
        set(search_properties)
    )
    assert search_properties["query"]["type"] == "string"
    assert search_properties["category"]["type"] == "string"
    assert search_properties["tag"]["type"] == "string"

    for command_name, id_field in OBJECT_SHOW_ID_FIELDS.items():
        properties = commands[command_name]["args_schema"]["properties"]
        assert set(properties) == {"vault_path", id_field}
        assert properties[id_field]["type"] == "string"


def test_v0323d_indbase_core_does_not_import_consoler_sdk_or_intent_drafting() -> None:
    forbidden = ("consoler_agent_sdk", "draftIntent", "intent_draft")
    core_files = Path("src/indbase_core").rglob("*.py")

    findings: list[str] = []
    for path in core_files:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                findings.append(f"{path}:{token}")

    assert findings == []
