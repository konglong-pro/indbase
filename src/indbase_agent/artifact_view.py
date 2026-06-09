"""Agent-owned artifact view construction for indbase logical URIs."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from consoler_agent_sdk import AgentError, json_block, markdown_block, table_block
from indbase_core.doctor import run_doctor
from indbase_core.errors import get_error
from indbase_core.reviews import get_review_item
from indbase_core.tasks import get_task, list_task_events

VIEW_SEMANTICS = "current_vault_state"

DOCUMENT_PREVIEW_CHARS = 4000
DOCUMENT_CHUNK_ROWS = 10
DOCUMENT_CHUNK_TEXT_CHARS = 300
FORMAL_TAG_ROWS = 50
REVIEW_RELATED_ROWS = 20
TASK_EVENT_ROWS = 50
ERROR_TEXT_CHARS = 4000
DOCTOR_FINDING_ROWS = 50

ARTIFACT_VIEW_TITLES = {
    "indbase.ingest_run": "Ingest run",
    "indbase.document": "Document",
    "indbase.review_item": "Review item",
    "indbase.task": "Task",
    "indbase.error": "Error",
    "indbase.doctor_report": "Doctor report",
    "indbase.provider_run": "Provider run",
    "indbase.provider_evidence": "Provider evidence",
}

ARTIFACT_URI_KINDS = {
    "indbase.ingest_run": "ingest_runs",
    "indbase.document": "documents",
    "indbase.review_item": "reviews",
    "indbase.task": "tasks",
    "indbase.error": "errors",
    "indbase.doctor_report": "doctor-reports",
    "indbase.provider_run": "provider_runs",
    "indbase.provider_evidence": "provider_runs",
}

SCOPE_SELECTOR_MARKERS = ("*", "?", "[", "]", "{", "}", "<", ">", "|", ";")
SCOPE_SELECTOR_PREFIXES = (
    "select",
    "where",
    "from ",
    "title:",
    "path:",
    "file:",
    "glob:",
    "sql:",
)


def build_indbase_artifact_view(
    *,
    artifact_uri: str,
    kind: str,
    block_id: str,
    action_id: str,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    try:
        entity_kind, entity_id = _parse_artifact_ref(artifact_uri, kind)
        vault_path = _vault_path(metadata, require_database=kind != "indbase.doctor_report")

        if kind == "indbase.ingest_run":
            blocks = _view_ingest_run(vault_path, entity_id)
            truncated = False
        elif kind == "indbase.document":
            view = load_document_view(vault_path, entity_id, not_found_code="artifact_not_found")
            blocks = document_view_blocks(view)
            truncated = bool(view.get("truncated"))
        elif kind == "indbase.review_item":
            view = load_review_item_view(vault_path, entity_id)
            blocks = review_item_view_blocks(view)
            truncated = bool(view.get("truncated"))
        elif kind == "indbase.task":
            view = load_task_view(vault_path, entity_id)
            blocks = task_view_blocks(view)
            truncated = bool(view.get("truncated"))
        elif kind == "indbase.error":
            view = load_error_view(vault_path, entity_id)
            blocks = error_view_blocks(view)
            truncated = bool(view.get("truncated"))
        elif kind == "indbase.doctor_report":
            if entity_id != "current":
                raise AgentError(
                    "artifact_not_found",
                    f"Doctor report artifact not found: {artifact_uri}",
                    details={"artifact_uri": artifact_uri},
                )
            view = load_doctor_report_view(vault_path)
            blocks = doctor_report_view_blocks(view)
            truncated = bool(view.get("truncated"))
        elif kind == "indbase.provider_run":
            view = load_provider_run_view(vault_path, entity_id, include_evidence=False)
            blocks = provider_run_view_blocks(view)
            truncated = bool(view.get("truncated"))
        elif kind == "indbase.provider_evidence":
            view = load_provider_run_view(vault_path, entity_id, include_evidence=True)
            blocks = provider_run_view_blocks(view)
            truncated = bool(view.get("truncated"))
        else:
            raise AgentError(
                "unsupported_artifact_kind",
                f"Unsupported artifact kind: {kind}",
                details={"kind": kind},
            )
    except AgentError:
        raise
    except Exception as exc:  # noqa: BLE001 - artifact view errors need a stable public code.
        raise AgentError(
            "view_generation_failed",
            f"Failed to generate artifact view: {exc}",
            details={"artifact_uri": artifact_uri, "kind": kind, "error_type": type(exc).__name__},
        ) from exc

    return {
        "artifact_uri": artifact_uri,
        "kind": kind,
        "title": ARTIFACT_VIEW_TITLES[kind],
        "truncated": truncated,
        "metadata": {
            "action_id": action_id,
            "block_id": block_id,
            "entity_kind": entity_kind,
            "entity_id": entity_id,
            **(metadata or {}),
        },
        "blocks": blocks,
    }


def load_document_view(
    vault_path: Path,
    doc_id: str,
    *,
    not_found_code: str = "document.not_found",
) -> dict[str, Any]:
    connection = _connect_ro(vault_path)
    try:
        document = connection.execute(
            """
            SELECT d.doc_id, d.title, d.status, d.archived_at, d.current_revision_id,
                   d.source_type, d.source_uri, d.normalized_source_uri,
                   d.canonical_path, d.original_path, d.ingest_status, d.fts_status,
                   d.quality_status, d.needs_review, d.category_id,
                   d.classification_status, c.name AS category_name
            FROM documents d
            LEFT JOIN categories c ON c.category_id = d.category_id
            WHERE d.doc_id = ?
              AND d.deleted_at IS NULL
            """,
            (doc_id,),
        ).fetchone()
        if document is None:
            raise AgentError(
                not_found_code,
                f"Document not found: {doc_id}",
                details={"doc_id": doc_id},
            )

        revision = None
        revision_id = document["current_revision_id"]
        if revision_id:
            revision = connection.execute(
                """
                SELECT dr.revision_id, dr.doc_id, dr.sequence, dr.markdown_path,
                       dr.content_hash, dr.converter_name, dr.converter_version,
                       dr.text_length, dr.chunk_count, dr.created_at,
                       CASE WHEN d.current_revision_id = dr.revision_id THEN 1 ELSE 0 END AS is_current,
                       (
                         SELECT COUNT(*)
                         FROM chunks c
                         WHERE c.doc_id = dr.doc_id
                           AND c.revision_id = dr.revision_id
                           AND c.is_current = 1
                           AND c.deleted_at IS NULL
                       ) AS current_chunk_count
                FROM document_revisions dr
                JOIN documents d ON d.doc_id = dr.doc_id
                WHERE dr.revision_id = ?
                  AND dr.deleted_at IS NULL
                """,
                (revision_id,),
            ).fetchone()

        tag_rows = connection.execute(
            """
            SELECT t.tag_id, t.name, t.type, t.status AS tag_status,
                   dt.source, dt.revision_id, dt.confidence,
                   dt.status AS document_tag_status, dt.created_at
            FROM document_tags dt
            JOIN tags t ON t.tag_id = dt.tag_id
            WHERE dt.doc_id = ?
              AND dt.deleted_at IS NULL
              AND dt.status = 'active'
              AND t.deleted_at IS NULL
            ORDER BY t.name, t.tag_id
            LIMIT ?
            """,
            (doc_id, FORMAL_TAG_ROWS + 1),
        ).fetchall()

        chunk_payload: list[dict[str, Any]] = []
        chunks_truncated = False
        if revision_id:
            chunk_payload, chunks_truncated = _current_chunk_preview_rows(
                connection,
                doc_id,
                str(revision_id),
            )
    finally:
        connection.close()

    document_payload = _row_dict(document)
    revision_payload = _row_dict(revision) if revision is not None else None
    tags_truncated = len(tag_rows) > FORMAL_TAG_ROWS
    tags = [_row_dict(row) for row in tag_rows[:FORMAL_TAG_ROWS]]
    preview = _source_preview(vault_path, revision_payload)
    truncated = bool(preview.get("truncated")) or tags_truncated or chunks_truncated
    return _with_view_envelope(
        {
            "vault_path": vault_path.as_posix(),
            "document": document_payload,
            "category": {
                "category_id": document_payload.get("category_id"),
                "name": document_payload.get("category_name"),
            },
            "classification": {
                "classification_status": document_payload.get("classification_status"),
                "needs_review": bool(document_payload.get("needs_review")),
            },
            "tags": tags,
            "current_revision": revision_payload,
            "current_chunks": chunk_payload,
            "source_preview": preview,
        },
        limits={
            "source_preview_chars": DOCUMENT_PREVIEW_CHARS,
            "current_chunk_rows": DOCUMENT_CHUNK_ROWS,
            "current_chunk_text_chars": DOCUMENT_CHUNK_TEXT_CHARS,
            "formal_tag_rows": FORMAL_TAG_ROWS,
        },
        truncated=truncated,
    )


def load_review_item_view(vault_path: Path, review_id: str) -> dict[str, Any]:
    connection = _connect_ro(vault_path)
    try:
        row = get_review_item(connection, review_id)
        if row is None:
            raise AgentError(
                "artifact_not_found",
                f"Review item not found: {review_id}",
                details={"review_id": review_id},
            )
        item = _row_dict(row)
        related_rows, related_truncated = _review_related_rows(connection, item)
    finally:
        connection.close()

    return _with_view_envelope(
        {
            "vault_path": vault_path.as_posix(),
            "review_item": item,
            "related_rows": related_rows,
        },
        limits={"related_rows": REVIEW_RELATED_ROWS},
        truncated=related_truncated,
    )


def load_task_view(vault_path: Path, task_id: str) -> dict[str, Any]:
    connection = _connect_ro(vault_path)
    try:
        task = get_task(connection, task_id)
        if task is None:
            raise AgentError(
                "artifact_not_found",
                f"Task not found: {task_id}",
                details={"task_id": task_id},
            )
        events = [_row_dict(row) for row in list_task_events(connection, task_id)]
    finally:
        connection.close()

    events_truncated = len(events) > TASK_EVENT_ROWS
    return _with_view_envelope(
        {
            "vault_path": vault_path.as_posix(),
            "task": _row_dict(task),
            "events": events[:TASK_EVENT_ROWS],
        },
        limits={"task_event_rows": TASK_EVENT_ROWS},
        truncated=events_truncated,
    )


def load_error_view(vault_path: Path, error_id: str) -> dict[str, Any]:
    connection = _connect_ro(vault_path)
    try:
        row = get_error(connection, error_id)
        if row is None:
            raise AgentError(
                "artifact_not_found",
                f"Error not found: {error_id}",
                details={"error_id": error_id},
            )
    finally:
        connection.close()

    error_payload = _row_dict(row)
    fields_truncated = _truncate_error_fields(error_payload)
    return _with_view_envelope(
        {
            "vault_path": vault_path.as_posix(),
            "error": error_payload,
        },
        limits={
            "message_chars": ERROR_TEXT_CHARS,
            "user_message_chars": ERROR_TEXT_CHARS,
            "developer_message_chars": ERROR_TEXT_CHARS,
            "stack_chars": ERROR_TEXT_CHARS,
            "payload_json_chars": ERROR_TEXT_CHARS,
        },
        truncated=fields_truncated,
    )


def load_doctor_report_view(vault_path: Path) -> dict[str, Any]:
    report = run_doctor(vault_path).to_dict()
    findings = report.get("findings") if isinstance(report, dict) else []
    if not isinstance(findings, list):
        findings = []
    findings_truncated = len(findings) > DOCTOR_FINDING_ROWS
    report_payload = {
        **report,
        "findings": findings[:DOCTOR_FINDING_ROWS],
        "finding_count": len(findings),
        "persistence": "ephemeral_diagnostic",
    }
    return _with_view_envelope(
        {
            "vault_path": vault_path.as_posix(),
            "doctor_report": report_payload,
        },
        limits={"doctor_finding_rows": DOCTOR_FINDING_ROWS},
        truncated=findings_truncated,
    )


def load_provider_run_view(
    vault_path: Path,
    provider_run_id: str,
    *,
    include_evidence: bool,
) -> dict[str, Any]:
    connection = _connect_ro(vault_path)
    try:
        row = connection.execute(
            """
            SELECT provider_run_id, operation_id, action_id, task_id, ingest_run_id,
                   converter_run_id, output_run_id, provider_id, provider_package,
                   provider_version, capability_id, capability_contract_version,
                   transport_profile, provider_job_id, provider_status, evidence_status,
                   started_at, finished_at, input_sha256, manifest_artifact_ref_json,
                   trace_artifact_ref_json, evidence_root, warning_count, error_count,
                   primary_error_code, provider_error_code, provider_error_json,
                   metadata_json, created_at, updated_at
            FROM provider_runs
            WHERE provider_run_id = ?
            """,
            (provider_run_id,),
        ).fetchone()
        if row is None:
            raise AgentError(
                "artifact_not_found",
                f"Provider run not found: {provider_run_id}",
                details={"provider_run_id": provider_run_id},
            )
        errors = connection.execute(
            """
            SELECT error_id, component, error_type, severity, message, created_at
            FROM errors
            WHERE provider_run_id = ?
            ORDER BY created_at DESC
            LIMIT 20
            """,
            (provider_run_id,),
        ).fetchall()
        reviews = connection.execute(
            """
            SELECT review_id, type, target_type, target_id, status, reason, created_at
            FROM review_items
            WHERE provider_run_id = ?
            ORDER BY created_at DESC
            LIMIT 20
            """,
            (provider_run_id,),
        ).fetchall()
    finally:
        connection.close()

    provider_run = _row_dict(row)
    evidence_summary: dict[str, Any] | None = None
    truncated = False
    if include_evidence:
        evidence_summary, truncated = _provider_evidence_summary(vault_path, provider_run)
    return _with_view_envelope(
        {
            "vault_path": vault_path.as_posix(),
            "provider_run": provider_run,
            "provider_errors": [_row_dict(item) for item in errors],
            "provider_review_items": [_row_dict(item) for item in reviews],
            "evidence_summary": evidence_summary,
        },
        limits={"provider_related_rows": 20},
        truncated=truncated,
    )


def provider_run_view_blocks(view: dict[str, Any]) -> list[dict[str, Any]]:
    run = view["provider_run"]
    evidence = view.get("evidence_summary")
    blocks = [
        markdown_block(
            f"# Provider run `{run['provider_run_id']}`\n\n"
            f"Provider: **{run.get('provider_id') or ''}**\n\n"
            f"Capability: `{run.get('capability_id') or ''}`\n\n"
            f"Status: **{run.get('provider_status') or 'pending'}**",
            title="Provider run",
        ),
        table_block(["field", "value"], _field_rows(run), title="Provider run fields"),
    ]
    if evidence:
        files = evidence.get("files") if isinstance(evidence, dict) else []
        blocks.append(
            table_block(
                ["name", "size_bytes"],
                [[item.get("name", ""), item.get("size_bytes", "")] for item in files if isinstance(item, dict)],
                title="Copied evidence files",
            )
        )
    blocks.append(json_block(view, title="Provider run JSON"))
    return blocks


def document_view_blocks(view: dict[str, Any]) -> list[dict[str, Any]]:
    document = view["document"]
    revision = view.get("current_revision")
    preview = view.get("source_preview") or {}
    title = document.get("title") or document["doc_id"]
    blocks: list[dict[str, Any]] = [
        markdown_block(
            f"# Document `{document['doc_id']}`\n\n"
            f"Title: **{title}**\n\n"
            f"Status: **{document.get('status') or ''}**",
            title="Document summary",
        ),
        table_block(
            ["field", "value"],
            _field_rows(
                {
                    "doc_id": document.get("doc_id"),
                    "title": document.get("title"),
                    "status": document.get("status"),
                    "current_revision_id": document.get("current_revision_id"),
                    "canonical_path": document.get("canonical_path"),
                    "original_path": document.get("original_path"),
                    "category_id": document.get("category_id"),
                    "category_name": document.get("category_name"),
                    "classification_status": document.get("classification_status"),
                    "needs_review": bool(document.get("needs_review")),
                    "ingest_status": document.get("ingest_status"),
                    "fts_status": document.get("fts_status"),
                }
            ),
            title="Trusted metadata",
        ),
    ]
    if revision is not None:
        blocks.append(
            table_block(
                ["field", "value"],
                _field_rows(
                    {
                        "revision_id": revision.get("revision_id"),
                        "sequence": revision.get("sequence"),
                        "is_current": bool(revision.get("is_current")),
                        "markdown_path": revision.get("markdown_path"),
                        "converter_name": revision.get("converter_name"),
                        "text_length": revision.get("text_length"),
                        "chunk_count": revision.get("chunk_count"),
                        "current_chunk_count": revision.get("current_chunk_count"),
                        "content_hash": revision.get("content_hash"),
                    }
                ),
                title="Current revision",
            )
        )
    tags = view.get("tags") or []
    blocks.append(
        table_block(
            ["tag_id", "name", "tag_status", "source", "revision_id"],
            [
                [
                    tag.get("tag_id"),
                    tag.get("name"),
                    tag.get("tag_status"),
                    tag.get("source"),
                    tag.get("revision_id"),
                ]
                for tag in tags
            ],
            title="Formal tags",
        )
    )
    chunks = view.get("current_chunks") or []
    if chunks:
        blocks.append(
            table_block(
                ["sequence", "chunk_id", "text"],
                [[row.get("sequence"), row.get("chunk_id"), row.get("text")] for row in chunks],
                title="Current chunk previews",
            )
        )
    if preview.get("text"):
        suffix = "\n\n(Preview truncated.)" if preview.get("truncated") else ""
        blocks.append(
            markdown_block(
                f"# Source preview\n\n{preview['text']}{suffix}",
                title="Source preview",
            )
        )
    blocks.append(json_block(view, title="Document JSON"))
    return blocks


def review_item_view_blocks(view: dict[str, Any]) -> list[dict[str, Any]]:
    item = view["review_item"]
    related_rows = view.get("related_rows") or []
    return [
        markdown_block(
            f"# Review `{item['review_id']}`\n\nStatus: **{item['status']}**",
            title="Review item",
        ),
        table_block(["field", "value"], _field_rows(item), title="Review fields"),
        table_block(
            ["kind", "id", "summary"],
            [[row.get("kind"), row.get("id"), row.get("summary")] for row in related_rows],
            title="Related rows",
        ),
        json_block(view, title="Review item JSON"),
    ]


def task_view_blocks(view: dict[str, Any]) -> list[dict[str, Any]]:
    task = view["task"]
    events = view.get("events") or []
    return [
        markdown_block(
            f"# Task `{task['task_id']}`\n\nStatus: **{task['status']}**",
            title="Task",
        ),
        table_block(["field", "value"], _field_rows(task), title="Task fields"),
        table_block(
            ["created_at", "event_type", "message"],
            [[row.get("created_at"), row.get("event_type"), row.get("message")] for row in events],
            title="Task events",
        ),
        json_block(view, title="Task view JSON"),
    ]


def error_view_blocks(view: dict[str, Any]) -> list[dict[str, Any]]:
    item = view["error"]
    return [
        markdown_block(
            f"# Error `{item['error_id']}`\n\nSeverity: **{item.get('severity') or ''}**",
            title="Error",
        ),
        table_block(["field", "value"], _field_rows(item), title="Error fields"),
        json_block(view, title="Error view JSON"),
    ]


def doctor_report_view_blocks(view: dict[str, Any]) -> list[dict[str, Any]]:
    report = view["doctor_report"]
    findings = report.get("findings") if isinstance(report, dict) else []
    rows = [
        [finding.get("severity", ""), finding.get("code", ""), finding.get("message", "")]
        for finding in findings
        if isinstance(finding, dict)
    ]
    return [
        markdown_block(
            f"# Doctor report\n\n"
            f"Vault: `{view['vault_path']}`\n\n"
            f"Exit code: **{report.get('exit_code', 0)}**\n\n"
            f"Findings: **{report.get('finding_count', len(rows))}**",
            title="Doctor report",
        ),
        table_block(["severity", "code", "message"], rows, title="Doctor findings"),
        json_block(view, title="Doctor report JSON"),
    ]


def ingest_artifact_metadata_with_vault(
    result: Any,
    snapshot: dict[str, Any],
    vault_path: Path,
) -> dict[str, Any]:
    from indbase_agent.ingest_state_snapshot import ingest_artifact_metadata

    metadata = ingest_artifact_metadata(result, snapshot)
    metadata["vault_path"] = vault_path.as_posix()
    return metadata


def _parse_artifact_ref(artifact_uri: str, kind: str) -> tuple[str, str]:
    expected_entity = ARTIFACT_URI_KINDS.get(kind)
    if expected_entity is None:
        raise AgentError(
            "unsupported_artifact_kind",
            f"Unsupported artifact kind: {kind}",
            details={"kind": kind},
        )

    parsed = urlparse(artifact_uri)
    if parsed.scheme != "indbase" or not parsed.netloc:
        raise AgentError(
            "invalid_artifact_uri",
            f"Invalid indbase artifact URI: {artifact_uri}",
            details={"artifact_uri": artifact_uri},
        )
    if parsed.query or parsed.fragment:
        raise AgentError(
            "invalid_artifact_uri",
            "Artifact URIs must not include query strings or fragments.",
            details={"artifact_uri": artifact_uri},
        )
    if parsed.netloc != expected_entity:
        raise AgentError(
            "unsupported_artifact_kind",
            f"Artifact kind {kind} does not match URI: {artifact_uri}",
            details={"artifact_uri": artifact_uri, "kind": kind, "expected_entity": expected_entity},
        )

    raw_parts = [part for part in parsed.path.split("/") if part]
    if len(raw_parts) != 1:
        if kind != "indbase.provider_evidence" or len(raw_parts) != 2 or raw_parts[1] != "evidence":
            raise AgentError(
                "invalid_artifact_uri",
                f"Artifact URI must identify exactly one object: {artifact_uri}",
                details={"artifact_uri": artifact_uri},
            )
    raw_id = raw_parts[0]
    entity_id = unquote(raw_id)
    if not entity_id or _looks_like_scope_selector(entity_id, raw_id):
        raise AgentError(
            "artifact_scope_rejected",
            f"Artifact URI is outside the supported object scope: {artifact_uri}",
            details={"artifact_uri": artifact_uri},
        )
    return parsed.netloc, entity_id


def _looks_like_scope_selector(entity_id: str, raw_id: str) -> bool:
    if entity_id in {".", ".."}:
        return True
    if "/" in entity_id or "\\" in entity_id:
        return True
    if "%2f" in raw_id.lower() or "%5c" in raw_id.lower():
        return True
    if ":" in entity_id:
        return True
    if any(marker in entity_id for marker in SCOPE_SELECTOR_MARKERS):
        return True
    if any(char.isspace() for char in entity_id):
        return True
    lowered = entity_id.lower()
    return any(lowered.startswith(prefix) for prefix in SCOPE_SELECTOR_PREFIXES)


def _vault_path(metadata: dict[str, Any] | None, *, require_database: bool) -> Path:
    if not metadata or not metadata.get("vault_path"):
        raise AgentError(
            "vault_not_initialized",
            "vault_path is required in artifact metadata for retrieval",
        )
    vault_path = Path(str(metadata["vault_path"])).expanduser()
    if not (vault_path / ".indbase").is_dir():
        raise AgentError(
            "vault_not_initialized",
            f"Path is not an initialized indbase vault: {vault_path.as_posix()}",
            details={"vault_path": vault_path.as_posix()},
        )
    if require_database and not (vault_path / ".indbase" / "db.sqlite").is_file():
        raise AgentError(
            "vault_not_initialized",
            f"Vault database is missing: {vault_path.as_posix()}",
            details={"vault_path": vault_path.as_posix()},
        )
    return vault_path


def _connect_ro(vault_path: Path) -> sqlite3.Connection:
    db_path = vault_path / ".indbase" / "db.sqlite"
    if not db_path.is_file():
        raise AgentError(
            "vault_not_initialized",
            f"Vault database is missing: {db_path.as_posix()}",
            details={"vault_path": vault_path.as_posix(), "db_path": db_path.as_posix()},
        )
    uri = f"{db_path.resolve(strict=False).as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _view_ingest_run(vault_path: Path, ingest_id: str) -> list[dict[str, Any]]:
    connection = _connect_ro(vault_path)
    try:
        row = connection.execute(
            """
            SELECT ingest_id, task_id, source_kind, status, total_items, succeeded_items,
                   failed_items, unsupported_items, duplicate_items, review_items_count,
                   created_at, finished_at
            FROM ingest_runs
            WHERE ingest_id = ?
            """,
            (ingest_id,),
        ).fetchone()
        if row is None:
            raise AgentError("artifact_not_found", f"Ingest run not found: {ingest_id}")

        items = connection.execute(
            """
            SELECT ingest_item_id, doc_id, source_uri, normalized_source_uri, status, finished_at
            FROM ingest_items
            WHERE ingest_id = ?
            ORDER BY created_at
            """,
            (ingest_id,),
        ).fetchall()
    finally:
        connection.close()

    summary = _row_dict(row)
    item_payload = [_row_dict(item) for item in items]
    return [
        markdown_block(f"# Ingest run `{ingest_id}`", title="Ingest run"),
        table_block(
            ["field", "value"],
            _field_rows(summary),
            title="Run summary",
        ),
        table_block(
            ["ingest_item_id", "doc_id", "status", "source_uri"],
            [
                [
                    item["ingest_item_id"],
                    item["doc_id"] or "",
                    item["status"] or "",
                    item["source_uri"] or "",
                ]
                for item in item_payload
            ],
            title="Ingest items",
        ),
        json_block(
            {"ingest_run": summary, "ingest_items": item_payload},
            title="Raw ingest run",
        ),
    ]


def _current_chunk_preview_rows(
    connection: sqlite3.Connection,
    doc_id: str,
    revision_id: str,
) -> tuple[list[dict[str, Any]], bool]:
    rows = connection.execute(
        """
        SELECT chunk_id, sequence, heading_path_json, source_page, language,
               token_count, text
        FROM chunks
        WHERE doc_id = ?
          AND revision_id = ?
          AND is_current = 1
          AND deleted_at IS NULL
        ORDER BY sequence
        LIMIT ?
        """,
        (doc_id, revision_id, DOCUMENT_CHUNK_ROWS + 1),
    ).fetchall()
    row_limit_truncated = len(rows) > DOCUMENT_CHUNK_ROWS
    payload: list[dict[str, Any]] = []
    text_truncated = False
    for row in rows[:DOCUMENT_CHUNK_ROWS]:
        text, shortened = _truncate_text(str(row["text"] or ""), DOCUMENT_CHUNK_TEXT_CHARS)
        text_truncated = text_truncated or shortened
        payload.append(
            {
                "chunk_id": row["chunk_id"],
                "sequence": row["sequence"],
                "heading_path": _jsonish(row["heading_path_json"]),
                "source_page": row["source_page"],
                "language": row["language"],
                "token_count": row["token_count"],
                "text": text,
                "text_truncated": shortened,
            }
        )
    return payload, row_limit_truncated or text_truncated


def _review_related_rows(
    connection: sqlite3.Connection,
    item: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    related: list[dict[str, Any]] = []
    target_type = str(item.get("target_type") or "")
    target_id = str(item.get("target_id") or "")
    if target_type == "document":
        document = connection.execute(
            """
            SELECT doc_id, title, status, current_revision_id, ingest_status, fts_status
            FROM documents
            WHERE doc_id = ?
              AND deleted_at IS NULL
            """,
            (target_id,),
        ).fetchone()
        if document is not None:
            related.append(
                {
                    "kind": "document",
                    "id": document["doc_id"],
                    "summary": document["title"] or document["status"] or "",
                    "row": _row_dict(document),
                }
            )
    elif target_type == "task":
        task = get_task(connection, target_id)
        if task is not None:
            related.append(
                {
                    "kind": "task",
                    "id": task["task_id"],
                    "summary": task["status"],
                    "row": _row_dict(task),
                }
            )
    elif target_type == "error":
        error = get_error(connection, target_id)
        if error is not None:
            related.append(
                {
                    "kind": "error",
                    "id": error["error_id"],
                    "summary": error["message"] or error["error_type"] or "",
                    "row": _row_dict(error),
                }
            )

    if len(related) <= REVIEW_RELATED_ROWS:
        return related, False
    return related[:REVIEW_RELATED_ROWS], True


def _truncate_error_fields(error_payload: dict[str, Any]) -> bool:
    truncated = False
    for field in ("message", "user_message", "developer_message", "stack", "payload_json"):
        value = error_payload.get(field)
        if isinstance(value, str):
            error_payload[field], shortened = _truncate_text(value, ERROR_TEXT_CHARS)
            error_payload[f"{field}_truncated"] = shortened
            truncated = truncated or shortened
    return truncated


def _provider_evidence_summary(vault_path: Path, provider_run: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    evidence_root = str(provider_run.get("evidence_root") or "")
    root = vault_path / evidence_root
    if not evidence_root or not root.is_dir():
        return {"evidence_root": evidence_root, "files": [], "missing": True}, False
    files: list[dict[str, Any]] = []
    truncated = False
    for index, path in enumerate(sorted(child for child in root.rglob("*") if child.is_file())):
        if index >= 50:
            truncated = True
            break
        files.append(
            {
                "name": path.relative_to(root).as_posix(),
                "size_bytes": path.stat().st_size,
            }
        )
    evidence_index = _provider_evidence_index_summary(root / "evidence_index.json")
    return {
        "evidence_root": evidence_root,
        "files": files,
        "evidence_index": evidence_index,
    }, truncated


def _provider_evidence_index_summary(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"parse_error": "invalid_json"}
    if not isinstance(payload, dict):
        return {"parse_error": "not_object"}
    artifacts = payload.get("artifacts")
    return {
        "provider_run_id": payload.get("provider_run_id"),
        "provider": payload.get("provider"),
        "artifact_count": len(artifacts) if isinstance(artifacts, list) else 0,
        "manifest": payload.get("manifest"),
        "trace": payload.get("trace"),
    }


def _source_preview(vault_path: Path, revision: dict[str, Any] | None) -> dict[str, Any]:
    if not revision or not revision.get("markdown_path"):
        return {"text": "", "truncated": False, "status": "unavailable"}
    path = _safe_vault_path(vault_path, str(revision["markdown_path"]))
    if path is None or not path.is_file():
        return {"text": "", "truncated": False, "status": "missing"}
    try:
        markdown = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"text": "", "truncated": False, "status": "unreadable", "error": str(exc)}
    body = _strip_frontmatter(markdown).strip()
    truncated = len(body) > DOCUMENT_PREVIEW_CHARS
    return {
        "text": body[:DOCUMENT_PREVIEW_CHARS],
        "truncated": truncated,
        "status": "ok",
        "chars": min(len(body), DOCUMENT_PREVIEW_CHARS),
        "max_chars": DOCUMENT_PREVIEW_CHARS,
    }


def _safe_vault_path(vault_path: Path, relative_path: str) -> Path | None:
    root = vault_path.resolve(strict=False)
    candidate = (root / relative_path).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _strip_frontmatter(markdown: str) -> str:
    if not markdown.startswith("---\n"):
        return markdown
    end = markdown.find("\n---\n", 4)
    if end < 0:
        return markdown
    return markdown[end + len("\n---\n") :]


def _with_view_envelope(
    payload: dict[str, Any],
    *,
    limits: dict[str, Any],
    truncated: bool,
) -> dict[str, Any]:
    return {
        "view_semantics": VIEW_SEMANTICS,
        "limits": limits,
        "truncated": bool(truncated),
        **payload,
    }


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: _jsonish(row[key]) for key in row.keys()}


def _field_rows(values: dict[str, Any]) -> list[list[Any]]:
    return [[key, "" if value is None else value] for key, value in values.items()]


def _jsonish(value: Any) -> Any:
    if isinstance(value, str) and value and value[0] in "[{":
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    if max_chars <= 3:
        return text[:max_chars], True
    return text[: max_chars - 3].rstrip() + "...", True
