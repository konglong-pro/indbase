from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from consoler_agent_sdk import (
    AgentAdapter,
    AgentError,
    ProgressHelper,
    StepHelper,
    artifact_block,
    diff_block,
    json_block,
    markdown_block,
    table_block,
)
from indbase_core.doctor import run_doctor
from indbase_core.errors import get_error, list_errors
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.paths import vault_paths
from indbase_core.reviews import get_review_item, list_review_items
from indbase_core.search import SearchOptions, governed_search_chunks
from indbase_core.search_explanations import governed_search_to_json
from indbase_core.tag_search import SearchFilterError
from indbase_core.tasks import get_task, list_task_events, list_tasks

from indbase_agent.artifact_view import (
    build_indbase_artifact_view,
    document_view_blocks,
    ingest_artifact_metadata_with_vault,
    load_document_view,
)
from indbase_agent.ingest_probe import probe_ingest_file
from indbase_agent.ingest_state_snapshot import (
    capture_ingest_state_snapshot,
    vault_state_unified_diff,
)

COMMANDS = (
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
)

WRITE_COMMANDS = frozenset({"indbase.ingest_file"})

ALLOWED_ARGS: dict[str, set[str]] = {
    "indbase.doctor": {"vault_path"},
    "indbase.ingest_file": {"vault_path", "source_path"},
    "indbase.search_sources": {"vault_path", "query", "category", "tag", "top_k"},
    "indbase.review_list": {"vault_path", "status", "type", "target_type", "limit"},
    "indbase.review_show": {"vault_path", "review_id"},
    "indbase.task_list": {"vault_path", "limit"},
    "indbase.task_show": {"vault_path", "task_id"},
    "indbase.error_list": {"vault_path", "component", "severity", "limit"},
    "indbase.error_show": {"vault_path", "error_id"},
    "indbase.doc_show": {"vault_path", "doc_id"},
}

REQUIRED_ARGS: dict[str, set[str]] = {
    "indbase.doctor": {"vault_path"},
    "indbase.ingest_file": {"vault_path", "source_path"},
    "indbase.search_sources": {"vault_path", "query"},
    "indbase.review_list": {"vault_path"},
    "indbase.review_show": {"vault_path", "review_id"},
    "indbase.task_list": {"vault_path"},
    "indbase.task_show": {"vault_path", "task_id"},
    "indbase.error_list": {"vault_path"},
    "indbase.error_show": {"vault_path", "error_id"},
    "indbase.doc_show": {"vault_path", "doc_id"},
}

INGEST_STAGES = (
    "inspect",
    "archive",
    "convert",
    "revision",
    "chunk",
    "index",
    "finalize",
)


class IndbaseAgentAdapter(AgentAdapter):
    def __init__(self) -> None:
        self._manifest_path = Path(__file__).with_name("manifest.json")

    def manifest_path(self) -> Path:
        return self._manifest_path

    def load_manifest(self) -> dict[str, Any]:
        return json.loads(self._manifest_path.read_text(encoding="utf-8"))

    def validate(self, command: str, args: dict[str, Any]) -> None:
        self._validate_command_args(command, args)
        if command == "indbase.ingest_file":
            self._validate_source_file(args)
        if command == "indbase.search_sources":
            self._validate_search_request(args)

    def plan(self, command: str, args: dict[str, Any], action_id: str) -> dict[str, Any]:
        self.validate(command, args)
        return {
            "steps": self._plan_steps(command),
            "side_effects": self._side_effects(command),
        }

    def preview(
        self,
        command: str,
        args: dict[str, Any],
        plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if command == "indbase.ingest_file":
            vault_path = Path(str(args["vault_path"])).expanduser()
            source_path = Path(str(args["source_path"])).expanduser()
            return probe_ingest_file(vault_path, source_path)
        if command in COMMANDS:
            return self._static_preview(command, args, plan)
        raise AgentError("command.not_found", f"Unknown command: {command}")

    def execute(
        self,
        command: str,
        args: dict[str, Any],
        plan: dict[str, Any],
        *,
        action_id: str,
        run_id: str,
        emitter,
        cancel_flag,
        interaction=None,
    ) -> dict[str, Any]:
        self.validate(command, args)
        if command == "indbase.doctor":
            return self._execute_doctor(
                args,
                action_id=action_id,
                emitter=emitter,
                cancel_flag=cancel_flag,
            )
        if command == "indbase.ingest_file":
            return self._execute_ingest(
                args,
                action_id=action_id,
                emitter=emitter,
                cancel_flag=cancel_flag,
                interaction=interaction,
            )
        return self._execute_readonly_command(
            command,
            args,
            action_id=action_id,
            emitter=emitter,
            cancel_flag=cancel_flag,
        )

    def get_artifact_view(
        self,
        *,
        artifact_uri: str,
        kind: str,
        block_id: str,
        action_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return build_indbase_artifact_view(
            artifact_uri=artifact_uri,
            kind=kind,
            block_id=block_id,
            action_id=action_id,
            metadata=metadata,
        )

    def _validate_command_args(self, command: str, args: dict[str, Any]) -> None:
        if command not in COMMANDS:
            raise AgentError("command.not_found", f"Unknown command: {command}")
        allowed = ALLOWED_ARGS[command]
        unknown = sorted(set(args) - allowed)
        if unknown:
            raise AgentError(
                "args.unsupported",
                f"Unsupported argument(s) for {command}: {', '.join(unknown)}",
                details={"command": command, "unsupported": unknown, "allowed": sorted(allowed)},
            )
        missing = sorted(key for key in REQUIRED_ARGS[command] if key not in args)
        if missing:
            raise AgentError(
                "args.missing",
                f"Missing required argument(s) for {command}: {', '.join(missing)}",
                details={"command": command, "missing": missing},
            )
        self._validate_vault(args)
        if command not in {"indbase.doctor", "indbase.ingest_file"}:
            self._validate_database(args)

    def _validate_vault(self, args: dict[str, Any]) -> None:
        vault_path = Path(str(args.get("vault_path", ""))).expanduser()
        if not vault_path.exists():
            raise AgentError(
                "vault.not_found",
                f"Vault path does not exist: {vault_path.as_posix()}",
                details={"vault_path": vault_path.as_posix()},
            )
        marker = vault_path / ".indbase"
        if not marker.exists():
            raise AgentError(
                "vault.invalid",
                f"Path is not an indbase vault (missing .indbase): {vault_path.as_posix()}",
                details={"vault_path": vault_path.as_posix()},
            )

    def _validate_database(self, args: dict[str, Any]) -> None:
        vault_path = Path(str(args["vault_path"])).expanduser()
        db_path = vault_path / ".indbase" / "db.sqlite"
        if not db_path.is_file():
            raise AgentError(
                "vault.database_missing",
                f"Vault database is missing: {db_path.as_posix()}",
                details={"vault_path": vault_path.as_posix(), "db_path": db_path.as_posix()},
            )

    def _validate_source_file(self, args: dict[str, Any]) -> None:
        source_path = Path(str(args.get("source_path", ""))).expanduser()
        if not source_path.exists():
            raise AgentError(
                "source.not_found",
                f"Source path does not exist: {source_path.as_posix()}",
                details={"source_path": source_path.as_posix()},
            )
        if not source_path.is_file():
            raise AgentError(
                "source.not_file",
                f"Source path must be a single file: {source_path.as_posix()}",
                details={"source_path": source_path.as_posix()},
            )

    def _validate_search_request(self, args: dict[str, Any]) -> None:
        self._bounded_int(args.get("top_k", 5), name="top_k", minimum=1, maximum=20)
        vault_path = Path(str(args["vault_path"])).expanduser()
        query = str(args.get("query", ""))
        category = _optional_string(args.get("category"))
        tag = _optional_string(args.get("tag"))
        from indbase_core.search_filters import build_governed_search_filters, require_valid_filters

        with _connect_readonly(vault_path) as connection:
            filters = build_governed_search_filters(
                connection,
                query,
                category_flag=category,
                tag_flag=tag,
            )
            try:
                require_valid_filters(filters)
            except SearchFilterError as exc:
                raise _search_filter_agent_error(exc) from exc
        if not filters.text_query and not filters.has_valid_filters:
            raise AgentError(
                "search.query_required",
                "query may be empty only when category or tag filter is present.",
                details={"query": query, "category": category, "tag": tag},
            )

    def _plan_steps(self, command: str) -> list[dict[str, str]]:
        if command == "indbase.doctor":
            return [
                {
                    "step_id": "validate-vault",
                    "title": "Validate vault path",
                    "description": "Confirm vault marker and layout.",
                },
                {
                    "step_id": "scan-vault",
                    "title": "Run doctor checks",
                    "description": "Read vault metadata and database integrity findings.",
                },
                {
                    "step_id": "render-report",
                    "title": "Render doctor report",
                    "description": "Convert findings into renderable blocks.",
                },
            ]
        if command == "indbase.ingest_file":
            return [
                {"step_id": stage, "title": stage.title(), "description": f"Ingest stage: {stage}"}
                for stage in INGEST_STAGES
            ]
        if command == "indbase.search_sources":
            return [
                {
                    "step_id": "resolve-filters",
                    "title": "Resolve filters",
                    "description": "Normalize trusted category/tag filters.",
                },
                {
                    "step_id": "search-sources",
                    "title": "Search sources",
                    "description": "Search trusted current source chunks.",
                },
                {
                    "step_id": "render-results",
                    "title": "Render results",
                    "description": "Render snippets, explanations, and document artifacts.",
                },
            ]
        return [
            {
                "step_id": "read-vault",
                "title": "Read vault state",
                "description": "Read the requested vault metadata.",
            },
            {
                "step_id": "render-result",
                "title": "Render result",
                "description": "Render bounded read-only blocks.",
            },
        ]

    def _side_effects(self, command: str) -> list[str]:
        if command == "indbase.ingest_file":
            return [
                "read_source_file",
                "write_vault_database",
                "write_vault_files",
                "read_vault_config",
            ]
        if command == "indbase.doctor":
            return ["read_vault_files", "read_vault_database", "read_vault_config"]
        if command == "indbase.doc_show":
            return ["read_vault_files", "read_vault_database"]
        return ["read_vault_database"]

    def _static_preview(
        self,
        command: str,
        args: dict[str, Any],
        plan: dict[str, Any] | None,
    ) -> dict[str, Any]:
        steps = (
            [step["title"] for step in plan.get("steps", [])]
            if plan and plan.get("steps")
            else [step["title"] for step in self._plan_steps(command)]
        )
        return {
            "preview_kind": "static",
            "summary": f"Static preview for {command}",
            "details": {
                "reads_vault": False,
                "vault_path": str(args.get("vault_path", "")),
                "steps": steps,
            },
        }

    def _execute_doctor(
        self,
        args: dict[str, Any],
        *,
        action_id: str,
        emitter,
        cancel_flag,
    ) -> dict[str, Any]:
        steps = StepHelper(emitter)
        progress = ProgressHelper(emitter)
        cancel_flag.check("before-plan")
        vault_path = Path(str(args["vault_path"])).expanduser()

        def run_checks() -> dict[str, Any]:
            cancel_flag.check("before-doctor")
            progress.update(0.2, "Running doctor checks")
            report = run_doctor(vault_path)
            cancel_flag.check("after-doctor")
            return report.to_dict()

        report_dict = steps.run("scan-vault", "Run doctor checks", run_checks)
        cancel_flag.check("before-render")
        progress.update(0.9, "Rendering report")
        vault_root = vault_paths(vault_path).root
        blocks = self._doctor_blocks(report_dict, vault_root.as_posix())
        blocks.append(
            _focused_artifact_block(
                "indbase://doctor-reports/current",
                "indbase.doctor_report",
                label="current",
                vault_path=vault_root,
                source_command="indbase.doctor",
                title="Doctor report",
            )
        )
        return {"blocks": blocks, "operation_trace": self._operation_trace("indbase.doctor", action_id=action_id)}

    def _execute_ingest(
        self,
        args: dict[str, Any],
        *,
        action_id: str,
        emitter,
        cancel_flag,
        interaction=None,
    ) -> dict[str, Any]:
        steps = StepHelper(emitter)
        progress = ProgressHelper(emitter)
        vault_path = Path(str(args["vault_path"])).expanduser()
        source_path = Path(str(args["source_path"])).expanduser()
        probe = probe_ingest_file(vault_path, source_path)
        duplicates = probe.get("duplicates", {})
        if duplicates.get("is_duplicate"):
            if interaction is None:
                raise AgentError(
                    "interaction.required",
                    "Duplicate source detected; interaction helper is required to choose skip or continue",
                    details={"duplicates": duplicates},
                )
            choice = self._request_duplicate_choice(
                interaction,
                action_id=action_id,
                probe=probe,
                vault_path=vault_path,
                source_path=source_path,
            )
            if choice == "skip":
                cancel_flag.check("duplicate-skip")
                progress.update(1.0, "Skipped duplicate ingest")
                return {
                    "blocks": self._ingest_skipped_blocks(
                        probe,
                        vault_path.as_posix(),
                        source_path.as_posix(),
                    ),
                    "operation_trace": self._operation_trace(
                        "indbase.ingest_file",
                        action_id=action_id,
                    ),
                }
            if choice != "continue":
                raise AgentError(
                    "interaction.invalid",
                    f"Unexpected duplicate choice: {choice!r}",
                    details={"expected": ["skip", "continue"]},
                )

        before_snapshot = capture_ingest_state_snapshot(vault_path, source_path)

        def run_pipeline():
            cancel_flag.check("before-ingest-pipeline")
            return run_m3_ingest_pipeline(
                vault_path,
                source_path,
                recursive=False,
                checkpoint=cancel_flag.check,
            )

        result = steps.run("ingest-pipeline", "Run ingest pipeline", run_pipeline)
        cancel_flag.check("before-render")
        progress.update(0.98, "Rendering ingest result")
        after_snapshot = capture_ingest_state_snapshot(
            vault_path, source_path, ingest_id=result.ingest_id
        )
        blocks = self._ingest_blocks(
            result,
            vault_path.as_posix(),
            source_path.as_posix(),
            before_snapshot=before_snapshot,
            after_snapshot=after_snapshot,
        )
        return {
            "blocks": blocks,
            "operation_trace": self._operation_trace(
                "indbase.ingest_file",
                action_id=action_id,
                vault_path=vault_path,
                task_id=result.task_id,
                ingest_id=result.ingest_id,
            ),
        }

    def _execute_readonly_command(
        self,
        command: str,
        args: dict[str, Any],
        *,
        action_id: str,
        emitter,
        cancel_flag,
    ) -> dict[str, Any]:
        steps = StepHelper(emitter)
        progress = ProgressHelper(emitter)

        def run_read() -> list[dict[str, Any]]:
            cancel_flag.check(f"before-{command}")
            progress.update(0.4, "Reading vault state")
            blocks = self._readonly_blocks(command, args)
            cancel_flag.check(f"after-{command}")
            return blocks

        blocks = steps.run("read-vault", "Read vault state", run_read)
        progress.update(0.95, "Rendered result")
        return {
            "blocks": blocks,
            "operation_trace": self._operation_trace(
                command,
                action_id=action_id,
                vault_path=Path(str(args["vault_path"])).expanduser(),
            ),
        }

    def _readonly_blocks(self, command: str, args: dict[str, Any]) -> list[dict[str, Any]]:
        vault_path = Path(str(args["vault_path"])).expanduser()
        if command == "indbase.search_sources":
            return self._search_sources_blocks(vault_path, args)
        if command == "indbase.review_list":
            return self._review_list_blocks(vault_path, args)
        if command == "indbase.review_show":
            review_id = str(args["review_id"])
            blocks = self._review_show_blocks(vault_path, review_id)
            blocks.append(
                _focused_artifact_block(
                    f"indbase://reviews/{review_id}",
                    "indbase.review_item",
                    label=review_id,
                    vault_path=vault_path,
                    source_command=command,
                    title="Review item",
                )
            )
            return blocks
        if command == "indbase.task_list":
            return self._task_list_blocks(vault_path, args)
        if command == "indbase.task_show":
            task_id = str(args["task_id"])
            blocks = self._task_show_blocks(vault_path, task_id)
            blocks.append(
                _focused_artifact_block(
                    f"indbase://tasks/{task_id}",
                    "indbase.task",
                    label=task_id,
                    vault_path=vault_path,
                    source_command=command,
                    title="Task",
                )
            )
            return blocks
        if command == "indbase.error_list":
            return self._error_list_blocks(vault_path, args)
        if command == "indbase.error_show":
            error_id = str(args["error_id"])
            blocks = self._error_show_blocks(vault_path, error_id)
            blocks.append(
                _focused_artifact_block(
                    f"indbase://errors/{error_id}",
                    "indbase.error",
                    label=error_id,
                    vault_path=vault_path,
                    source_command=command,
                    title="Error",
                )
            )
            return blocks
        if command == "indbase.doc_show":
            doc_id = str(args["doc_id"])
            view = load_document_view(vault_path, doc_id)
            blocks = document_view_blocks(view)
            blocks.append(
                _focused_artifact_block(
                    f"indbase://documents/{doc_id}",
                    "indbase.document",
                    label=doc_id,
                    vault_path=vault_path,
                    source_command=command,
                    title="Document",
                )
            )
            return blocks
        raise AgentError("command.not_found", f"Unknown command: {command}")

    def _search_sources_blocks(
        self,
        vault_path: Path,
        args: dict[str, Any],
    ) -> list[dict[str, Any]]:
        top_k = self._bounded_int(args.get("top_k", 5), name="top_k", minimum=1, maximum=20)
        query = str(args.get("query", ""))
        category = _optional_string(args.get("category"))
        tag = _optional_string(args.get("tag"))
        try:
            with _connect_readonly(vault_path) as connection:
                governed = governed_search_chunks(
                    connection,
                    query,
                    category=category,
                    tag=tag,
                    options=SearchOptions(
                        top_k=top_k,
                        log_queries=False,
                        persist_search_results=False,
                        mode="fts",
                    ),
                )
                payload = governed_search_to_json(governed)
        except SearchFilterError as exc:
            raise _search_filter_agent_error(exc) from exc

        results = payload["results"]
        summary = (
            f"# Source search\n\n"
            f"Vault: `{vault_path.as_posix()}`\n\n"
            f"Query: `{payload['normalized_query']}`\n\n"
            f"Results: **{payload['result_count']}**"
        )
        rows = [
            [
                result["rank"],
                result["doc_id"],
                result["revision_id"],
                result["chunk_id"],
                result["match_source"],
                _trim(str(result["snippet"]), 180),
            ]
            for result in results
        ]
        blocks: list[dict[str, Any]] = [
            markdown_block(summary, title="Search summary"),
            table_block(
                ["rank", "doc_id", "revision_id", "chunk_id", "match_source", "snippet"],
                rows,
                title="Trusted source snippets",
            ),
            json_block(payload, title="Search JSON"),
        ]
        blocks.extend(_document_artifact_blocks(results, vault_path, source_command="indbase.search_sources"))
        return blocks

    def _review_list_blocks(self, vault_path: Path, args: dict[str, Any]) -> list[dict[str, Any]]:
        status_arg = str(args.get("status", "pending"))
        status = None if status_arg == "all" else status_arg
        limit = self._bounded_int(args.get("limit", 20), name="limit", minimum=1, maximum=100)
        with _connect_readonly(vault_path) as connection:
            rows = list_review_items(
                connection,
                status=status,
                review_type=_optional_string(args.get("type")),
                target_type=_optional_string(args.get("target_type")),
                limit=limit,
            )
        items = [_row_dict(row) for row in rows]
        return [
            markdown_block(f"# Review items\n\nItems: **{len(items)}**", title="Review summary"),
            table_block(
                ["review_id", "type", "target", "status", "priority", "created_at"],
                [
                    [
                        item["review_id"],
                        item["type"],
                        f"{item['target_type']}:{item['target_id']}",
                        item["status"],
                        item["priority"],
                        item["created_at"],
                    ]
                    for item in items
                ],
                title="Review items",
            ),
            json_block({"review_items": items}, title="Review JSON"),
        ]

    def _review_show_blocks(self, vault_path: Path, review_id: str) -> list[dict[str, Any]]:
        with _connect_readonly(vault_path) as connection:
            row = get_review_item(connection, review_id)
        if row is None:
            raise AgentError(
                "review.not_found",
                f"Review item not found: {review_id}",
                details={"review_id": review_id},
            )
        item = _row_dict(row)
        return [
            markdown_block(f"# Review `{review_id}`\n\nStatus: **{item['status']}**", title="Review"),
            table_block(["field", "value"], _field_rows(item), title="Review fields"),
            json_block(item, title="Review JSON"),
        ]

    def _task_list_blocks(self, vault_path: Path, args: dict[str, Any]) -> list[dict[str, Any]]:
        limit = self._bounded_int(args.get("limit", 20), name="limit", minimum=1, maximum=100)
        with _connect_readonly(vault_path) as connection:
            rows = list_tasks(connection, limit=limit)
        items = [_row_dict(row) for row in rows]
        return [
            markdown_block(f"# Tasks\n\nItems: **{len(items)}**", title="Task summary"),
            table_block(
                ["task_id", "type", "status", "created_at", "finished_at"],
                [
                    [item["task_id"], item["type"], item["status"], item["created_at"], item["finished_at"]]
                    for item in items
                ],
                title="Tasks",
            ),
            json_block({"tasks": items}, title="Task JSON"),
        ]

    def _task_show_blocks(self, vault_path: Path, task_id: str) -> list[dict[str, Any]]:
        with _connect_readonly(vault_path) as connection:
            task = get_task(connection, task_id)
            events = list_task_events(connection, task_id) if task is not None else []
        if task is None:
            raise AgentError(
                "task.not_found",
                f"Task not found: {task_id}",
                details={"task_id": task_id},
            )
        task_payload = _row_dict(task)
        event_payload = [_row_dict(row) for row in events]
        return [
            markdown_block(f"# Task `{task_id}`\n\nStatus: **{task_payload['status']}**", title="Task"),
            table_block(["field", "value"], _field_rows(task_payload), title="Task fields"),
            table_block(
                ["created_at", "event_type", "message"],
                [[row["created_at"], row["event_type"], row["message"]] for row in event_payload],
                title="Task events",
            ),
            json_block({"task": task_payload, "events": event_payload}, title="Task JSON"),
        ]

    def _error_list_blocks(self, vault_path: Path, args: dict[str, Any]) -> list[dict[str, Any]]:
        limit = self._bounded_int(args.get("limit", 20), name="limit", minimum=1, maximum=100)
        with _connect_readonly(vault_path) as connection:
            rows = list_errors(
                connection,
                component=_optional_string(args.get("component")),
                severity=_optional_string(args.get("severity")),
                limit=limit,
            )
        items = [_row_dict(row) for row in rows]
        return [
            markdown_block(f"# Errors\n\nItems: **{len(items)}**", title="Error summary"),
            table_block(
                ["error_id", "component", "error_type", "severity", "retryable", "created_at"],
                [
                    [
                        item["error_id"],
                        item["component"],
                        item["error_type"],
                        item["severity"],
                        bool(item["retryable"]),
                        item["created_at"],
                    ]
                    for item in items
                ],
                title="Errors",
            ),
            json_block({"errors": items}, title="Error JSON"),
        ]

    def _error_show_blocks(self, vault_path: Path, error_id: str) -> list[dict[str, Any]]:
        with _connect_readonly(vault_path) as connection:
            row = get_error(connection, error_id)
        if row is None:
            raise AgentError(
                "error.not_found",
                f"Error not found: {error_id}",
                details={"error_id": error_id},
            )
        item = _row_dict(row)
        return [
            markdown_block(f"# Error `{error_id}`\n\nSeverity: **{item['severity']}**", title="Error"),
            table_block(["field", "value"], _field_rows(item), title="Error fields"),
            json_block(item, title="Error JSON"),
        ]

    def _request_duplicate_choice(
        self,
        interaction: Any,
        *,
        action_id: str,
        probe: dict[str, Any],
        vault_path: Path,
        source_path: Path,
    ) -> str:
        duplicates = probe.get("duplicates", {})
        inspection = probe.get("inspection", {})
        summary = (
            f"Source `{source_path.name}` already exists in vault `{vault_path.name}`.\n\n"
            f"- by_source_hash: `{duplicates.get('by_source_hash') or 'none'}`\n"
            f"- by_normalized_source_uri: `{duplicates.get('by_normalized_source_uri') or 'none'}`"
        )
        response = interaction.request(
            interaction_id=f"duplicate-{action_id}",
            title="Duplicate source detected",
            message="This source matches an existing document in the vault. Choose skip or continue.",
            choices=[
                {"id": "skip", "label": "Skip ingest"},
                {"id": "continue", "label": "Continue ingest"},
            ],
            blocks=[
                markdown_block(summary, title="Duplicate summary"),
                json_block(
                    {"duplicates": duplicates, "inspection": inspection},
                    title="Duplicate probe",
                ),
            ],
        )
        if not isinstance(response, str):
            raise AgentError(
                "interaction.invalid",
                "Duplicate choice response must be a choice id string",
            )
        return response

    def _ingest_skipped_blocks(
        self,
        probe: dict[str, Any],
        vault_path: str,
        source_path: str,
    ) -> list[dict[str, Any]]:
        duplicates = probe.get("duplicates", {})
        doc_ids = [
            value
            for value in (
                duplicates.get("by_source_hash"),
                duplicates.get("by_normalized_source_uri"),
            )
            if value
        ]
        unique_doc_ids = list(dict.fromkeys(doc_ids))
        summary = (
            f"# File import skipped\n\n"
            f"Vault: `{vault_path}`\n\n"
            f"Source: `{source_path}`\n\n"
            f"Status: **skipped**\n\n"
            f"Duplicate detected; ingest pipeline was not run."
        )
        rows = [["existing_doc_id", doc_id] for doc_id in unique_doc_ids] or [["existing_doc_id", "(none)"]]
        return [
            markdown_block(summary, title="Ingest skipped"),
            table_block(["field", "doc_id"], rows, title="Existing documents"),
            json_block(
                {
                    "status": "skipped",
                    "reason": "duplicate_source",
                    "duplicates": duplicates,
                    "existing_doc_ids": unique_doc_ids,
                },
                title="Skip result",
            ),
        ]

    def _doctor_blocks(self, report: dict[str, Any], vault_path: str) -> list[dict[str, Any]]:
        findings = report.get("findings", [])
        if isinstance(findings, list):
            hard = [f for f in findings if f.get("severity") in {"error", "critical"}]
            warning = [f for f in findings if f.get("severity") == "warning"]
        else:
            hard = []
            warning = []

        exit_code = int(report.get("exit_code", 0))
        summary = (
            f"# Vault check\n\n"
            f"Vault: `{vault_path}`\n\n"
            f"Exit code: **{exit_code}**\n\n"
            f"Findings: {len(findings)} total"
            f" ({len(hard)} hard, {len(warning)} warnings)"
        )
        rows = [
            [f.get("severity", ""), f.get("code", ""), f.get("message", "")]
            for f in findings
            if isinstance(f, dict)
        ]
        return [
            markdown_block(summary, title="Doctor summary"),
            table_block(["severity", "code", "message"], rows, title="Findings"),
            json_block(report, title="Raw report"),
        ]

    def _ingest_blocks(
        self,
        result: Any,
        vault_path: str,
        source_path: str,
        *,
        before_snapshot: dict[str, Any],
        after_snapshot: dict[str, Any],
    ) -> list[dict[str, Any]]:
        summary = (
            f"# File import\n\n"
            f"Vault: `{vault_path}`\n\n"
            f"Source: `{source_path}`\n\n"
            f"Status: **{result.status}**\n\n"
            f"Ingest id: `{result.ingest_id}`"
        )
        rows = [
            ["total_items", str(result.total_items)],
            ["succeeded_items", str(result.succeeded_items)],
            ["failed_items", str(result.failed_items)],
            ["unsupported_items", str(result.unsupported_items)],
            ["duplicate_items", str(result.duplicate_items)],
            ["written_revisions", str(result.written_revisions)],
            ["indexed_documents", str(result.indexed_documents)],
            ["indexed_chunks", str(result.indexed_chunks)],
        ]
        payload = {
            "ingest_id": result.ingest_id,
            "task_id": result.task_id,
            "status": result.status,
            "total_items": result.total_items,
            "succeeded_items": result.succeeded_items,
            "failed_items": result.failed_items,
            "unsupported_items": result.unsupported_items,
            "duplicate_items": result.duplicate_items,
            "review_items_count": result.review_items_count,
            "written_revisions": result.written_revisions,
            "searchable": result.searchable,
            "chunked_documents": result.chunked_documents,
            "indexed_documents": result.indexed_documents,
            "indexed_chunks": result.indexed_chunks,
            "index_failed_documents": result.index_failed_documents,
        }
        blocks: list[dict[str, Any]] = [
            markdown_block(summary, title="Ingest summary"),
            table_block(["metric", "value"], rows, title="Counts"),
            json_block(payload, title="Raw result"),
            diff_block(
                vault_state_unified_diff(before_snapshot, after_snapshot),
                language="json",
                from_label="before ingest",
                to_label="after ingest",
                title="Vault state diff",
            ),
        ]
        blocks.extend(
            self._ingest_artifact_blocks(result, after_snapshot, Path(vault_path))
        )
        return blocks

    def _ingest_artifact_blocks(
        self,
        result: Any,
        after_snapshot: dict[str, Any],
        vault_path: Path,
    ) -> list[dict[str, Any]]:
        metadata = ingest_artifact_metadata_with_vault(result, after_snapshot, vault_path)
        blocks: list[dict[str, Any]] = [
            artifact_block(
                f"indbase://ingest_runs/{result.ingest_id}",
                "indbase.ingest_run",
                label=result.ingest_id,
                metadata=metadata,
                title="Ingest run",
            )
        ]
        document = after_snapshot.get("document")
        if isinstance(document, dict) and document.get("doc_id"):
            doc_id = str(document["doc_id"])
            blocks.append(
                artifact_block(
                    f"indbase://documents/{doc_id}",
                    "indbase.document",
                    label=doc_id,
                    metadata=metadata,
                    title="Document",
                )
            )
        return blocks

    def _operation_trace(
        self,
        command: str,
        *,
        action_id: str | None,
        vault_path: Path | None = None,
        task_id: str | None = None,
        ingest_id: str | None = None,
    ) -> dict[str, Any]:
        trace: dict[str, Any] = {
            "command": command,
            "action_id": action_id,
            "task_id": task_id,
            "ingest_id": ingest_id,
            "provider_runs": [],
        }
        if vault_path is None or task_id is None:
            return trace
        try:
            with _connect_readonly(vault_path) as connection:
                rows = connection.execute(
                    """
                    SELECT provider_run_id, operation_id, provider_id, provider_version,
                           capability_id, transport_profile, provider_job_id,
                           provider_status, evidence_status
                    FROM provider_runs
                    WHERE task_id = ?
                    ORDER BY created_at, provider_run_id
                    """,
                    (task_id,),
                ).fetchall()
        except Exception:
            return trace
        trace["provider_runs"] = [
            {
                "provider_run_id": row["provider_run_id"],
                "operation_id": row["operation_id"],
                "provider": {
                    "provider_id": row["provider_id"],
                    "provider_version": row["provider_version"],
                    "capability_id": row["capability_id"],
                    "profile": row["transport_profile"],
                    "provider_job_id": row["provider_job_id"],
                    "provider_status": row["provider_status"],
                    "evidence_copied": row["evidence_status"] == "copied",
                },
                "artifacts": {
                    "provider_run": f"indbase://provider_runs/{row['provider_run_id']}",
                    "evidence": f"indbase://provider_runs/{row['provider_run_id']}/evidence",
                },
            }
            for row in rows
        ]
        return trace

    def _bounded_int(self, value: object, *, name: str, minimum: int, maximum: int) -> int:
        if isinstance(value, bool):
            raise AgentError(
                "args.invalid",
                f"{name} must be an integer.",
                details={"argument": name, "value": value},
            )
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise AgentError(
                "args.invalid",
                f"{name} must be an integer.",
                details={"argument": name, "value": value},
            ) from exc
        if parsed < minimum or parsed > maximum:
            raise AgentError(
                "args.invalid",
                f"{name} must be between {minimum} and {maximum}.",
                details={"argument": name, "minimum": minimum, "maximum": maximum, "value": parsed},
            )
        return parsed


def _connect_readonly(vault_path: Path) -> sqlite3.Connection:
    db_path = vault_path / ".indbase" / "db.sqlite"
    if not db_path.is_file():
        raise AgentError(
            "vault.database_missing",
            f"Vault database is missing: {db_path.as_posix()}",
            details={"vault_path": vault_path.as_posix(), "db_path": db_path.as_posix()},
        )
    uri = f"{db_path.resolve(strict=False).as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    clean = " ".join(str(value).strip().split())
    return clean or None


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _field_rows(item: dict[str, Any]) -> list[list[Any]]:
    return [[key, _jsonish(value)] for key, value in item.items()]


def _jsonish(value: Any) -> Any:
    if isinstance(value, str) and value and value[0] in "[{":
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _trim(text: str, max_chars: int) -> str:
    clean = " ".join(text.split())
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 3].rstrip() + "..."


def _document_artifact_blocks(
    results: list[dict[str, Any]],
    vault_path: Path,
    *,
    source_command: str,
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for result in results:
        doc_id = str(result.get("doc_id") or "")
        if not doc_id or doc_id in seen:
            continue
        seen.add(doc_id)
        blocks.append(
            artifact_block(
                f"indbase://documents/{doc_id}",
                "indbase.document",
                label=doc_id,
                metadata={
                    "vault_path": vault_path.as_posix(),
                    "doc_id": doc_id,
                    "source_command": source_command,
                },
                title="Document",
            )
        )
        if len(blocks) >= 5:
            break
    return blocks


def _focused_artifact_block(
    uri: str,
    kind: str,
    *,
    label: str,
    vault_path: Path,
    source_command: str,
    title: str,
) -> dict[str, Any]:
    return artifact_block(
        uri,
        kind,
        label=label,
        metadata={
            "vault_path": vault_path.as_posix(),
            "source_command": source_command,
        },
        title=title,
    )


def _search_filter_agent_error(exc: SearchFilterError) -> AgentError:
    return AgentError(
        "search.filter_invalid",
        exc.message,
        details={"filter_errors": [{"code": exc.code, "message": exc.message}]},
    )
