#!/usr/bin/env python3
"""v0.3.2.3a consoler Source Trust probe stabilization gate."""

from __future__ import annotations

import argparse
import json
import gc
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from gate_common import TRUSTED_NEEDLE, configure_v02_vault, install_deterministic_swallow_stub

PHASE = "v0.3.2.3a"
TOKEN = TRUSTED_NEEDLE


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prepare-consoler-smoke",
        type=Path,
        help="Create a persistent synthetic vault and search args JSON for consoler real-agent smoke.",
    )
    args = parser.parse_args()
    if args.prepare_consoler_smoke is not None:
        return _prepare_consoler_smoke(args.prepare_consoler_smoke)

    hard_gates: dict[str, Any] = {
        "uv_run_available": False,
        "local_path_pollution": _local_path_pollution_count(),
        "manifest_schema_failures": 0,
        "successful_ingest_revisions": 0,
        "search_hits": 0,
        "missing_document_artifacts": 1,
        "artifact_metadata_vault_path_missing": 1,
        "doc_show_failures": 1,
        "artifact_view_failures": 1,
        "legacy_conversion_retired_in_success_path": 1,
        "disabled_swallow_legacy_conversion_retired": False,
    }
    warnings: list[str] = []
    failures: list[str] = []

    hard_gates["uv_run_available"] = _uv_run_available()
    if not hard_gates["uv_run_available"]:
        failures.append("uv run python -c print check failed")
    if hard_gates["local_path_pollution"]:
        failures.append("pyproject.toml or uv.lock contains a local consoler path")

    try:
        _ensure_consoler_sdk()
        install_deterministic_swallow_stub()
        from consoler_agent_sdk import CancelFlag, EventEmitter
        from indbase_agent.adapter import IndbaseAgentAdapter

        adapter = IndbaseAgentAdapter()
        hard_gates["manifest_schema_failures"] = _manifest_schema_failures(adapter.load_manifest())
        if hard_gates["manifest_schema_failures"]:
            failures.append("manifest is missing required command/permission fields")

        with tempfile.TemporaryDirectory(prefix="indbase-v0323a-", ignore_cleanup_errors=True) as temp_dir:
            temp_root = Path(temp_dir)
            success_metrics = _run_success_path(
                temp_root,
                adapter,
                CancelFlag=CancelFlag,
                EventEmitter=EventEmitter,
            )
            hard_gates.update(success_metrics)
            disabled_visible = _run_disabled_swallow_path(
                temp_root,
                adapter,
                CancelFlag=CancelFlag,
                EventEmitter=EventEmitter,
            )
            hard_gates["disabled_swallow_legacy_conversion_retired"] = disabled_visible
            gc.collect()
    except Exception as exc:  # noqa: BLE001 - gate must summarize failures as JSON.
        failures.append(f"{type(exc).__name__}: {exc}")

    _append_hard_gate_failures(hard_gates, failures)
    status = "passed" if not failures else "failed"
    summary = {
        "phase": PHASE,
        "status": status,
        "hard_gates": hard_gates,
        "warnings": warnings,
        "failures": sorted(set(failures)),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if status == "passed" else 1


def _prepare_consoler_smoke(output_dir: Path) -> int:
    hard_gates: dict[str, Any] = {
        "uv_run_available": _uv_run_available(),
        "local_path_pollution": _local_path_pollution_count(),
        "manifest_schema_failures": 0,
        "successful_ingest_revisions": 0,
        "search_hits": 0,
        "missing_document_artifacts": 1,
        "artifact_metadata_vault_path_missing": 1,
        "doc_show_failures": 1,
        "artifact_view_failures": 1,
        "legacy_conversion_retired_in_success_path": 1,
    }
    failures: list[str] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        _ensure_consoler_sdk()
        install_deterministic_swallow_stub()
        from consoler_agent_sdk import CancelFlag, EventEmitter
        from indbase_agent.adapter import IndbaseAgentAdapter

        adapter = IndbaseAgentAdapter()
        hard_gates["manifest_schema_failures"] = _manifest_schema_failures(adapter.load_manifest())
        hard_gates.update(
            _run_success_path(
                output_dir,
                adapter,
                CancelFlag=CancelFlag,
                EventEmitter=EventEmitter,
            )
        )
        search_args_path = output_dir / "search-sources-args.json"
        search_args_path.write_text(
            json.dumps(
                {
                    "vault_path": (output_dir / "success-vault").as_posix(),
                    "query": TOKEN,
                    "top_k": 5,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001 - preparation must summarize failures as JSON.
        failures.append(f"{type(exc).__name__}: {exc}")

    _append_prepare_failures(hard_gates, failures)
    status = "passed" if not failures else "failed"
    summary = {
        "phase": PHASE,
        "status": status,
        "mode": "prepare-consoler-smoke",
        "vault_path": (output_dir / "success-vault").as_posix(),
        "search_args_path": (output_dir / "search-sources-args.json").as_posix(),
        "query": TOKEN,
        "hard_gates": hard_gates,
        "warnings": [],
        "failures": sorted(set(failures)),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if status == "passed" else 1


def _run_success_path(
    temp_root: Path,
    adapter: Any,
    *,
    CancelFlag: Any,
    EventEmitter: Any,
) -> dict[str, Any]:
    from indbase_core.db import connect
    from indbase_core.vault import init_vault

    vault = temp_root / "success-vault"
    init_vault(vault)
    configure_v02_vault(
        vault,
        swallow_ingest=True,
        transition_output=False,
        web_ingest=False,
        min_markdown_chars=1,
    )
    source = temp_root / "source-trust-probe.md"
    source.write_text(
        "# Source Trust Probe\n\n"
        f"This deterministic source contains {TOKEN}. "
        "It is synthetic, sanitized, and long enough to produce a trusted current revision. "
        "The probe must create chunks, FTS rows, a search hit, and a document artifact.\n",
        encoding="utf-8",
    )

    ingest = _execute(
        adapter,
        "indbase.ingest_file",
        {"vault_path": str(vault), "source_path": str(source)},
        CancelFlag=CancelFlag,
        EventEmitter=EventEmitter,
    )
    ingest_payload = _block_content(ingest, "Raw result")

    search = _execute(
        adapter,
        "indbase.search_sources",
        {"vault_path": str(vault), "query": TOKEN, "top_k": 5},
        CancelFlag=CancelFlag,
        EventEmitter=EventEmitter,
    )
    search_payload = _block_content(search, "Search JSON")
    results = search_payload.get("results") or []
    first_hit = results[0] if results else {}
    document_artifacts = [
        block
        for block in search.get("blocks", [])
        if block.get("type") == "artifact"
        and (block.get("content") or {}).get("kind") == "indbase.document"
    ]
    missing_vault_metadata = sum(
        1
        for block in document_artifacts
        if (block.get("content") or {}).get("metadata", {}).get("vault_path") != vault.as_posix()
    )

    doc_show_failures = 1
    artifact_view_failures = 1
    doc_id = first_hit.get("doc_id")
    if doc_id:
        doc_show = _execute(
            adapter,
            "indbase.doc_show",
            {"vault_path": str(vault), "doc_id": str(doc_id)},
            CancelFlag=CancelFlag,
            EventEmitter=EventEmitter,
        )
        doc_payload = _block_content(doc_show, "Document JSON")
        preview = doc_payload.get("source_preview") or {}
        revision = doc_payload.get("current_revision") or {}
        if (
            doc_payload.get("document", {}).get("doc_id") == doc_id
            and revision.get("revision_id") == first_hit.get("revision_id")
            and preview.get("max_chars") == 4000
            and len(str(preview.get("text") or "")) <= 4000
        ):
            doc_show_failures = 0

    if document_artifacts:
        artifact = document_artifacts[0].get("content") or {}
        view = adapter.get_artifact_view(
            artifact_uri=_artifact_uri(artifact),
            kind=str(artifact["kind"]),
            block_id=str(document_artifacts[0].get("id") or "blk_v0323a"),
            action_id="act_v0323a_artifact_view",
            metadata=dict(artifact.get("metadata") or {}),
        )
        view_payload = _block_content(view, "Document JSON")
        preview = view_payload.get("source_preview") or {}
        if (
            view_payload.get("document", {}).get("doc_id") == doc_id
            and preview.get("max_chars") == 4000
            and len(str(preview.get("text") or "")) <= 4000
            and all(block.get("type") != "artifact" for block in view.get("blocks", []))
        ):
            artifact_view_failures = 0

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        legacy_success_count = connection.execute(
            "SELECT COUNT(*) AS count FROM errors WHERE error_type = 'legacy_conversion_retired'"
        ).fetchone()["count"]
        chunk_count = connection.execute(
            "SELECT COUNT(*) AS count FROM chunks WHERE is_current = 1 AND deleted_at IS NULL"
        ).fetchone()["count"]
        fts_count = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()["count"]

    successful_revisions = int(ingest_payload.get("written_revisions") or 0)
    if int(ingest_payload.get("succeeded_items") or 0) != 1:
        successful_revisions = 0
    if int(chunk_count or 0) <= 0 or int(fts_count or 0) <= 0:
        successful_revisions = 0

    return {
        "successful_ingest_revisions": successful_revisions,
        "search_hits": int(search_payload.get("result_count") or 0),
        "missing_document_artifacts": 0 if document_artifacts else 1,
        "artifact_metadata_vault_path_missing": missing_vault_metadata,
        "doc_show_failures": doc_show_failures,
        "artifact_view_failures": artifact_view_failures,
        "legacy_conversion_retired_in_success_path": int(legacy_success_count or 0),
    }


def _run_disabled_swallow_path(
    temp_root: Path,
    adapter: Any,
    *,
    CancelFlag: Any,
    EventEmitter: Any,
) -> bool:
    from indbase_core.db import connect
    from indbase_core.vault import init_vault

    vault = temp_root / "disabled-swallow-vault"
    init_vault(vault)
    source = temp_root / "disabled-swallow.md"
    source.write_text(
        "# Disabled Swallow\n\n"
        "This fixture must fail visibly when features.swallow_ingest is false.\n",
        encoding="utf-8",
    )
    result = _execute(
        adapter,
        "indbase.ingest_file",
        {"vault_path": str(vault), "source_path": str(source)},
        CancelFlag=CancelFlag,
        EventEmitter=EventEmitter,
    )
    payload = _block_content(result, "Raw result")
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM errors WHERE error_type = 'legacy_conversion_retired'"
        ).fetchone()
    return (
        payload.get("status") == "completed_with_issues"
        and int(payload.get("written_revisions") or 0) == 0
        and int(row["count"] or 0) > 0
    )


def _execute(
    adapter: Any,
    command: str,
    args: dict[str, Any],
    *,
    CancelFlag: Any,
    EventEmitter: Any,
) -> dict[str, Any]:
    action_id = f"act_{command.replace('.', '_')}"
    run_id = f"run_{command.replace('.', '_')}"
    plan = adapter.plan(command, args, action_id)
    emitter = EventEmitter(run_id, action_id, "indbase", command, lambda _event: None)
    return adapter.execute(
        command,
        args,
        plan,
        action_id=action_id,
        run_id=run_id,
        emitter=emitter,
        cancel_flag=CancelFlag(),
    )


def _block_content(result: dict[str, Any], title: str) -> dict[str, Any]:
    for block in result.get("blocks", []):
        if block.get("title") == title:
            content = block.get("content")
            if isinstance(content, dict):
                return content
    raise RuntimeError(f"Missing result block: {title}")


def _artifact_uri(content: dict[str, Any]) -> str:
    for key in ("uri", "artifact_uri"):
        value = content.get(key)
        if value:
            return str(value)
    raise RuntimeError("Document artifact block did not include a URI")


def _ensure_consoler_sdk() -> None:
    try:
        __import__("consoler_agent_sdk")
        return
    except ModuleNotFoundError:
        pass

    sibling_sdk = REPO_ROOT.parent / "consoler" / "sdks" / "python"
    if sibling_sdk.is_dir():
        sys.path.insert(0, str(sibling_sdk))
        __import__("consoler_agent_sdk")
        return
    raise RuntimeError("consoler_agent_sdk is not importable")


def _manifest_schema_failures(manifest: dict[str, Any]) -> int:
    failures = 0
    commands = manifest.get("commands")
    if not isinstance(commands, list) or not commands:
        return 1
    for command in commands:
        if not isinstance(command, dict):
            failures += 1
            continue
        if not command.get("name"):
            failures += 1
        if not isinstance(command.get("args_schema"), dict):
            failures += 1
        permissions = command.get("permissions")
        if not isinstance(permissions, list) or not permissions:
            failures += 1
    artifact_retrieval = manifest.get("artifact_retrieval") or {}
    if "indbase.document" not in artifact_retrieval.get("kinds", []):
        failures += 1
    return failures


def _uv_run_available() -> bool:
    try:
        completed = subprocess.run(
            ["uv", "run", "python", "-c", "print('uv-ok')"],
            cwd=REPO_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except OSError:
        return False
    return completed.returncode == 0 and "uv-ok" in completed.stdout


def _local_path_pollution_count() -> int:
    drive = "E:"
    forward = drive + "/" + "consoler"
    backward = drive + "\\" + "consoler"
    patterns = (forward, backward, "file:///" + forward)
    count = 0
    for relative in ("pyproject.toml", "uv.lock"):
        path = REPO_ROOT / relative
        text = path.read_text(encoding="utf-8")
        count += sum(text.count(pattern) for pattern in patterns)
    return count


def _append_hard_gate_failures(hard_gates: dict[str, Any], failures: list[str]) -> None:
    if hard_gates["manifest_schema_failures"] != 0:
        failures.append("manifest_schema_failures must be zero")
    if hard_gates["successful_ingest_revisions"] != 1:
        failures.append("successful_ingest_revisions must be exactly one")
    if hard_gates["search_hits"] < 1:
        failures.append("search_hits must be at least one")
    for key in (
        "missing_document_artifacts",
        "artifact_metadata_vault_path_missing",
        "doc_show_failures",
        "artifact_view_failures",
        "legacy_conversion_retired_in_success_path",
    ):
        if hard_gates[key] != 0:
            failures.append(f"{key} must be zero")
    if not hard_gates["disabled_swallow_legacy_conversion_retired"]:
        failures.append("disabled swallow must still record legacy_conversion_retired")


def _append_prepare_failures(hard_gates: dict[str, Any], failures: list[str]) -> None:
    if not hard_gates["uv_run_available"]:
        failures.append("uv run python -c print check failed")
    if hard_gates["local_path_pollution"]:
        failures.append("pyproject.toml or uv.lock contains a local consoler path")
    if hard_gates["manifest_schema_failures"] != 0:
        failures.append("manifest_schema_failures must be zero")
    if hard_gates["successful_ingest_revisions"] != 1:
        failures.append("successful_ingest_revisions must be exactly one")
    if hard_gates["search_hits"] < 1:
        failures.append("search_hits must be at least one")
    for key in (
        "missing_document_artifacts",
        "artifact_metadata_vault_path_missing",
        "doc_show_failures",
        "artifact_view_failures",
        "legacy_conversion_retired_in_success_path",
    ):
        if hard_gates[key] != 0:
            failures.append(f"{key} must be zero")


if __name__ == "__main__":
    raise SystemExit(main())
