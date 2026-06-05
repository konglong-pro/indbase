#!/usr/bin/env python3
"""v0.3.2.3b consoler read-only artifact views gate."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

PHASE = "v0.3.2.3b"
REQUIRED_ARTIFACT_KINDS = [
    "indbase.ingest_run",
    "indbase.document",
    "indbase.review_item",
    "indbase.task",
    "indbase.error",
    "indbase.doctor_report",
]


def main() -> int:
    hard_gates: dict[str, Any] = {
        "uv_run_available": False,
        "local_path_pollution": _local_path_pollution_count(),
        "manifest_schema_failures": 0,
        "missing_artifact_kinds": 0,
        "list_artifact_violations": 0,
        "focused_artifact_failures": 0,
        "artifact_metadata_vault_path_missing": 0,
        "artifact_view_failures": 0,
        "nested_artifact_blocks": 0,
        "invalid_uri_error_failures": 0,
        "doctor_artifact_failures": 0,
        "doctor_persistence_violations": 0,
        "doctor_mutation_count": 0,
    }
    failures: list[str] = []
    warnings: list[str] = []

    hard_gates["uv_run_available"] = _uv_run_available()
    if not hard_gates["uv_run_available"]:
        failures.append("uv run python -c print check failed")
    if hard_gates["local_path_pollution"]:
        failures.append("pyproject.toml or uv.lock contains a local consoler path")

    try:
        _ensure_consoler_sdk()
        from consoler_agent_sdk import CancelFlag, EventEmitter
        from indbase_agent.adapter import IndbaseAgentAdapter

        adapter = IndbaseAgentAdapter()
        manifest = adapter.load_manifest()
        hard_gates["manifest_schema_failures"] = _manifest_schema_failures(manifest)
        hard_gates["missing_artifact_kinds"] = _missing_artifact_kinds(manifest)

        with tempfile.TemporaryDirectory(prefix="indbase-v0323b-", ignore_cleanup_errors=True) as temp_dir:
            seeded = _seed_operational_vault(Path(temp_dir))
            metrics = _run_readonly_view_checks(
                adapter,
                seeded,
                CancelFlag=CancelFlag,
                EventEmitter=EventEmitter,
            )
            hard_gates.update(metrics)
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


def _seed_operational_vault(temp_root: Path) -> dict[str, Any]:
    from indbase_core.db import connect
    from indbase_core.errors import record_error
    from indbase_core.indexer import rebuild_fts_index
    from indbase_core.paths import vault_paths
    from indbase_core.reviews import create_review_item
    from indbase_core.tasks import add_task_event, create_task, finish_task
    from indbase_core.vault import init_vault

    vault = temp_root / "readonly-views-vault"
    init_vault(vault)
    paths = vault_paths(vault)
    doc_id = "doc_20250101_a0323b"
    revision_id = "rev_doc_20250101_a0323b_0001"
    markdown_path = paths.source_markdown_path(doc_id, "readonly-view-demo", 1)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    body = "# Readonly View Demo\n\n" + ("trusted consoler readonly view source " * 240)
    markdown_path.write_text(
        "---\n"
        "schema_version: indbase.source.v1\n"
        "type: source_document\n"
        f"doc_id: {doc_id}\n"
        f"revision_id: {revision_id}\n"
        "title: Readonly View Demo\n"
        "---\n\n"
        f"{body}\n",
        encoding="utf-8",
    )
    rel = paths.relative_to_vault(markdown_path)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        connection.execute(
            """
            INSERT INTO documents (
              doc_id, title, filename_slug, status, ingest_status, current_revision_id,
              canonical_path, fts_status, created_at, updated_at
            ) VALUES (?, 'Readonly View Demo', 'readonly-view-demo', 'active', 'revisioned',
                      ?, ?, 'indexed', '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')
            """,
            (doc_id, revision_id, rel),
        )
        connection.execute(
            """
            INSERT INTO document_revisions (
              revision_id, doc_id, sequence, markdown_path, content_hash,
              converter_name, converter_version, promotion_status, created_at, updated_at
            ) VALUES (?, ?, 1, ?, 'hash', 'gate', 'gate', 'promoted',
                      '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')
            """,
            (revision_id, doc_id, rel),
        )
        for sequence in range(1, 13):
            connection.execute(
                """
                INSERT INTO chunks (
                  chunk_id, doc_id, revision_id, sequence, heading_path_json, text,
                  token_count, content_hash, is_current, created_at, updated_at
                ) VALUES (?, ?, ?, ?, '[]', ?, 10, ?, 1,
                          '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')
                """,
                (
                    f"chk_v0323b_{sequence:03d}",
                    doc_id,
                    revision_id,
                    sequence,
                    ("trusted consoler readonly view chunk " * 20) + str(sequence),
                    f"chunk_hash_{sequence}",
                ),
            )
        review_id = create_review_item(
            connection,
            review_type="unsupported_source",
            target_type="document",
            target_id=doc_id,
            reason="read-only view inspection",
        )
        task_id = create_task(connection, "readonly_view_gate", {"doc_id": doc_id})
        for index in range(60):
            add_task_event(connection, task_id, "progress", f"gate event {index}")
        finish_task(connection, task_id, "succeeded", result_data={"doc_id": doc_id})
        error_id = record_error(
            connection,
            component="readonly_view_gate",
            error_type="GateError",
            message="readonly view gate error " * 300,
            developer_message="developer diagnostics " * 300,
            task_id=task_id,
        )
        connection.commit()
        rebuild_fts_index(connection, vault)

    return {
        "vault": vault,
        "doc_id": doc_id,
        "revision_id": revision_id,
        "review_id": review_id,
        "task_id": task_id,
        "error_id": error_id,
    }


def _run_readonly_view_checks(
    adapter: Any,
    seeded: dict[str, Any],
    *,
    CancelFlag: Any,
    EventEmitter: Any,
) -> dict[str, int]:
    vault = Path(seeded["vault"])
    metrics = {
        "list_artifact_violations": 0,
        "focused_artifact_failures": 0,
        "artifact_metadata_vault_path_missing": 0,
        "artifact_view_failures": 0,
        "nested_artifact_blocks": 0,
        "invalid_uri_error_failures": 0,
        "doctor_artifact_failures": 0,
        "doctor_persistence_violations": 0,
        "doctor_mutation_count": 0,
    }

    show_commands = [
        ("indbase.doc_show", {"vault_path": str(vault), "doc_id": seeded["doc_id"]}, "indbase.document", "Document JSON"),
        (
            "indbase.review_show",
            {"vault_path": str(vault), "review_id": seeded["review_id"]},
            "indbase.review_item",
            "Review item JSON",
        ),
        ("indbase.task_show", {"vault_path": str(vault), "task_id": seeded["task_id"]}, "indbase.task", "Task view JSON"),
        (
            "indbase.error_show",
            {"vault_path": str(vault), "error_id": seeded["error_id"]},
            "indbase.error",
            "Error view JSON",
        ),
        ("indbase.doctor", {"vault_path": str(vault)}, "indbase.doctor_report", "Doctor report JSON"),
    ]
    for command, args, kind, json_title in show_commands:
        before_counts = _state_counts(vault) if command == "indbase.doctor" else {}
        result = _execute(adapter, command, args, CancelFlag=CancelFlag, EventEmitter=EventEmitter)
        artifacts = _artifact_blocks(result)
        if len(artifacts) != 1 or artifacts[0].get("content", {}).get("kind") != kind:
            metrics["focused_artifact_failures"] += 1
            continue
        artifact = artifacts[0].get("content") or {}
        metadata = dict(artifact.get("metadata") or {})
        if metadata.get("vault_path") != vault.as_posix():
            metrics["artifact_metadata_vault_path_missing"] += 1
        view = adapter.get_artifact_view(
            artifact_uri=_artifact_uri(artifact),
            kind=str(artifact["kind"]),
            block_id=str(artifacts[0].get("block_id") or "blk_v0323b"),
            action_id="act_v0323b_artifact_view",
            metadata=metadata,
        )
        payload = _block_content(view, json_title)
        if (
            payload.get("view_semantics") != "current_vault_state"
            or not isinstance(payload.get("limits"), dict)
            or not isinstance(payload.get("truncated"), bool)
            or view.get("truncated") != payload.get("truncated")
        ):
            metrics["artifact_view_failures"] += 1
        if any(block.get("type") == "artifact" for block in view.get("blocks", [])):
            metrics["nested_artifact_blocks"] += 1
        if command == "indbase.doctor":
            if _artifact_uri(artifact) != "indbase://doctor-reports/current":
                metrics["doctor_artifact_failures"] += 1
            doctor_report = payload.get("doctor_report") or {}
            findings = doctor_report.get("findings") or []
            if doctor_report.get("persistence") != "ephemeral_diagnostic" or len(findings) > 50:
                metrics["doctor_persistence_violations"] += 1
            after_counts = _state_counts(vault)
            metrics["doctor_mutation_count"] += sum(
                abs(int(after_counts[key]) - int(before_counts[key])) for key in before_counts
            )

    list_commands = [
        ("indbase.review_list", {"vault_path": str(vault), "status": "all"}),
        ("indbase.task_list", {"vault_path": str(vault)}),
        ("indbase.error_list", {"vault_path": str(vault)}),
    ]
    for command, args in list_commands:
        result = _execute(adapter, command, args, CancelFlag=CancelFlag, EventEmitter=EventEmitter)
        metrics["list_artifact_violations"] += len(_artifact_blocks(result))

    metrics["invalid_uri_error_failures"] = _invalid_uri_error_failures(adapter, vault, seeded)
    return metrics


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


def _invalid_uri_error_failures(adapter: Any, vault: Path, seeded: dict[str, Any]) -> int:
    from consoler_agent_sdk import AgentError

    cases = [
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
    failures = 0
    for artifact_uri, kind, expected_code in cases:
        try:
            adapter.get_artifact_view(
                artifact_uri=artifact_uri,
                kind=kind,
                block_id="blk_v0323b",
                action_id="act_v0323b",
                metadata={"vault_path": vault.as_posix()},
            )
        except AgentError as exc:
            if exc.code != expected_code:
                failures += 1
        else:
            failures += 1
    try:
        adapter.get_artifact_view(
            artifact_uri=f"indbase://documents/{seeded['doc_id']}",
            kind="indbase.document",
            block_id="blk_v0323b",
            action_id="act_v0323b",
            metadata={},
        )
    except AgentError as exc:
        if exc.code != "vault_not_initialized":
            failures += 1
    else:
        failures += 1
    return failures


def _state_counts(vault: Path) -> dict[str, int]:
    from indbase_core.db import connect

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        return {
            "tasks": int(connection.execute("SELECT COUNT(*) AS count FROM tasks").fetchone()["count"]),
            "task_events": int(connection.execute("SELECT COUNT(*) AS count FROM task_events").fetchone()["count"]),
            "errors": int(connection.execute("SELECT COUNT(*) AS count FROM errors").fetchone()["count"]),
            "review_items": int(connection.execute("SELECT COUNT(*) AS count FROM review_items").fetchone()["count"]),
        }


def _artifact_blocks(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [block for block in result.get("blocks", []) if block.get("type") == "artifact"]


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
    raise RuntimeError("Artifact block did not include a URI")


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
    return failures


def _missing_artifact_kinds(manifest: dict[str, Any]) -> int:
    capability = manifest.get("artifact_retrieval") or {}
    declared = set(capability.get("kinds") or [])
    return len([kind for kind in REQUIRED_ARTIFACT_KINDS if kind not in declared])


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
    for key in (
        "manifest_schema_failures",
        "missing_artifact_kinds",
        "list_artifact_violations",
        "focused_artifact_failures",
        "artifact_metadata_vault_path_missing",
        "artifact_view_failures",
        "nested_artifact_blocks",
        "invalid_uri_error_failures",
        "doctor_artifact_failures",
        "doctor_persistence_violations",
        "doctor_mutation_count",
    ):
        if hard_gates[key] != 0:
            failures.append(f"{key} must be zero")


if __name__ == "__main__":
    raise SystemExit(main())
