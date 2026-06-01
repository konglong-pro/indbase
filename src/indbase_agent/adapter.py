from __future__ import annotations

import json
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
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.paths import vault_paths

from indbase_agent.ingest_probe import probe_ingest_file
from indbase_agent.artifact_view import (
    build_indbase_artifact_view,
    ingest_artifact_metadata_with_vault,
)
from indbase_agent.ingest_state_snapshot import (
    capture_ingest_state_snapshot,
    vault_state_unified_diff,
)

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
        if command == "indbase.doctor":
            self._validate_vault(args)
            return
        if command == "indbase.ingest_file":
            self._validate_vault(args)
            self._validate_source_file(args)
            return
        raise AgentError("command.not_found", f"Unknown command: {command}")

    def plan(self, command: str, args: dict[str, Any], action_id: str) -> dict[str, Any]:
        self.validate(command, args)
        if command == "indbase.doctor":
            return {
                "steps": [
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
                ],
                "side_effects": [
                    "read_vault_files",
                    "read_vault_database",
                    "read_vault_config",
                ],
            }
        if command == "indbase.ingest_file":
            return {
                "steps": [
                    {"step_id": stage, "title": stage.title(), "description": f"Ingest stage: {stage}"}
                    for stage in INGEST_STAGES
                ],
                "side_effects": [
                    "read_source_file",
                    "write_vault_database",
                    "write_vault_files",
                    "read_vault_config",
                ],
            }
        raise AgentError("command.not_found", f"Unknown command: {command}")

    def preview(
        self,
        command: str,
        args: dict[str, Any],
        plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if command == "indbase.doctor":
            return self._preview_doctor(args, plan)
        if command == "indbase.ingest_file":
            vault_path = Path(str(args["vault_path"])).expanduser()
            source_path = Path(str(args["source_path"])).expanduser()
            return probe_ingest_file(vault_path, source_path)
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
            return self._execute_doctor(args, plan, emitter=emitter, cancel_flag=cancel_flag)
        if command == "indbase.ingest_file":
            return self._execute_ingest(
                args,
                plan,
                action_id=action_id,
                emitter=emitter,
                cancel_flag=cancel_flag,
                interaction=interaction,
            )
        raise AgentError("command.not_found", f"Unknown command: {command}")

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

    def _preview_doctor(
        self,
        args: dict[str, Any],
        plan: dict[str, Any] | None,
    ) -> dict[str, Any]:
        vault_path = str(args.get("vault_path", ""))
        static_steps = [
            "Validate vault path",
            "Run doctor checks",
            "Render doctor report",
        ]
        steps = (
            [step["title"] for step in plan.get("steps", [])]
            if plan and plan.get("steps")
            else static_steps
        )
        return {
            "preview_kind": "static",
            "summary": f"Static preview for vault check on {vault_path}",
            "details": {
                "reads_vault": False,
                "steps": steps,
            },
        }

    def _execute_doctor(
        self,
        args: dict[str, Any],
        plan: dict[str, Any],
        *,
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
        blocks = self._doctor_blocks(report_dict, vault_paths(vault_path).root.as_posix())
        return {"blocks": blocks}

    def _execute_ingest(
        self,
        args: dict[str, Any],
        plan: dict[str, Any],
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
                    )
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
        return {"blocks": blocks}

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
        revision = after_snapshot.get("current_revision")
        if isinstance(revision, dict) and revision.get("revision_id"):
            revision_id = str(revision["revision_id"])
            blocks.append(
                artifact_block(
                    f"indbase://document_revisions/{revision_id}",
                    "indbase.document_revision",
                    label=revision_id,
                    metadata=metadata,
                    title="Document revision",
                )
            )
        return blocks
