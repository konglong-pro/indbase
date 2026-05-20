"""Transition-backed output export and normalize workflows."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Callable

from indbase_core.chunker import chunk_revision, strip_frontmatter
from indbase_core.config import IndbaseConfig, load_config
from indbase_core.conversion import hash_markdown
from indbase_core.db import connect
from indbase_core.errors import record_error
from indbase_core.ids import new_prefixed_id, revision_id
from indbase_core.indexer import reindex_document_fts
from indbase_core.output_evidence import archive_partial_evidence, archive_transition_evidence
from indbase_core.output_locators import record_normalize_locator_mappings
from indbase_core.paths import vault_paths
from indbase_core.protected_spans import spans_for_export_markdown, spans_for_normalize_body, validate_protected_spans
from indbase_core.revisions import _render_source_markdown, _write_immutable_file
from indbase_core.tasks import add_task_event, create_task, finish_task, start_task
from indbase_core.time import utc_now_iso
from indbase_core.transition_adapter import (
    TRANSITION_PIN_COMMIT,
    BridgeInvocation,
    BridgeRunContext,
    TransitionBridgeError,
    build_request,
    invoke_transition_bridge,
    run_fake_bridge,
)
from indbase_core.transition_config import config_hash, load_transition_config
from indbase_core.transition_contract import BridgeResponse, CONTRACT_VERSION
from indbase_core.transition_runtime import TransitionRuntimeError, ensure_runtime_ready

BridgeRunner = Callable[..., BridgeResponse]


@dataclass(frozen=True)
class OutputRunResult:
    output_run_id: str
    task_id: str
    status: str
    export_dir: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class NormalizeResult:
    output_run_id: str
    task_id: str
    status: str
    doc_id: str
    created_revision_id: str | None = None
    promotion_status: str | None = None


def export_source_revision(
    connection: sqlite3.Connection,
    vault_path: Path | str,
    *,
    doc_id: str,
    revision_id_value: str | None = None,
    targets: tuple[str, ...] = (),
    bridge_runner: BridgeRunner | None = None,
) -> OutputRunResult:
    config = _load_vault_config(vault_path)
    ensure_runtime_ready(config)
    document = _load_document(connection, doc_id)
    current_revision_id = str(document["current_revision_id"] or "")
    selected_revision_id = revision_id_value or current_revision_id
    if not selected_revision_id:
        raise ValueError(f"Document has no current revision: {doc_id}")
    revision = _load_revision(connection, selected_revision_id)
    if str(revision["doc_id"]) != doc_id:
        raise ValueError(f"Revision {selected_revision_id} does not belong to document {doc_id}")
    markdown_path = vault_paths(vault_path).root / str(revision["markdown_path"])
    markdown = markdown_path.read_text(encoding="utf-8")
    warnings: list[str] = []
    input_stale = int(selected_revision_id != current_revision_id)
    input_archived = int(str(document["status"]) == "archived")
    if input_stale:
        warnings.append("input_revision_stale")
    if input_archived:
        warnings.append("input_archived")
    return _run_export(
        connection,
        vault_path,
        config=config,
        mode="export",
        input_kind="source_revision",
        input_id=doc_id,
        input_path=str(revision["markdown_path"]),
        source_doc_id=doc_id,
        source_revision_id=selected_revision_id,
        markdown=markdown,
        targets=targets,
        bridge_runner=bridge_runner,
        input_stale=bool(input_stale),
        input_archived=bool(input_archived),
        output_sources=_source_revision_bindings(connection, doc_id, selected_revision_id),
        warnings=tuple(warnings),
    )


def export_translation(
    connection: sqlite3.Connection,
    vault_path: Path | str,
    *,
    translation_id: str,
    targets: tuple[str, ...] = (),
    bridge_runner: BridgeRunner | None = None,
) -> OutputRunResult:
    config = _load_vault_config(vault_path)
    ensure_runtime_ready(config)
    row = connection.execute(
        """
        SELECT translation_id, source_doc_id, source_revision_id, source_chunk_ids_json,
               output_path, status
        FROM translations
        WHERE translation_id = ?
          AND deleted_at IS NULL
        """,
        (translation_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Translation not found: {translation_id}")
    if row["status"] != "succeeded" or not row["output_path"]:
        raise ValueError(f"Translation is not exportable: {translation_id}")
    paths = vault_paths(vault_path)
    markdown_path = paths.root / str(row["output_path"])
    if not markdown_path.is_file():
        raise ValueError(f"Translation output file is missing: {translation_id}")
    if not str(row["output_path"]).startswith("outputs/translations/"):
        raise ValueError(f"Translation output_path is outside outputs/translations: {translation_id}")
    markdown = markdown_path.read_text(encoding="utf-8")
    chunk_ids = json.loads(row["source_chunk_ids_json"] or "[]")
    bindings = [
        (
            str(row["source_doc_id"]),
            str(row["source_revision_id"]),
            str(chunk_id),
            "exact",
        )
        for chunk_id in chunk_ids
    ]
    return _run_export(
        connection,
        vault_path,
        config=config,
        mode="export",
        input_kind="translation",
        input_id=translation_id,
        input_path=str(row["output_path"]),
        source_doc_id=str(row["source_doc_id"]),
        source_revision_id=str(row["source_revision_id"]),
        markdown=markdown,
        targets=targets,
        bridge_runner=bridge_runner,
        output_sources=bindings,
    )


def export_accepted_note(
    connection: sqlite3.Connection,
    vault_path: Path | str,
    *,
    candidate_card_id: str,
    targets: tuple[str, ...] = (),
    bridge_runner: BridgeRunner | None = None,
) -> OutputRunResult:
    config = _load_vault_config(vault_path)
    ensure_runtime_ready(config)
    row = connection.execute(
        """
        SELECT candidate_card_id, source_doc_id, source_revision_id, status, accepted_note_path
        FROM candidate_cards
        WHERE candidate_card_id = ?
          AND deleted_at IS NULL
        """,
        (candidate_card_id,),
    ).fetchone()
    if row is None or row["status"] != "accepted" or not row["accepted_note_path"]:
        raise ValueError(f"Accepted candidate card not found: {candidate_card_id}")
    paths = vault_paths(vault_path)
    note_path = paths.root / str(row["accepted_note_path"])
    if not note_path.is_file() or not str(row["accepted_note_path"]).startswith("notes/atomic/"):
        raise ValueError(f"Accepted note path is invalid: {candidate_card_id}")
    markdown = note_path.read_text(encoding="utf-8")
    sources = connection.execute(
        """
        SELECT source_doc_id, source_revision_id, source_chunk_id
        FROM candidate_card_sources
        WHERE candidate_card_id = ?
          AND deleted_at IS NULL
        """,
        (candidate_card_id,),
    ).fetchall()
    bindings = [
        (
            str(source["source_doc_id"]),
            str(source["source_revision_id"]),
            str(source["source_chunk_id"]),
            "exact",
        )
        for source in sources
    ]
    return _run_export(
        connection,
        vault_path,
        config=config,
        mode="export",
        input_kind="accepted_atomic_note",
        input_id=candidate_card_id,
        input_path=str(row["accepted_note_path"]),
        source_doc_id=str(row["source_doc_id"]),
        source_revision_id=str(row["source_revision_id"]),
        markdown=markdown,
        targets=targets,
        bridge_runner=bridge_runner,
        output_sources=bindings,
    )


def normalize_replace_current(
    connection: sqlite3.Connection,
    vault_path: Path | str,
    *,
    doc_id: str,
    bridge_runner: BridgeRunner | None = None,
) -> NormalizeResult:
    config = _load_vault_config(vault_path)
    ensure_runtime_ready(config)
    document = _load_document(connection, doc_id)
    if document["status"] == "archived":
        raise ValueError(f"Archived documents cannot be normalized: {doc_id}")
    if document["current_revision_id"] is None:
        raise ValueError(f"Document has no current revision: {doc_id}")
    if document["ingest_status"] == "source_shell":
        raise ValueError(f"Source shell documents cannot be normalized: {doc_id}")
    if _normalize_in_progress(connection, doc_id):
        raise ValueError(f"Normalize already running for document: {doc_id}")
    current_revision_id = str(document["current_revision_id"])
    revision = _load_revision(connection, current_revision_id)
    paths = vault_paths(vault_path)
    markdown = (paths.root / str(revision["markdown_path"])).read_text(encoding="utf-8")
    body, _offset = strip_frontmatter(markdown)
    protected = spans_for_normalize_body(body)
    task_id = create_task(
        connection,
        "doc_normalize_replace_current",
        input_data={"doc_id": doc_id, "revision_id": current_revision_id},
    )
    output_run_id = new_prefixed_id("outrun")
    start_task(connection, task_id)
    _insert_output_run(
        connection,
        output_run_id=output_run_id,
        task_id=task_id,
        mode="source_normalize_replace_current",
        input_kind="source_revision",
        input_id=doc_id,
        input_path=str(revision["markdown_path"]),
        source_doc_id=doc_id,
        source_revision_id=current_revision_id,
        status="running",
    )
    transition_config = load_transition_config(paths.root / config.output.config_path)
    invocation: BridgeInvocation | None = None
    try:
        invocation = _invoke_bridge(
            config,
            paths,
            mode="source_normalize_replace_current",
            input_kind="source_document",
            markdown=body,
            protected_spans=protected,
            targets=(),
            config_hash_value=config_hash(transition_config),
            bridge_runner=bridge_runner,
            output_run_id=output_run_id,
        )
        response = invocation.response
        if response is None:
            _persist_failure_evidence(
                connection,
                paths,
                output_run_id,
                transition_config,
                body,
                invocation=invocation,
            )
            raise TransitionBridgeError(invocation.error or "transition_normalize_failed")
        if response.status == "failed":
            _persist_failure_evidence(
                connection,
                paths,
                output_run_id,
                transition_config,
                body,
                invocation=invocation,
            )
            raise TransitionBridgeError("; ".join(response.errors) or "transition_normalize_failed")
        protected_specs = protected
        errors = validate_protected_spans(body, response.normalized_markdown, protected_specs)
        if errors:
            _persist_failure_evidence(
                connection,
                paths,
                output_run_id,
                transition_config,
                body,
                invocation=invocation,
            )
            raise TransitionBridgeError("; ".join(errors))
        _persist_bridge_evidence(
            connection,
            paths,
            output_run_id,
            response,
            transition_config,
            input_markdown=body,
        )
        sequence = int(revision["sequence"]) + 1
        new_revision_id = revision_id(doc_id, sequence)
        written = _write_normalized_revision(
            connection,
            paths,
            document=document,
            revision=revision,
            new_revision_id=new_revision_id,
            sequence=sequence,
            normalized_body=response.normalized_markdown,
            output_run_id=output_run_id,
            parent_revision_id=current_revision_id,
        )
        now = utc_now_iso()
        connection.execute(
            "UPDATE chunks SET is_current = 0, updated_at = ? WHERE doc_id = ? AND is_current = 1",
            (now, doc_id),
        )
        connection.execute(
            """
            UPDATE document_revisions
            SET promotion_status = 'superseded', updated_at = ?
            WHERE doc_id = ? AND revision_id = ?
            """,
            (now, doc_id, current_revision_id),
        )
        connection.execute(
            """
            UPDATE document_revisions
            SET promotion_status = 'promoted', updated_at = ?
            WHERE revision_id = ?
            """,
            (now, new_revision_id),
        )
        connection.execute(
            """
            UPDATE documents
            SET current_revision_id = ?, canonical_path = ?, updated_at = ?
            WHERE doc_id = ?
            """,
            (new_revision_id, written.markdown_path, now, doc_id),
        )
        chunk_revision(connection, paths.root, new_revision_id)
        record_normalize_locator_mappings(
            connection,
            output_run_id=output_run_id,
            doc_id=doc_id,
            parent_revision_id=current_revision_id,
            new_revision_id=new_revision_id,
        )
        reindex_document_fts(connection, paths.root, doc_id)
        if config.features.embedding:
            _mark_doc_embeddings_stale(connection, doc_id)
        _finish_output_run(
            connection,
            output_run_id,
            status="succeeded",
            normalized_hash=response.normalized_hash,
            created_revision_id=new_revision_id,
        )
        finish_task(
            connection,
            task_id,
            "succeeded",
            result_data={"output_run_id": output_run_id, "revision_id": new_revision_id},
        )
        connection.commit()
        return NormalizeResult(
            output_run_id=output_run_id,
            task_id=task_id,
            status="succeeded",
            doc_id=doc_id,
            created_revision_id=new_revision_id,
            promotion_status="promoted",
        )
    except Exception as exc:
        try:
            _persist_failure_evidence(
                connection,
                paths,
                output_run_id,
                transition_config,
                body,
                invocation=invocation,
            )
        except Exception:
            pass
        created_revision_id = connection.execute(
            "SELECT created_revision_id FROM output_runs WHERE output_run_id = ?",
            (output_run_id,),
        ).fetchone()
        created_id = (
            str(created_revision_id["created_revision_id"])
            if created_revision_id and created_revision_id["created_revision_id"]
            else None
        )
        if created_id:
            connection.execute(
                """
                UPDATE document_revisions
                SET promotion_status = 'never_promoted', updated_at = ?
                WHERE revision_id = ?
                """,
                (utc_now_iso(), created_id),
            )
        _finish_output_run(connection, output_run_id, status="failed")
        record_error(
            connection,
            component="transition_output",
            error_type=type(exc).__name__,
            message=str(exc),
            task_id=task_id,
            severity="error",
        )
        finish_task(connection, task_id, "failed", error_data={"message": str(exc), "doc_id": doc_id})
        connection.commit()
        raise


def _run_export(
    connection: sqlite3.Connection,
    vault_path: Path | str,
    *,
    config: IndbaseConfig,
    mode: str,
    input_kind: str,
    input_id: str,
    input_path: str,
    source_doc_id: str | None,
    source_revision_id: str | None,
    markdown: str,
    targets: tuple[str, ...],
    bridge_runner: BridgeRunner | None,
    output_sources: list[tuple[str, str, str, str]],
    input_stale: bool = False,
    input_archived: bool = False,
    warnings: tuple[str, ...] = (),
) -> OutputRunResult:
    paths = vault_paths(vault_path)
    clean_targets = _normalize_targets(targets)
    task_id = create_task(
        connection,
        "output_export",
        input_data={
            "input_kind": input_kind,
            "input_id": input_id,
            "targets": list(clean_targets),
        },
    )
    output_run_id = new_prefixed_id("outrun")
    start_task(connection, task_id)
    _insert_output_run(
        connection,
        output_run_id=output_run_id,
        task_id=task_id,
        mode=mode,
        input_kind=input_kind,
        input_id=input_id,
        input_path=input_path,
        source_doc_id=source_doc_id,
        source_revision_id=source_revision_id,
        status="running",
        input_stale=input_stale,
        input_archived=input_archived,
    )
    transition_config = load_transition_config(paths.root / config.output.config_path)
    invocation: BridgeInvocation | None = None
    try:
        protected = spans_for_export_markdown(markdown, input_kind=input_kind)
        invocation = _invoke_bridge(
            config,
            paths,
            mode=mode,
            input_kind=input_kind,
            markdown=markdown,
            protected_spans=protected,
            targets=clean_targets,
            config_hash_value=config_hash(transition_config),
            bridge_runner=bridge_runner,
            output_run_id=output_run_id,
        )
        response = invocation.response
        if response is None:
            _persist_failure_evidence(
                connection,
                paths,
                output_run_id,
                transition_config,
                markdown,
                invocation=invocation,
            )
            raise TransitionBridgeError(invocation.error or "transition_export_failed")
        validation_errors = validate_protected_spans(markdown, response.normalized_markdown, protected)
        if validation_errors or response.status == "failed":
            _persist_failure_evidence(
                connection,
                paths,
                output_run_id,
                transition_config,
                markdown,
                invocation=invocation,
            )
            raise TransitionBridgeError(
                "; ".join(validation_errors or list(response.errors) or ["normalized_export_failed"])
            )
        _persist_bridge_evidence(
            connection,
            paths,
            output_run_id,
            response,
            transition_config,
            input_markdown=markdown,
        )
        export_dir = paths.output_run_export_dir(output_run_id)
        export_dir.mkdir(parents=True, exist_ok=True)
        normalized_path = export_dir / "normalized.md"
        normalized_path.write_text(response.normalized_markdown, encoding="utf-8")
        _record_artifact(
            connection,
            output_run_id,
            format_name="md",
            rel_path=paths.relative_to_vault(normalized_path),
            sha256=hash_markdown(response.normalized_markdown),
            status="succeeded",
        )
        for target in clean_targets:
            result = next((item for item in response.targets if item.format == target), None)
            status = result.status if result else "failed"
            rel_path = None
            sha = None
            if status == "succeeded" and result and result.path:
                rel_path = result.path
                sha = hash_markdown((paths.root / rel_path).read_text(encoding="utf-8"))
            _record_artifact(
                connection,
                output_run_id,
                format_name=target,
                rel_path=rel_path,
                sha256=sha,
                status=status,
                error=result.error if result else "target_missing",
            )
        final_status = _export_status(response, clean_targets)
        _write_output_sources(connection, output_run_id, output_sources)
        _finish_output_run(
            connection,
            output_run_id,
            status=final_status,
            normalized_hash=response.normalized_hash,
        )
        finish_task(
            connection,
            task_id,
            "succeeded" if final_status in {"succeeded", "partial"} else "failed",
            result_data={"output_run_id": output_run_id, "status": final_status},
        )
        connection.commit()
        return OutputRunResult(
            output_run_id=output_run_id,
            task_id=task_id,
            status=final_status,
            export_dir=paths.relative_to_vault(export_dir),
            warnings=warnings,
        )
    except Exception as exc:
        try:
            _persist_failure_evidence(
                connection,
                paths,
                output_run_id,
                transition_config,
                markdown,
                invocation=invocation,
            )
        except Exception:
            pass
        _finish_output_run(connection, output_run_id, status="failed")
        record_error(
            connection,
            component="transition_output",
            error_type=type(exc).__name__,
            message=str(exc),
            task_id=task_id,
            severity="error",
        )
        finish_task(connection, task_id, "failed", error_data={"message": str(exc)})
        connection.commit()
        raise


def _invoke_bridge(
    config: IndbaseConfig,
    paths,
    *,
    mode: str,
    input_kind: str,
    markdown: str,
    protected_spans,
    targets: tuple[str, ...],
    config_hash_value: str,
    bridge_runner: BridgeRunner | None,
    output_run_id: str,
) -> BridgeInvocation:
    request = build_request(
        mode=mode,
        input_kind=input_kind,
        markdown=markdown,
        protected_spans=protected_spans,
        targets=targets,
        config_hash=config_hash_value,
        cache_dir=paths.transition_cache / "jobs" / output_run_id,
    )
    context = BridgeRunContext(
        vault_root=paths.root,
        runtime_dir=paths.transition_runtime,
        bridge_path=paths.root / config.output.bridge_path,
        cache_dir=paths.transition_cache / "jobs" / output_run_id,
        timeout_seconds=config.output.bridge_timeout_seconds,
    )
    return invoke_transition_bridge(request, context, runner=bridge_runner)


def _persist_failure_evidence(
    connection: sqlite3.Connection,
    paths,
    output_run_id: str,
    transition_config: dict[str, object],
    input_markdown: str,
    *,
    invocation: BridgeInvocation | None,
) -> None:
    if invocation is None:
        return
    archived = archive_partial_evidence(
        paths,
        output_run_id,
        transition_config,
        input_markdown,
        response=invocation.response,
        job_dir=invocation.job_dir,
    )
    if archived is None:
        return
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE output_runs
        SET evidence_manifest_path = COALESCE(?, evidence_manifest_path),
            evidence_trace_path = COALESCE(?, evidence_trace_path),
            config_hash = COALESCE(?, config_hash),
            input_hash = COALESCE(?, input_hash),
            updated_at = ?
        WHERE output_run_id = ?
        """,
        (
            archived.manifest_path,
            archived.trace_path,
            config_hash(transition_config),
            hashlib.sha256(input_markdown.encode("utf-8")).hexdigest(),
            now,
            output_run_id,
        ),
    )


def _write_normalized_revision(
    connection: sqlite3.Connection,
    paths,
    *,
    document: sqlite3.Row,
    revision: sqlite3.Row,
    new_revision_id: str,
    sequence: int,
    normalized_body: str,
    output_run_id: str,
    parent_revision_id: str,
):
    doc_id = str(document["doc_id"])
    markdown_path = paths.source_markdown_path(doc_id, str(document["filename_slug"]), sequence)
    rendered = _render_source_markdown(
        row=document,
        revision_id_value=new_revision_id,
        canonical_path=paths.relative_to_vault(markdown_path),
        content_hash=hash_markdown(normalized_body),
        body=normalized_body,
        ingested_at=utc_now_iso(),
    )
    body_hash = hash_markdown(normalized_body)
    _write_immutable_file(markdown_path, rendered)
    rel_path = paths.relative_to_vault(markdown_path)
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO document_revisions (
          revision_id, doc_id, sequence, markdown_path, content_hash,
          converter_name, converter_version, chunk_strategy, text_length,
          chunk_count, parent_revision_id, derived_from_output_run_id,
          derivation_kind, derivation_tool, promotion_status,
          created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, 'never_promoted', ?, ?)
        """,
        (
            new_revision_id,
            doc_id,
            sequence,
            rel_path,
            body_hash,
            "transition",
            TRANSITION_PIN_COMMIT,
            "markdown_heading_v1",
            len(normalized_body),
            parent_revision_id,
            output_run_id,
            "transition_normalize",
            "transition",
            now,
            now,
        ),
    )
    connection.execute(
        "UPDATE output_runs SET created_revision_id = ?, updated_at = ? WHERE output_run_id = ?",
        (new_revision_id, now, output_run_id),
    )
    return type("Written", (), {"markdown_path": rel_path, "revision_id": new_revision_id})()


def _insert_output_run(
    connection: sqlite3.Connection,
    *,
    output_run_id: str,
    task_id: str,
    mode: str,
    input_kind: str,
    input_id: str,
    input_path: str | None = None,
    source_doc_id: str | None = None,
    source_revision_id: str | None = None,
    status: str,
    input_stale: bool = False,
    input_archived: bool = False,
) -> None:
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO output_runs (
          output_run_id, task_id, mode, input_kind, input_id, input_path,
          source_doc_id, source_revision_id, contract_version, transition_commit,
          input_stale, input_archived, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            output_run_id,
            task_id,
            mode,
            input_kind,
            input_id,
            input_path,
            source_doc_id,
            source_revision_id,
            CONTRACT_VERSION,
            TRANSITION_PIN_COMMIT,
            int(input_stale),
            int(input_archived),
            status,
            now,
            now,
        ),
    )


def _persist_bridge_evidence(
    connection: sqlite3.Connection,
    paths,
    output_run_id: str,
    response: BridgeResponse,
    transition_config: dict[str, object],
    *,
    input_markdown: str,
) -> None:
    archived = archive_transition_evidence(
        paths,
        output_run_id,
        response,
        transition_config=transition_config,
        input_markdown=input_markdown,
    )
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE output_runs
        SET evidence_manifest_path = ?,
            evidence_trace_path = ?,
            config_hash = ?,
            input_hash = ?,
            updated_at = ?
        WHERE output_run_id = ?
        """,
        (
            archived.manifest_path,
            archived.trace_path,
            config_hash(transition_config),
            hashlib.sha256(input_markdown.encode("utf-8")).hexdigest(),
            now,
            output_run_id,
        ),
    )


def _finish_output_run(
    connection: sqlite3.Connection,
    output_run_id: str,
    *,
    status: str,
    normalized_hash: str | None = None,
    created_revision_id: str | None = None,
) -> None:
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE output_runs
        SET status = ?,
            normalized_hash = COALESCE(?, normalized_hash),
            created_revision_id = COALESCE(?, created_revision_id),
            updated_at = ?,
            finished_at = ?
        WHERE output_run_id = ?
        """,
        (status, normalized_hash, created_revision_id, now, now, output_run_id),
    )


def _record_artifact(
    connection: sqlite3.Connection,
    output_run_id: str,
    *,
    format_name: str,
    rel_path: str | None,
    sha256: str | None,
    status: str,
    error: str | None = None,
) -> None:
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO output_artifacts (
          output_artifact_id, output_run_id, format, path, sha256, status, error_json,
          created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_prefixed_id("outart"),
            output_run_id,
            format_name,
            rel_path,
            sha256,
            status,
            json.dumps({"message": error}) if error else None,
            now,
            now,
        ),
    )


def _write_output_sources(
    connection: sqlite3.Connection,
    output_run_id: str,
    bindings: list[tuple[str, str, str, str]],
) -> None:
    now = utc_now_iso()
    for doc_id, revision_id, chunk_id, confidence in bindings:
        connection.execute(
            """
            INSERT INTO output_sources (
              output_source_id, output_run_id, source_doc_id, source_revision_id,
              source_chunk_id, mapping_confidence, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_prefixed_id("outsrc"),
                output_run_id,
                doc_id,
                revision_id,
                chunk_id,
                confidence,
                now,
                now,
            ),
        )


def _export_status(response: BridgeResponse, requested_targets: tuple[str, ...]) -> str:
    if not requested_targets:
        return "succeeded" if response.status != "failed" else "failed"
    succeeded = [target for target in response.targets if target.status == "succeeded"]
    if len(succeeded) == len(requested_targets):
        return "succeeded"
    if succeeded:
        return "partial"
    return "failed"


def _normalize_targets(targets: tuple[str, ...]) -> tuple[str, ...]:
    allowed = {"html", "pdf", "docx"}
    normalized: list[str] = []
    for target in targets:
        clean = target.lower().removeprefix("--to").strip()
        if clean not in allowed:
            raise ValueError(f"Unsupported export target: {target}")
        if clean not in normalized:
            normalized.append(clean)
    return tuple(normalized)


def _load_vault_config(vault_path: Path | str) -> IndbaseConfig:
    paths = vault_paths(vault_path)
    if not paths.config_path.is_file():
        raise TransitionRuntimeError("transition_output_disabled")
    return load_config(paths.config_path)


def _load_document(connection: sqlite3.Connection, doc_id: str) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT d.doc_id, d.title, d.original_title, d.filename_slug, d.status, d.source_type,
               d.source_uri, d.normalized_source_uri, d.source_hash, d.original_path, d.language,
               d.category_id, d.quality_status, d.quality_signals_json, d.needs_review,
               d.current_revision_id, d.canonical_path, d.ingest_status,
               dr.converter_name, dr.converter_version
        FROM documents d
        LEFT JOIN document_revisions dr ON dr.revision_id = d.current_revision_id
        WHERE d.doc_id = ?
          AND d.deleted_at IS NULL
        """,
        (doc_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Document not found: {doc_id}")
    return row


def _load_revision(connection: sqlite3.Connection, revision_id_value: str) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT revision_id, doc_id, sequence, markdown_path
        FROM document_revisions
        WHERE revision_id = ?
          AND deleted_at IS NULL
        """,
        (revision_id_value,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Revision not found: {revision_id_value}")
    return row


def _normalize_in_progress(connection: sqlite3.Connection, doc_id: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM output_runs
        WHERE source_doc_id = ?
          AND mode = 'source_normalize_replace_current'
          AND status IN ('pending', 'running')
          AND deleted_at IS NULL
        LIMIT 1
        """,
        (doc_id,),
    ).fetchone()
    return row is not None


def _source_revision_bindings(
    connection: sqlite3.Connection,
    doc_id: str,
    revision_id_value: str,
) -> list[tuple[str, str, str, str]]:
    rows = connection.execute(
        """
        SELECT chunk_id
        FROM chunks
        WHERE doc_id = ?
          AND revision_id = ?
          AND deleted_at IS NULL
        ORDER BY sequence, chunk_id
        """,
        (doc_id, revision_id_value),
    ).fetchall()
    return [(doc_id, revision_id_value, str(row["chunk_id"]), "parent_only") for row in rows]


def _mark_doc_embeddings_stale(connection: sqlite3.Connection, doc_id: str) -> None:
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE embeddings
        SET status = 'stale', updated_at = ?
        WHERE doc_id = ?
          AND deleted_at IS NULL
        """,
        (now, doc_id),
    )
    connection.execute(
        """
        UPDATE documents
        SET embedding_status = 'stale', updated_at = ?
        WHERE doc_id = ?
        """,
        (now, doc_id),
    )
