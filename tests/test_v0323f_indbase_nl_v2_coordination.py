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


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _manifest() -> dict:
    return json.loads(_read("src/indbase_agent/manifest.json"))


def test_v0323f_docs_route_nl_v2_to_consoler_without_expanding_indbase() -> None:
    legacy_plan_path = Path("docs/planning/v0.3.2.3f-indbase-nl-v2-intent-drafting.md")
    legacy_guide_path = Path("docs/agents/v0.3.2.3f-indbase-nl-v2-intent-drafting/AGENT.md")
    plan_path = Path("docs/planning/archive/v0.3.2.3f/indbase-nl-v2-intent-drafting.plan.md")
    guide_path = Path("docs/agents/archive/indbase/v0.3.2.3f-indbase-nl-v2-intent-drafting.md")

    assert legacy_plan_path.is_file()
    assert legacy_guide_path.is_file()
    assert plan_path.is_file()
    assert guide_path.is_file()

    assert str(plan_path).replace("\\", "/") in legacy_plan_path.read_text(encoding="utf-8")
    assert str(guide_path).replace("\\", "/") in legacy_guide_path.read_text(encoding="utf-8")

    plan = plan_path.read_text(encoding="utf-8")
    guide = guide_path.read_text(encoding="utf-8")
    agents = _read("AGENTS.md")
    context = _read("CONTEXT.md")

    for text in (plan, guide, agents):
        assert "v0.3.2.3f-indbase-nl-v2-intent-drafting" in text
        assert "E:\\consoler\\docs\\planning\\v4g-indbase-nl-v2-intent-drafting.md" in text

    assert "Indbase NL v2 Intent Drafting" in context
    assert "consoler-owned" in plan
    assert "`indbase_core` owns nothing in this phase" in plan
    assert "Do not change `indbase_core`" in guide
    assert "Do not add natural-language parsing to `indbase_agent`" in guide
    assert "Do not send raw natural language to `indbase_agent`" in guide

    for command_name in SOURCE_TRUST_ACTION_SURFACE:
        assert command_name in plan

    for forbidden in (
        "new indbase commands",
        "indbase natural-language parsing",
        "sending raw natural language to `indbase_agent`",
        "default LLM/assisted behavior",
        "`ask`",
        "generated answers",
        "follow-up suggestions",
        "multi-action workflows",
        "provider endpoints",
        "provider model names",
        "raw provider responses",
        "session-local `vault_path`",
        "action history",
        "trace records",
        "artifact blocks",
        "source snippets",
    ):
        assert forbidden in plan


def test_v0323f_manifest_remains_source_trust_only_and_raw_nl_free() -> None:
    manifest = _manifest()
    commands = {command["name"]: command for command in manifest["commands"]}

    assert set(commands) == SOURCE_TRUST_ACTION_SURFACE
    assert "indbase.intent_draft" not in commands
    assert "indbase.nl_draft" not in commands
    assert "indbase.ask" not in commands
    assert "indbase.chat" not in commands

    for command in commands.values():
        properties = command["args_schema"]["properties"]
        assert "natural_language" not in properties
        assert "raw_nl" not in properties
        assert "raw_text" not in properties
        assert "provider_response" not in properties
        assert "prompt" not in properties


def test_v0323f_indbase_core_has_no_consoler_or_assisted_intent_dependency() -> None:
    forbidden = (
        "consoler_agent_sdk",
        "draftIntent",
        "draftIntentAssisted",
        "LlmIntentProvider",
        "CONSOLER_TUI_ASSISTED_INTENT",
        "CONSOLER_INTENT_PROVIDER_URL",
        "intent_draft",
    )
    findings: list[str] = []

    for path in Path("src/indbase_core").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                findings.append(f"{path}:{token}")

    assert findings == []
