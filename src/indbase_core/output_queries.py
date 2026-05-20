"""Read-only queries for output runs and artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from pathlib import Path

from indbase_core.paths import vault_paths


@dataclass(frozen=True)
class OutputArtifactView:
    output_artifact_id: str
    format: str
    path: str | None
    sha256: str | None
    status: str


@dataclass(frozen=True)
class OutputRunView:
    output_run_id: str
    task_id: str | None
    mode: str
    input_kind: str
    input_id: str | None
    input_path: str | None
    source_doc_id: str | None
    source_revision_id: str | None
    created_revision_id: str | None
    contract_version: str | None
    transition_commit: str | None
    config_hash: str | None
    status: str
    input_stale: bool
    input_archived: bool
    evidence_manifest_path: str | None
    evidence_trace_path: str | None
    created_at: str
    finished_at: str | None
    artifacts: tuple[OutputArtifactView, ...]


def list_output_runs(
    connection: sqlite3.Connection,
    *,
    status: str | None = None,
    input_kind: str | None = None,
    source_doc_id: str | None = None,
    limit: int = 50,
) -> list[sqlite3.Row]:
    clauses = ["deleted_at IS NULL"]
    params: list[object] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if input_kind:
        clauses.append("input_kind = ?")
        params.append(input_kind)
    if source_doc_id:
        clauses.append("source_doc_id = ?")
        params.append(source_doc_id)
    params.append(limit)
    where = " AND ".join(clauses)
    return list(
        connection.execute(
            f"""
            SELECT output_run_id, task_id, mode, input_kind, input_id, source_doc_id,
                   source_revision_id, status, created_at, finished_at
            FROM output_runs
            WHERE {where}
            ORDER BY created_at DESC, output_run_id DESC
            LIMIT ?
            """,
            tuple(params),
        )
    )


def get_output_run(connection: sqlite3.Connection, output_run_id: str) -> OutputRunView | None:
    row = connection.execute(
        """
        SELECT output_run_id, task_id, mode, input_kind, input_id, input_path,
               source_doc_id, source_revision_id, created_revision_id,
               contract_version, transition_commit, config_hash, status,
               input_stale, input_archived, evidence_manifest_path, evidence_trace_path,
               created_at, finished_at
        FROM output_runs
        WHERE output_run_id = ?
          AND deleted_at IS NULL
        """,
        (output_run_id,),
    ).fetchone()
    if row is None:
        return None
    artifacts = tuple(
        OutputArtifactView(
            output_artifact_id=str(item["output_artifact_id"]),
            format=str(item["format"]),
            path=str(item["path"]) if item["path"] else None,
            sha256=str(item["sha256"]) if item["sha256"] else None,
            status=str(item["status"]),
        )
        for item in connection.execute(
            """
            SELECT output_artifact_id, format, path, sha256, status
            FROM output_artifacts
            WHERE output_run_id = ?
              AND deleted_at IS NULL
            ORDER BY format, output_artifact_id
            """,
            (output_run_id,),
        )
    )
    return OutputRunView(
        output_run_id=str(row["output_run_id"]),
        task_id=str(row["task_id"]) if row["task_id"] else None,
        mode=str(row["mode"]),
        input_kind=str(row["input_kind"]),
        input_id=str(row["input_id"]) if row["input_id"] else None,
        input_path=str(row["input_path"]) if row["input_path"] else None,
        source_doc_id=str(row["source_doc_id"]) if row["source_doc_id"] else None,
        source_revision_id=str(row["source_revision_id"]) if row["source_revision_id"] else None,
        created_revision_id=str(row["created_revision_id"]) if row["created_revision_id"] else None,
        contract_version=str(row["contract_version"]) if row["contract_version"] else None,
        transition_commit=str(row["transition_commit"]) if row["transition_commit"] else None,
        config_hash=str(row["config_hash"]) if row["config_hash"] else None,
        status=str(row["status"]),
        input_stale=bool(row["input_stale"]),
        input_archived=bool(row["input_archived"]),
        evidence_manifest_path=str(row["evidence_manifest_path"]) if row["evidence_manifest_path"] else None,
        evidence_trace_path=str(row["evidence_trace_path"]) if row["evidence_trace_path"] else None,
        created_at=str(row["created_at"]),
        finished_at=str(row["finished_at"]) if row["finished_at"] else None,
        artifacts=artifacts,
    )


def resolve_output_artifact_path(
    connection: sqlite3.Connection,
    vault_path: Path | str,
    output_run_id: str,
    *,
    format_name: str,
) -> Path:
    row = connection.execute(
        """
        SELECT path, status
        FROM output_artifacts
        WHERE output_run_id = ?
          AND format = ?
          AND deleted_at IS NULL
        """,
        (output_run_id, format_name),
    ).fetchone()
    if row is None or not row["path"] or row["status"] != "succeeded":
        raise ValueError(f"Output artifact not available: {output_run_id} ({format_name})")
    target = vault_paths(vault_path).root / str(row["path"])
    if not target.is_file():
        raise ValueError(f"Output artifact file is missing: {target}")
    return target.resolve()
