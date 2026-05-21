"""Typer entrypoint for the indbase CLI."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from typing import Iterator
from contextlib import contextmanager

import typer
from rich.console import Console
from rich.table import Table
from typer.core import TyperGroup

from indbase_core.categories import (
    add_category,
    archive_category,
    list_categories,
    restore_category,
    template_names,
    update_category,
)
from indbase_core.cards import (
    accept_candidate_card,
    generate_candidate_card,
    get_candidate_card,
    list_candidate_card_sources,
    list_candidate_cards,
    reject_candidate_card,
)
from indbase_core.classification import (
    accept_classification_suggestion,
    get_classification_suggestion,
    list_classification_suggestions,
    reject_classification_suggestion,
    suggest_classifications,
)
from indbase_core.candidate_review import (
    accept_conversion_candidate_review,
    reject_conversion_candidate_review,
)
from indbase_core.config import ConfigError, IngestConfig as CoreIngestConfig, SearchConfig, load_config
from indbase_core.db import connect
from indbase_core.documents import (
    archive_document,
    list_documents,
    list_document_revisions,
    restore_document,
    set_document_category,
)
from indbase_core.doctor import run_doctor
from indbase_core.embeddings import rebuild_vector_index
from indbase_core.errors import get_error, list_errors
from indbase_core.ingest import (
    run_m3_archive_ingest_pipeline,
    run_m3_ingest_pipeline,
    run_m3_url_ingest_pipeline,
)
from indbase_core.indexer import rebuild_fts_index
from indbase_core.ocr import list_ocr_pages, run_ocr_for_document
from indbase_core.reviews import get_review_item, list_review_items, resolve_review_item, resolve_review_items
from indbase_core.retrieval import (
    get_retrieval_run,
    list_retrieval_items,
    list_retrieval_runs,
    retrieve_chunks,
)
from indbase_core.retrieval_evaluation import (
    assess_answer_readiness,
    export_eval_cases_jsonl,
    get_eval_run,
    import_eval_cases_from_jsonl,
    list_eval_results,
    list_eval_runs,
    run_eval_suite,
)
from indbase_core.search import SearchOptions, search_chunks
from indbase_core.tags import (
    add_document_tag,
    add_tag,
    archive_tag,
    list_document_tags,
    list_tags,
    remove_document_tag,
    restore_tag,
    update_tag,
)
from indbase_core.tasks import get_task, list_task_events, list_tasks
from indbase_core.translations import (
    get_translation,
    list_translations,
    resolve_translation_output_path,
    translate_full_document,
    translate_selected_chunks,
)
from indbase_core.output_queries import get_output_run, list_output_runs, resolve_output_artifact_path
from indbase_core.output_service import (
    export_accepted_note,
    export_source_revision,
    export_translation,
    normalize_replace_current,
)
from indbase_core.category_manager import suggest_category_assignments
from indbase_core.profile import (
    build_document_profile,
    list_stale_profiles,
    show_document_profile,
)
from indbase_core.tag_candidates import (
    list_pending_tag_names,
    list_tag_candidates,
    promote_tag_candidate,
    reject_tag_candidate,
)
from indbase_core.taxonomy_janitor import run_taxonomy_audit
from indbase_core.taxonomy_manager import analyze_all_profiled_documents, analyze_document_taxonomy
from indbase_core.taxonomy_mutations import (
    add_tag_alias,
    archive_tag_mutation,
    deprecate_tag,
    merge_tags,
)
from indbase_core.taxonomy_suggestions import (
    accept_taxonomy_suggestion,
    get_taxonomy_suggestion,
    list_taxonomy_suggestions,
    reject_taxonomy_suggestion,
)
from indbase_core.transition_runtime import TransitionRuntimeError, install_runtime, runtime_status
from indbase_core.vault import init_vault
from indbase_core.version import __version__
from indbase_cli.tui_lite import run_tui_lite

app = typer.Typer(
    add_completion=False,
    help="Local-first personal knowledge database.",
    no_args_is_help=True,
)


class IngestSourceTypeGroup(TyperGroup):
    default_command_name = "path"

    def resolve_command(self, ctx, args):
        if args:
            cmd_name = args[0]
            cmd = self.get_command(ctx, cmd_name)
            if cmd is None and not cmd_name.startswith("-"):
                default = self.get_command(ctx, self.default_command_name)
                if default is not None:
                    return self.default_command_name, default, args
        return super().resolve_command(ctx, args)


LOCAL_MEDIA_EXTENSIONS = (
    "aac",
    "aiff",
    "flac",
    "m4a",
    "mkv",
    "mov",
    "mp3",
    "mp4",
    "mpeg",
    "mpga",
    "oga",
    "ogg",
    "opus",
    "wav",
    "webm",
    "wma",
)


ingest_app = typer.Typer(
    cls=IngestSourceTypeGroup,
    help="Ingest sources by explicit source type.",
    no_args_is_help=True,
)
catalog_app = typer.Typer(help="Manage manual categories.", no_args_is_help=True)
task_app = typer.Typer(help="Inspect local task records.", no_args_is_help=True)
index_app = typer.Typer(help="Inspect and rebuild local indexes.", no_args_is_help=True)
doc_app = typer.Typer(help="Inspect and manage documents.", no_args_is_help=True)
review_app = typer.Typer(help="Inspect and resolve review queue items.", no_args_is_help=True)
error_app = typer.Typer(help="Inspect recorded errors.", no_args_is_help=True)
tag_app = typer.Typer(help="Manage manual tags.", no_args_is_help=True)
ocr_app = typer.Typer(help="Run and inspect OCR records.", no_args_is_help=True)
classification_app = typer.Typer(help="Create and review classification suggestions.", no_args_is_help=True)
translation_app = typer.Typer(help="Create and inspect translation outputs.", no_args_is_help=True)
card_app = typer.Typer(help="Create and inspect candidate card records.", no_args_is_help=True)
output_app = typer.Typer(help="Transition-backed output export and runtime.", no_args_is_help=True)
output_runtime_app = typer.Typer(help="Manage per-vault transition runtime.", no_args_is_help=True)
output_export_app = typer.Typer(help="Read-only output export.", no_args_is_help=True)
profile_app = typer.Typer(help="Build and inspect document profiles.", no_args_is_help=True)
taxonomy_app = typer.Typer(help="Taxonomy analysis and governance.", no_args_is_help=True)
retrieval_app = typer.Typer(help="Inspect persisted retrieval packages.", no_args_is_help=True)
eval_app = typer.Typer(help="Deterministic evaluation workflows.", no_args_is_help=True)
eval_retrieval_app = typer.Typer(
    help="Retrieval evaluation cases, runs, and answer readiness.",
    no_args_is_help=True,
)
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"indbase {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the indbase version and exit.",
    ),
) -> None:
    """Run indbase commands."""


@app.command()
def init(
    vault_path: Path = typer.Argument(
        Path("."),
        help="Vault directory to create or initialize.",
    ),
    category_template: str = typer.Option(
        "minimal",
        "--category-template",
        help=f"Category template: {', '.join(template_names())}.",
    ),
) -> None:
    """Create a vault layout, database, config, and category template."""
    result = init_vault(vault_path, category_template=category_template)
    console.print(f"Initialized vault: {result.vault_path}")
    console.print(f"DB: {result.db_path}")
    console.print(f"Config: {result.config_path}")
    console.print(f"Task: {result.task_id}")
    if result.applied_migrations:
        console.print(f"Migrations: {', '.join(result.applied_migrations)}")
    console.print(f"Categories inserted: {result.inserted_categories}")


@ingest_app.command("path", hidden=True)
def ingest_legacy_path(
    source_input: Path = typer.Argument(..., help="File or folder to ingest."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    recursive: bool = typer.Option(
        False,
        "--recursive",
        help="Recursively scan folders.",
    ),
) -> None:
    """Compatibility alias for local file or folder ingest."""
    _run_local_ingest(vault, source_input, recursive=recursive)


@ingest_app.command("file")
def ingest_file(
    source_input: Path = typer.Argument(..., help="Local file to ingest."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
) -> None:
    """Ingest one local file through the indbase durable chain."""
    if not source_input.is_file():
        console.print(f"[red]Source is not a file:[/red] {source_input}")
        raise typer.Exit(2)
    _run_local_ingest(vault, source_input, recursive=False)


@ingest_app.command("folder")
def ingest_folder(
    source_input: Path = typer.Argument(..., help="Local folder to ingest."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    recursive: bool = typer.Option(
        False,
        "--recursive",
        help="Recursively scan folders.",
    ),
) -> None:
    """Ingest a local folder through the indbase durable chain."""
    if not source_input.is_dir():
        console.print(f"[red]Source is not a folder:[/red] {source_input}")
        raise typer.Exit(2)
    _run_local_ingest(vault, source_input, recursive=recursive)


@ingest_app.command("media")
def ingest_media(
    source_input: Path = typer.Argument(..., help="Local audio or video file to ingest through swallow ASR."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
) -> None:
    """Ingest audio or video via the swallow conversion adapter."""
    config = _load_vault_config_for_cli(vault)
    _require_feature(config, "swallow_ingest", "use swallow media conversion")
    _require_feature(config, "asr", "use ASR media ingest")
    if not source_input.is_file():
        console.print(f"[red]Source is not a file:[/red] {source_input}")
        raise typer.Exit(2)
    extension = source_input.suffix.lower().lstrip(".")
    if extension not in LOCAL_MEDIA_EXTENSIONS:
        console.print(f"[red]Source is not a supported media extension:[/red] .{extension}")
        raise typer.Exit(2)
    _run_local_ingest(vault, source_input, recursive=False, ingest_config=_media_ingest_config(config.ingest))


@ingest_app.command("url")
def ingest_url(
    url: str = typer.Argument(..., help="URL to capture and ingest."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
) -> None:
    """Capture a URL with local Playwright and ingest the resulting snapshot."""
    config = _load_vault_config_for_cli(vault)
    _require_feature(config, "swallow_ingest", "use swallow URL conversion")
    _require_feature(config, "web_ingest", "capture URLs")
    _run_url_ingest(vault, url)


@ingest_app.command("browser-capture")
def ingest_browser_capture(
    capture_path: Path = typer.Argument(..., help="Browser capture JSON to ingest."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
) -> None:
    """Validate browser-capture ingest policy before capture replay is wired."""
    config = _load_vault_config_for_cli(vault)
    _require_feature(config, "swallow_ingest", "use swallow browser-capture conversion")
    _require_feature(config, "web_ingest", "ingest browser captures")
    _require_feature(config, "login_profile_ingest", "use authenticated browser capture")
    if not capture_path.is_file():
        console.print(f"[red]Browser capture file not found:[/red] {capture_path}")
        raise typer.Exit(2)
    _fail_unwired_source_type("browser-capture", str(capture_path))


@ingest_app.command("archive")
def ingest_archive(
    archive_path: Path = typer.Argument(..., help="Archive file to expand and ingest."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
) -> None:
    """Expand an archive into logical documents through swallow."""
    config = _load_vault_config_for_cli(vault)
    _require_feature(config, "swallow_ingest", "use swallow archive conversion")
    if not archive_path.is_file():
        console.print(f"[red]Archive file not found:[/red] {archive_path}")
        raise typer.Exit(2)
    _run_archive_ingest(vault, archive_path)


@app.command()
def doctor(
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory to inspect.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit a structured JSON report.",
    ),
) -> None:
    """Check vault health without modifying it."""
    report = run_doctor(vault)
    if json_output:
        typer.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        raise typer.Exit(report.exit_code)

    table = Table(title=f"Doctor: {report.vault_path}")
    table.add_column("Severity")
    table.add_column("Code")
    table.add_column("Message")
    for finding in report.findings:
        table.add_row(finding.severity, finding.code, finding.message)
    console.print(table)
    raise typer.Exit(report.exit_code)


@app.command()
def search(
    query: str = typer.Argument(..., help="Query text to retrieve source snippets."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    top_k: int | None = typer.Option(
        None,
        "--top-k",
        min=1,
        help="Maximum number of snippets to return.",
    ),
    mode: str = typer.Option(
        "fts",
        "--mode",
        help="Search mode: fts, vector, or hybrid.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON results.",
    ),
) -> None:
    """Search current source chunks and render citation snippets."""
    options = _search_options_for_vault(vault, top_k=top_k, mode=mode)
    with _existing_vault_connection(vault) as connection:
        try:
            result = search_chunks(connection, query, options=options)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "query_id": result.query_id,
                    "query_text": result.query_text,
                    "result_count": result.result_count,
                    "results": [
                        {
                            "rank": row.rank,
                            "doc_id": row.doc_id,
                            "revision_id": row.revision_id,
                            "chunk_id": row.chunk_id,
                            "title": row.title,
                            "source_path": row.source_path,
                            "snippet": row.snippet,
                            "score": row.score,
                            "match_source": row.match_source,
                        }
                        for row in result.results
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if not result.results:
        console.print("No results")
        return

    console.print(f"Search: {result.query_text}")
    for row in result.results:
        console.print(f"{row.rank}. {row.match_source}")
        console.print(f"doc_id: {row.doc_id}")
        console.print(f"revision_id: {row.revision_id}")
        console.print(f"chunk_id: {row.chunk_id}")
        console.print(f"source_path: {row.source_path or ''}")
        console.print(f"snippet: {row.snippet}")


@app.command("retrieve")
def retrieve(
    query: str = typer.Argument(..., help="Query text for citation-ready retrieval package."),
    vault: Path = typer.Option(Path("."), "--vault", help="Vault directory."),
    top_k: int | None = typer.Option(None, "--top-k", min=1, help="Maximum package items."),
    candidate_k: int | None = typer.Option(None, "--candidate-k", min=1, help="Search candidate pool size."),
    mode: str = typer.Option("hybrid", "--mode", help="Base search mode: fts, vector, or hybrid."),
    per_doc_limit: int = typer.Option(3, "--per-doc-limit", min=1, help="Max items per document."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Build and persist a deterministic retrieval package."""
    options = _retrieval_search_options_for_vault(vault, mode=mode)
    with _existing_vault_connection(vault) as connection:
        try:
            result = retrieve_chunks(
                connection,
                query,
                top_k=top_k or options.top_k,
                candidate_k=candidate_k,
                per_doc_limit=per_doc_limit,
                mode=mode,
                search_options=options,
            )
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "retrieval_run_id": result.retrieval_run_id,
                    "query_text": result.query_text,
                    "normalized_query_text": result.normalized_query_text,
                    "linked_search_query_id": result.linked_search_query_id,
                    "status": result.status,
                    "warnings": list(result.warnings),
                    "top_k": result.top_k,
                    "candidate_k": result.candidate_k,
                    "per_doc_limit": result.per_doc_limit,
                    "base_mode": result.base_mode,
                    "result_count": result.result_count,
                    "items": [
                        {
                            "retrieval_item_id": item.retrieval_item_id,
                            "rank": item.rank,
                            "doc_id": item.doc_id,
                            "revision_id": item.revision_id,
                            "chunk_id": item.chunk_id,
                            "title": item.title,
                            "source_path": item.source_path,
                            "quote": item.quote,
                            "snippet": item.snippet,
                            "base_score": item.base_score,
                            "taxonomy_score": item.taxonomy_score,
                            "final_score": item.final_score,
                            "match_source": item.match_source,
                            "reasons": list(item.reasons),
                        }
                        for item in result.items
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise typer.Exit(0 if result.status != "failed" else 1)

    console.print(f"Retrieval run: {result.retrieval_run_id} ({result.status})")
    console.print(f"Query: {result.query_text}")
    if result.normalized_query_text != result.query_text:
        console.print(f"Normalized: {result.normalized_query_text}")
    if result.warnings:
        console.print("Warnings:")
        for warning in result.warnings:
            console.print(f"- {warning}")
    if not result.items:
        console.print("No retrieval items")
        raise typer.Exit(1)
    for item in result.items:
        console.print(f"{item.rank}. {item.match_source} score={item.final_score}")
        console.print(f"doc_id: {item.doc_id}")
        console.print(f"revision_id: {item.revision_id}")
        console.print(f"chunk_id: {item.chunk_id}")
        console.print(f"reasons: {', '.join(item.reasons)}")
        console.print(f"quote: {item.quote[:200]}")


@retrieval_app.command("list")
def retrieval_list(
    vault: Path = typer.Option(Path("."), "--vault", help="Vault directory."),
    limit: int = typer.Option(20, "--limit", min=1, help="Maximum runs to list."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """List persisted retrieval runs."""
    with _existing_vault_connection(vault) as connection:
        rows = list_retrieval_runs(connection, limit=limit)
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "runs": [
                        {
                            "retrieval_run_id": row["retrieval_run_id"],
                            "query_text": row["query_text"],
                            "normalized_query_text": row["normalized_query_text"],
                            "base_mode": row["base_mode"],
                            "result_count": row["result_count"],
                            "status": row["status"],
                            "created_at": row["created_at"],
                        }
                        for row in rows
                    ]
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if not rows:
        console.print("No retrieval runs")
        return
    table = Table(title="Retrieval Runs")
    table.add_column("Run")
    table.add_column("Status")
    table.add_column("Items")
    table.add_column("Mode")
    table.add_column("Created")
    for row in rows:
        table.add_row(
            str(row["retrieval_run_id"]),
            str(row["status"]),
            str(row["result_count"]),
            str(row["base_mode"]),
            str(row["created_at"]),
        )
    console.print(table)


@retrieval_app.command("show")
def retrieval_show(
    retrieval_run_id: str = typer.Argument(..., help="Retrieval run ID."),
    vault: Path = typer.Option(Path("."), "--vault", help="Vault directory."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Show a persisted retrieval package."""
    with _existing_vault_connection(vault) as connection:
        run = get_retrieval_run(connection, retrieval_run_id)
        if run is None:
            console.print(f"[red]Retrieval run not found: {retrieval_run_id}[/red]")
            raise typer.Exit(1)
        items = list_retrieval_items(connection, retrieval_run_id)
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "run": dict(run),
                    "items": [dict(item) for item in items],
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return
    console.print(f"Retrieval run: {run['retrieval_run_id']} ({run['status']})")
    console.print(f"Query: {run['query_text']}")
    for item in items:
        console.print(f"{item['rank']}. {item['chunk_id']} score={item['final_score']}")
        console.print(f"quote: {str(item['quote'])[:200]}")


@eval_retrieval_app.command("import")
def eval_retrieval_import(
    jsonl_path: Path = typer.Argument(..., help="JSONL file with eval cases."),
    vault: Path = typer.Option(Path("."), "--vault", help="Vault directory."),
    source: str = typer.Option("fixture", "--source", help="Case source: fixture, dogfood, manual."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Import retrieval evaluation cases from JSONL."""
    with _existing_vault_connection(vault) as connection:
        result = import_eval_cases_from_jsonl(connection, jsonl_path, source=source)
    payload = {
        "imported": result.imported,
        "updated": result.updated,
        "rejected": result.rejected,
        "errors": list(result.errors),
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        console.print(
            f"Imported {result.imported}, updated {result.updated}, rejected {result.rejected}"
        )
        for error in result.errors:
            console.print(f"- {error}")
    if result.rejected:
        raise typer.Exit(1)


@eval_retrieval_app.command("export")
def eval_retrieval_export(
    suite: str = typer.Option(..., "--suite", help="Suite name to export."),
    vault: Path = typer.Option(Path("."), "--vault", help="Vault directory."),
    output: Path | None = typer.Option(None, "--output", help="Write JSONL to this path."),
) -> None:
    """Export active retrieval evaluation cases as JSONL."""
    with _existing_vault_connection(vault) as connection:
        content = export_eval_cases_jsonl(connection, suite=suite)
    if output is not None:
        output.write_text(content, encoding="utf-8")
        console.print(f"Wrote {output}")
    else:
        typer.echo(content, nl=False)


@eval_retrieval_app.command("run")
def eval_retrieval_run(
    suite: str = typer.Option(..., "--suite", help="Suite name to execute."),
    vault: Path = typer.Option(Path("."), "--vault", help="Vault directory."),
    case: str | None = typer.Option(None, "--case", help="Run a single eval_case_id."),
    limit: int | None = typer.Option(None, "--limit", min=1, help="Maximum cases to run."),
    fail_fast: bool = typer.Option(False, "--fail-fast", help="Stop after first failed/error case."),
    mode: str = typer.Option("hybrid", "--mode", help="Default search mode when case omits mode."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Run retrieval evaluation cases and persist results."""
    options = _retrieval_search_options_for_vault(vault, mode=mode)
    with _existing_vault_connection(vault) as connection:
        result = run_eval_suite(
            connection,
            suite=suite,
            case_id=case,
            limit=limit,
            fail_fast=fail_fast,
            search_options=options,
        )
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "eval_run_id": result.eval_run_id,
                    "suite": result.suite,
                    "status": result.status,
                    "case_count": result.case_count,
                    "passed_count": result.passed_count,
                    "failed_count": result.failed_count,
                    "error_count": result.error_count,
                    "results": [
                        {
                            "eval_result_id": item.eval_result_id,
                            "eval_case_id": item.eval_case_id,
                            "status": item.status,
                            "retrieval_run_id": item.retrieval_run_id,
                            "readiness_report_id": item.readiness_report_id,
                            "failures": list(item.failures),
                            "error": item.error,
                        }
                        for item in result.results
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        console.print(f"Eval run: {result.eval_run_id} ({result.status})")
        console.print(
            f"Cases {result.case_count}: passed={result.passed_count} "
            f"failed={result.failed_count} errors={result.error_count}"
        )
        for item in result.results:
            console.print(f"- {item.eval_case_id}: {item.status}")
            if item.failures:
                console.print(f"  failures: {', '.join(item.failures)}")
            if item.error:
                console.print(f"  error: {item.error}")
    if result.failed_count > 0 or result.error_count > 0:
        raise typer.Exit(1)


@eval_retrieval_app.command("list")
def eval_retrieval_list(
    vault: Path = typer.Option(Path("."), "--vault", help="Vault directory."),
    limit: int = typer.Option(20, "--limit", min=1, help="Maximum runs to list."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """List persisted retrieval evaluation runs."""
    with _existing_vault_connection(vault) as connection:
        rows = list_eval_runs(connection, limit=limit)
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "runs": [
                        {
                            "eval_run_id": row["eval_run_id"],
                            "suite": row["suite"],
                            "status": row["status"],
                            "case_count": row["case_count"],
                            "passed_count": row["passed_count"],
                            "failed_count": row["failed_count"],
                            "error_count": row["error_count"],
                            "created_at": row["created_at"],
                        }
                        for row in rows
                    ]
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if not rows:
        console.print("No eval runs")
        return
    table = Table(title="Retrieval Eval Runs")
    table.add_column("Run")
    table.add_column("Suite")
    table.add_column("Status")
    table.add_column("Passed")
    table.add_column("Failed")
    for row in rows:
        table.add_row(
            str(row["eval_run_id"]),
            str(row["suite"]),
            str(row["status"]),
            str(row["passed_count"]),
            str(row["failed_count"]),
        )
    console.print(table)


@eval_retrieval_app.command("show")
def eval_retrieval_show(
    eval_run_id: str = typer.Argument(..., help="Evaluation run ID."),
    vault: Path = typer.Option(Path("."), "--vault", help="Vault directory."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Show a retrieval evaluation run and its results."""
    with _existing_vault_connection(vault) as connection:
        run = get_eval_run(connection, eval_run_id)
        if run is None:
            console.print(f"[red]Eval run not found: {eval_run_id}[/red]")
            raise typer.Exit(1)
        results = list_eval_results(connection, eval_run_id)
    if json_output:
        typer.echo(
            json.dumps(
                {"run": dict(run), "results": [dict(row) for row in results]},
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return
    console.print(f"Eval run: {run['eval_run_id']} ({run['status']})")
    for row in results:
        console.print(
            f"- {row['eval_case_id']}: {row['status']} "
            f"retrieval={row['retrieval_run_id']} readiness={row['readiness_report_id']}"
        )


@eval_retrieval_app.command("readiness")
def eval_retrieval_readiness(
    retrieval_run_id: str = typer.Argument(..., help="Retrieval run ID to assess."),
    vault: Path = typer.Option(Path("."), "--vault", help="Vault directory."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Assess answer readiness for a persisted retrieval run."""
    with _existing_vault_connection(vault) as connection:
        try:
            report = assess_answer_readiness(connection, retrieval_run_id)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "readiness_report_id": report.readiness_report_id,
                    "retrieval_run_id": report.retrieval_run_id,
                    "policy_version": report.policy_version,
                    "verdict": report.verdict,
                    "score": report.score,
                    "blockers": list(report.blockers),
                    "warnings": list(report.warnings),
                    "metrics": report.metrics,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        console.print(f"Readiness: {report.verdict} (score={report.score})")
        console.print(f"Report: {report.readiness_report_id}")
        if report.blockers:
            console.print("Blockers:")
            for blocker in report.blockers:
                console.print(f"- {blocker}")
        if report.warnings:
            console.print("Warnings:")
            for warning in report.warnings:
                console.print(f"- {warning}")
    if report.verdict == "not_ready":
        raise typer.Exit(1)


@app.command()
def tui(
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    action: str | None = typer.Option(
        None,
        "--action",
        help="TUI-lite action: dashboard, init, ingest, search, tasks, review, errors, settings.",
    ),
    source_input: Path | None = typer.Option(
        None,
        "--source",
        help="Source file or folder for --action ingest.",
    ),
    query: str | None = typer.Option(
        None,
        "--query",
        help="Search query for --action search.",
    ),
    recursive: bool = typer.Option(
        False,
        "--recursive",
        help="Recursively ingest folders for --action ingest.",
    ),
    limit: int = typer.Option(
        10,
        "--limit",
        min=1,
        help="Maximum rows/results to show.",
    ),
    category_template: str = typer.Option(
        "minimal",
        "--category-template",
        help=f"Category template for --action init: {', '.join(template_names())}.",
    ),
) -> None:
    """Run the M4 guided CLI / TUI-lite surface."""
    exit_code = run_tui_lite(
        console=console,
        vault=vault,
        action=action,
        source_input=source_input,
        query=query,
        recursive=recursive,
        limit=limit,
        category_template=category_template,
    )
    raise typer.Exit(exit_code)


@catalog_app.command("list")
def catalog_list(
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    include_inactive: bool = typer.Option(
        False,
        "--include-inactive",
        help="Include inactive or deleted categories.",
    ),
) -> None:
    """List categories."""
    with _existing_vault_connection(vault) as connection:
        rows = list_categories(connection, include_inactive=include_inactive)

    table = Table(title="Categories")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Active")
    table.add_column("System")
    for row in rows:
        table.add_row(
            row["category_id"],
            row["name"],
            _yes_no(row["is_active"]),
            _yes_no(row["is_system"]),
        )
    console.print(table)


@catalog_app.command("add")
def catalog_add(
    name: str = typer.Argument(..., help="Category name."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    description: str | None = typer.Option(
        None,
        "--description",
        help="Optional category description.",
    ),
) -> None:
    """Add a manual category."""
    with _existing_vault_connection(vault) as connection:
        category_id = add_category(connection, name=name, description=description)
    console.print(f"Added category {category_id}: {name}")


@catalog_app.command("update")
def catalog_update(
    category_id: str = typer.Argument(..., help="Category ID to update."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        help="Replacement category name.",
    ),
    description: str | None = typer.Option(
        None,
        "--description",
        help="Replacement category description.",
    ),
    sort_order: int | None = typer.Option(
        None,
        "--sort-order",
        help="Replacement category sort order.",
    ),
) -> None:
    """Update a category without touching document revisions."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = update_category(
                connection,
                category_id,
                name=name,
                description=description,
                sort_order=sort_order,
            )
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    state = "updated" if result.changed else "unchanged"
    console.print(f"Category {result.category_id}: {state}")


@catalog_app.command("archive")
def catalog_archive(
    category_id: str = typer.Argument(..., help="Manual category ID to archive."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
) -> None:
    """Archive an unused manual category without physical deletion."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = archive_category(connection, category_id)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    state = "archived" if result.changed else "already archived"
    console.print(f"Category {result.category_id}: {state}")


@catalog_app.command("restore")
def catalog_restore(
    category_id: str = typer.Argument(..., help="Category ID to restore."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
) -> None:
    """Restore an archived category."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = restore_category(connection, category_id)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    state = "restored" if result.changed else "already active"
    console.print(f"Category {result.category_id}: {state}")


@tag_app.command("list")
def tag_list(
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    limit: int = typer.Option(
        50,
        "--limit",
        min=1,
        help="Maximum number of tags to show.",
    ),
    include_inactive: bool = typer.Option(
        False,
        "--include-inactive",
        help="Include archived tags.",
    ),
) -> None:
    """List manual tags."""
    with _existing_vault_connection(vault) as connection:
        rows = list_tags(connection, limit=limit, include_inactive=include_inactive)

    table = Table(title="Tags")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Language")
    table.add_column("Archived")
    for row in rows:
        table.add_row(
            row["tag_id"],
            row["name"],
            row["type"] or "",
            row["language"] or "",
            _yes_no(row["deleted_at"]),
        )
    console.print(table if rows else "No tags")


@tag_app.command("add")
def tag_add(
    name: str = typer.Argument(..., help="Tag name."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    tag_type: str = typer.Option(
        ...,
        "--type",
        help="Tag type: topic, method, tool, entity, workflow, format, language, project.",
    ),
    description: str | None = typer.Option(
        None,
        "--description",
        help="Optional tag description.",
    ),
    language: str | None = typer.Option(
        None,
        "--language",
        help="Optional tag language.",
    ),
) -> None:
    """Add or reuse a governed tag with an explicit type."""
    with _existing_vault_connection(vault) as connection:
        try:
            tag_id = add_tag(connection, name, tag_type=tag_type, description=description, language=language)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    console.print(f"Tag {tag_id}: {name} ({tag_type})")


@tag_app.command("update")
def tag_update(
    tag_id: str = typer.Argument(..., help="Tag ID to update."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        help="Replacement tag name.",
    ),
    description: str | None = typer.Option(
        None,
        "--description",
        help="Replacement tag description.",
    ),
    language: str | None = typer.Option(
        None,
        "--language",
        help="Replacement tag language.",
    ),
) -> None:
    """Update a tag without touching document revisions."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = update_tag(
                connection,
                tag_id,
                name=name,
                description=description,
                language=language,
            )
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    state = "updated" if result.changed else "unchanged"
    console.print(f"Tag {result.tag_id} {result.tag_name}: {state}")


@tag_app.command("archive")
def tag_archive(
    tag_id: str = typer.Argument(..., help="Tag ID to archive."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
) -> None:
    """Archive a tag without physical deletion."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = archive_tag(connection, tag_id)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    state = "archived" if result.changed else "already archived"
    console.print(f"Tag {result.tag_id} {result.tag_name}: {state}")


@tag_app.command("restore")
def tag_restore(
    tag_id: str = typer.Argument(..., help="Tag ID to restore."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
) -> None:
    """Restore an archived tag."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = restore_tag(connection, tag_id)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    state = "restored" if result.changed else "already active"
    console.print(f"Tag {result.tag_id} {result.tag_name}: {state}")


@task_app.command("list")
def task_list(
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        min=1,
        help="Maximum number of tasks to show.",
    ),
) -> None:
    """List recent tasks."""
    with _existing_vault_connection(vault) as connection:
        rows = list_tasks(connection, limit=limit)

    table = Table(title="Tasks")
    table.add_column("ID")
    table.add_column("Type")
    table.add_column("Status")
    table.add_column("Created")
    for row in rows:
        table.add_row(row["task_id"], row["type"], row["status"], row["created_at"])
    console.print(table)


@task_app.command("show")
def task_show(
    task_id: str = typer.Argument(..., help="Task ID."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
) -> None:
    """Show one task and its events."""
    with _existing_vault_connection(vault) as connection:
        task = get_task(connection, task_id)
        events = list_task_events(connection, task_id) if task is not None else []

    if task is None:
        console.print(f"[red]Task not found:[/red] {task_id}")
        raise typer.Exit(1)

    console.print(f"Task {task['task_id']}")
    console.print(f"Type: {task['type']}")
    console.print(f"Status: {task['status']}")
    console.print(f"Created: {task['created_at']}")
    if task["result_json"]:
        console.print(f"Result: {task['result_json']}")
    if task["error_json"]:
        console.print(f"Error: {task['error_json']}")

    table = Table(title="Task Events")
    table.add_column("Created")
    table.add_column("Type")
    table.add_column("Message")
    for event in events:
        table.add_row(event["created_at"], event["event_type"] or "", event["message"] or "")
    console.print(table)


@index_app.command("status")
def index_status(
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
) -> None:
    """Show current index state."""
    with _existing_vault_connection(vault) as connection:
        rows = list(
            connection.execute(
                """
                SELECT COALESCE(fts_status, 'unknown') AS fts_status, COUNT(*) AS count
                FROM documents
                WHERE status = 'active'
                  AND deleted_at IS NULL
                GROUP BY COALESCE(fts_status, 'unknown')
                ORDER BY fts_status
                """
            )
        )
        current_chunks = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM chunks c
            JOIN documents d ON d.doc_id = c.doc_id
            WHERE d.status = 'active'
              AND d.current_revision_id = c.revision_id
              AND c.is_current = 1
              AND c.deleted_at IS NULL
            """
        ).fetchone()["count"]
        fts_rows = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()["count"]
        embedding_rows = list(
            connection.execute(
                """
                SELECT COALESCE(embedding_status, 'unknown') AS embedding_status, COUNT(*) AS count
                FROM documents
                WHERE status = 'active'
                  AND deleted_at IS NULL
                GROUP BY COALESCE(embedding_status, 'unknown')
                ORDER BY embedding_status
                """
            )
        )
        vector_rows = list(
            connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM embeddings
                WHERE deleted_at IS NULL
                GROUP BY status
                ORDER BY status
                """
            )
        )

    table = Table(title="Index Status")
    table.add_column("FTS Status")
    table.add_column("Documents", justify="right")
    for row in rows:
        table.add_row(row["fts_status"], str(row["count"]))
    if not rows:
        table.add_row("none", "0")
    console.print(table)
    console.print(f"Current chunks: {current_chunks}")
    console.print(f"FTS rows: {fts_rows}")
    embedding_table = Table(title="Embedding Status")
    embedding_table.add_column("Embedding Status")
    embedding_table.add_column("Documents", justify="right")
    for row in embedding_rows:
        embedding_table.add_row(row["embedding_status"], str(row["count"]))
    if not embedding_rows:
        embedding_table.add_row("none", "0")
    console.print(embedding_table)
    vector_table = Table(title="Embedding Rows")
    vector_table.add_column("Row Status")
    vector_table.add_column("Rows", justify="right")
    for row in vector_rows:
        vector_table.add_row(row["status"], str(row["count"]))
    if not vector_rows:
        vector_table.add_row("none", "0")
    console.print(vector_table)


@index_app.command("rebuild")
def index_rebuild(
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    fts: bool = typer.Option(
        False,
        "--fts",
        help="Rebuild the SQLite FTS index.",
    ),
    vectors: bool = typer.Option(
        False,
        "--vectors",
        help="Rebuild deterministic local vectors for active current chunks.",
    ),
) -> None:
    """Rebuild supported local indexes."""
    if not fts and not vectors:
        console.print("[red]No supported index selected.[/red] Use --fts or --vectors.")
        raise typer.Exit(2)

    exit_code = 0
    with _existing_vault_connection(vault) as connection:
        if fts:
            result = rebuild_fts_index(connection, vault)

            console.print("FTS rebuild complete")
            console.print(f"Active documents: {result.active_documents}")
            console.print(f"Indexed documents: {result.indexed_documents}")
            console.print(f"Indexed chunks: {result.indexed_chunks}")
            console.print(f"Failed documents: {result.failed_documents}")

            if result.failures:
                table = Table(title="FTS Index Failures")
                table.add_column("Doc ID")
                table.add_column("Reason")
                table.add_column("Error ID")
                for failure in result.failures:
                    table.add_row(failure.doc_id, failure.reason, failure.error_id)
                console.print(table)
                exit_code = 2

        if vectors:
            vector_result = rebuild_vector_index(connection)
            console.print("Vector rebuild complete")
            console.print(f"Task: {vector_result.task_id}")
            console.print(f"Provider: {vector_result.provider}")
            console.print(f"Model: {vector_result.model}")
            console.print(f"Dimension: {vector_result.dimension}")
            console.print(f"Current chunks: {vector_result.current_chunks}")
            console.print(f"Embedded chunks: {vector_result.embedded_chunks}")
            console.print(f"Failed chunks: {vector_result.failed_chunks}")

            if vector_result.failures:
                table = Table(title="Vector Index Failures")
                table.add_column("Doc ID")
                table.add_column("Chunk ID")
                table.add_column("Reason")
                table.add_column("Error ID")
                for failure in vector_result.failures:
                    table.add_row(failure.doc_id, failure.chunk_id, failure.reason, failure.error_id)
                console.print(table)
                exit_code = 2

    if exit_code:
        raise typer.Exit(2)


@doc_app.command("list")
def doc_list(
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    status: str = typer.Option(
        "active",
        "--status",
        help="Document status: active, archived, or all.",
    ),
    category_id: str | None = typer.Option(
        None,
        "--category-id",
        help="Filter by category ID.",
    ),
    tag: str | None = typer.Option(
        None,
        "--tag",
        help="Filter by active manual tag name.",
    ),
    limit: int = typer.Option(
        50,
        "--limit",
        min=1,
        help="Maximum number of documents to show.",
    ),
) -> None:
    """List documents by active/archive status and manual metadata."""
    with _existing_vault_connection(vault) as connection:
        try:
            rows = list_documents(
                connection,
                status=status,
                category_id=category_id,
                tag=tag,
                limit=limit,
            )
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc

    if not rows:
        console.print("No documents")
        return

    table = Table(title="Documents")
    table.add_column("ID")
    table.add_column("Title")
    table.add_column("Status")
    table.add_column("Category")
    table.add_column("Tags")
    table.add_column("FTS")
    table.add_column("Updated")
    for row in rows:
        table.add_row(
            row["doc_id"],
            row["title"] or "",
            row["status"] or "",
            row["category_name"] or row["category_id"] or "",
            row["tags"] or "",
            row["fts_status"] or "",
            row["updated_at"] or row["created_at"] or "",
        )
    console.print(table)
    for row in rows:
        console.print(
            f"doc: {row['doc_id']} status={row['status']} "
            f"category={row['category_name'] or row['category_id'] or ''} "
            f"tags={row['tags'] or ''} title={row['title'] or ''}"
        )


@doc_app.command("archive")
def doc_archive(
    doc_id: str = typer.Argument(..., help="Document ID to archive."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
) -> None:
    """Archive a document without deleting files, revisions, chunks, or FTS rows."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = archive_document(connection, doc_id)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    state = "archived" if result.changed else "already archived"
    console.print(f"Document {result.doc_id}: {state}")


@doc_app.command("set-category")
def doc_set_category(
    doc_id: str = typer.Argument(..., help="Document ID to update."),
    category_id: str = typer.Argument(..., help="Category ID to set."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
) -> None:
    """Set a document category without mutating revision Markdown."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = set_document_category(connection, doc_id, category_id)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    state = "updated" if result.changed else "unchanged"
    console.print(f"Document {result.doc_id} category: {result.category_id} ({state})")


@doc_app.command("add-tag")
def doc_add_tag(
    doc_id: str = typer.Argument(..., help="Document ID to tag."),
    tag: str = typer.Argument(..., help="Tag name."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
) -> None:
    """Add a manual tag to a document."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = add_document_tag(connection, doc_id, tag)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    state = "added" if result.changed else "already present"
    console.print(f"Document {result.doc_id} tag {result.tag_name}: {state}")


@doc_app.command("remove-tag")
def doc_remove_tag(
    doc_id: str = typer.Argument(..., help="Document ID to update."),
    tag: str = typer.Argument(..., help="Tag name."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
) -> None:
    """Remove a manual tag from a document."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = remove_document_tag(connection, doc_id, tag)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    state = "removed" if result.changed else "not present"
    console.print(f"Document {result.doc_id} tag {result.tag_name}: {state}")


@doc_app.command("tags")
def doc_tags(
    doc_id: str = typer.Argument(..., help="Document ID."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
) -> None:
    """List manual tags on a document."""
    with _existing_vault_connection(vault) as connection:
        try:
            rows = list_document_tags(connection, doc_id)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    table = Table(title=f"Document Tags: {doc_id}")
    table.add_column("Tag ID")
    table.add_column("Name")
    table.add_column("Source")
    for row in rows:
        table.add_row(row["tag_id"], row["name"], row["source"] or "")
    console.print(table if rows else "No document tags")


@doc_app.command("show")
def doc_show(
    doc_id: str = typer.Argument(..., help="Document ID to show."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
) -> None:
    """Show document metadata and durable file paths."""
    with _existing_vault_connection(vault) as connection:
        row = _load_document_for_cli(connection, doc_id)
    if row is None:
        console.print(f"[red]Document not found:[/red] {doc_id}")
        raise typer.Exit(1)
    _print_key_values(
        {
            "doc_id": row["doc_id"],
            "title": row["title"],
            "status": row["status"],
            "archived_at": row["archived_at"],
            "current_revision_id": row["current_revision_id"],
            "source_type": row["source_type"],
            "source_uri": row["source_uri"],
            "canonical_path": row["canonical_path"],
            "original_path": row["original_path"],
            "ingest_status": row["ingest_status"],
            "fts_status": row["fts_status"],
            "quality_status": row["quality_status"],
            "needs_review": bool(row["needs_review"]),
            "category_id": row["category_id"],
        }
    )


@doc_app.command("revisions")
def doc_revisions(
    doc_id: str = typer.Argument(..., help="Document ID."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
) -> None:
    """List immutable revisions for a document."""
    with _existing_vault_connection(vault) as connection:
        try:
            rows = list_document_revisions(connection, doc_id)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    table = Table(title=f"Document Revisions: {doc_id}")
    table.add_column("Seq", justify="right")
    table.add_column("Current")
    table.add_column("Revision ID")
    table.add_column("Chunks", justify="right")
    table.add_column("Markdown Path", overflow="fold")
    for row in rows:
        table.add_row(
            str(row["sequence"]),
            _yes_no(row["is_current"]),
            row["revision_id"],
            str(row["chunk_count"] or 0),
            row["markdown_path"],
        )
    console.print(table if rows else "No revisions")
    for row in rows:
        console.print(f"revision_path: {row['markdown_path']}")


@doc_app.command("open")
def doc_open(
    doc_id: str = typer.Argument(..., help="Document ID to open."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    original: bool = typer.Option(
        False,
        "--original",
        help="Open the archived original instead of source Markdown.",
    ),
    folder: bool = typer.Option(
        False,
        "--folder",
        help="Open the containing folder.",
    ),
    revision_id: str | None = typer.Option(
        None,
        "--revision",
        help="Open a specific revision Markdown file.",
    ),
    print_path: bool = typer.Option(
        False,
        "--print-path",
        help="Print the resolved path without launching it.",
    ),
) -> None:
    """Open source Markdown, an archived original, a folder, or a specific revision."""
    with _existing_vault_connection(vault) as connection:
        try:
            target = _resolve_doc_open_target(
                connection,
                vault,
                doc_id,
                original=original,
                folder=folder,
                revision_id=revision_id,
            )
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    if print_path:
        console.print(str(target))
        return
    typer.launch(str(target))
    console.print(str(target))


@doc_app.command("restore")
def doc_restore(
    doc_id: str = typer.Argument(..., help="Document ID to restore."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
) -> None:
    """Restore an archived document to active search results."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = restore_document(connection, doc_id)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    state = "restored" if result.changed else "already active"
    console.print(f"Document {result.doc_id}: {state}")


@review_app.command("list")
def review_list(
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    status: str = typer.Option(
        "pending",
        "--status",
        help="Review status to show, or 'all'.",
    ),
    review_type: str | None = typer.Option(
        None,
        "--type",
        help="Filter by review item type.",
    ),
    target_type: str | None = typer.Option(
        None,
        "--target-type",
        help="Filter by target type.",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        min=1,
        help="Maximum number of review items to show.",
    ),
) -> None:
    """List review queue items."""
    with _existing_vault_connection(vault) as connection:
        rows = list_review_items(
            connection,
            status=None if status == "all" else status,
            review_type=review_type,
            target_type=target_type,
            limit=limit,
        )

    console.print("Review Items")
    if not rows:
        console.print("No review items")
        return
    for row in rows:
        console.print(f"review_id: {row['review_id']}")
        console.print(f"type: {row['type']}")
        console.print(f"target: {row['target_type']}:{row['target_id']}")
        console.print(f"status: {row['status']}")
        console.print(f"priority: {row['priority'] or ''}")
        console.print(f"created_at: {row['created_at']}")
        if row["resolved_at"]:
            console.print(f"resolved_at: {row['resolved_at']}")
        if row["resolved_by"]:
            console.print(f"resolved_by: {row['resolved_by']}")
        if row["resolution_note"]:
            console.print(f"resolution_note: {row['resolution_note']}")
        console.print(f"reason: {row['reason'] or ''}")


@review_app.command("show")
def review_show(
    review_id: str = typer.Argument(..., help="Review item ID."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
) -> None:
    """Show one review item."""
    with _existing_vault_connection(vault) as connection:
        row = get_review_item(connection, review_id)
    if row is None:
        console.print(f"[red]Review item not found:[/red] {review_id}")
        raise typer.Exit(1)
    _print_key_values(
        {
            "review_id": row["review_id"],
            "type": row["type"],
            "target_type": row["target_type"],
            "target_id": row["target_id"],
            "priority": row["priority"],
            "reason": row["reason"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "resolved_at": row["resolved_at"],
            "resolution_note": row["resolution_note"],
            "resolved_by": row["resolved_by"],
        }
    )


@review_app.command("resolve")
def review_resolve(
    review_id: str = typer.Argument(..., help="Review item ID."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    note: str | None = typer.Option(
        None,
        "--note",
        help="Resolution note.",
    ),
    resolved_by: str | None = typer.Option(
        None,
        "--resolved-by",
        help="Resolver identifier.",
    ),
    accept: bool = typer.Option(
        False,
        "--accept",
        help="Accept and promote a conversion candidate review.",
    ),
    reject: bool = typer.Option(
        False,
        "--reject",
        help="Reject a conversion candidate review without writing a revision.",
    ),
) -> None:
    """Resolve a review item; conversion candidates can be accepted or rejected explicitly."""
    if accept and reject:
        console.print("[red]Use only one of --accept or --reject.[/red]")
        raise typer.Exit(2)
    with _existing_vault_connection(vault) as connection:
        try:
            if accept:
                result = accept_conversion_candidate_review(
                    connection,
                    vault,
                    review_id,
                    note=note,
                    resolved_by=resolved_by,
                )
                console.print(
                    f"Review {result.review_id}: accepted "
                    f"doc_id={result.doc_id} revision_id={result.revision_id}"
                )
                return
            if reject:
                result = reject_conversion_candidate_review(
                    connection,
                    review_id,
                    note=note,
                    resolved_by=resolved_by,
                )
                console.print(f"Review {result.review_id}: rejected doc_id={result.doc_id}")
                return
            row = resolve_review_item(connection, review_id, note=note, resolved_by=resolved_by)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    console.print(f"Review {row['review_id']}: {row['status']}")


@review_app.command("resolve-many")
def review_resolve_many(
    review_ids: list[str] = typer.Argument(..., help="Review item IDs."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    note: str | None = typer.Option(
        None,
        "--note",
        help="Resolution note applied to each review item.",
    ),
    resolved_by: str | None = typer.Option(
        None,
        "--resolved-by",
        help="Resolver identifier.",
    ),
) -> None:
    """Resolve multiple review items without applying hidden fixes."""
    with _existing_vault_connection(vault) as connection:
        try:
            rows = resolve_review_items(connection, review_ids, note=note, resolved_by=resolved_by)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    console.print(f"Resolved review items: {len(rows)}")
    for row in rows:
        console.print(f"review_id: {row['review_id']} status: {row['status']}")


@error_app.command("list")
def error_list(
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    component: str | None = typer.Option(
        None,
        "--component",
        help="Filter by component.",
    ),
    severity: str | None = typer.Option(
        None,
        "--severity",
        help="Filter by severity.",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        min=1,
        help="Maximum number of errors to show.",
    ),
) -> None:
    """List recorded errors."""
    with _existing_vault_connection(vault) as connection:
        rows = list_errors(connection, component=component, severity=severity, limit=limit)

    console.print("Errors")
    if not rows:
        console.print("No errors")
        return
    for row in rows:
        console.print(f"error_id: {row['error_id']}")
        console.print(f"component: {row['component'] or ''}")
        console.print(f"error_type: {row['error_type'] or ''}")
        console.print(f"severity: {row['severity'] or ''}")
        console.print(f"retryable: {_yes_no(row['retryable'])}")
        console.print(f"created_at: {row['created_at']}")
        console.print(f"message: {row['message'] or row['user_message'] or ''}")


@error_app.command("show")
def error_show(
    error_id: str = typer.Argument(..., help="Error ID."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
) -> None:
    """Show one recorded error."""
    with _existing_vault_connection(vault) as connection:
        row = get_error(connection, error_id)
    if row is None:
        console.print(f"[red]Error not found:[/red] {error_id}")
        raise typer.Exit(1)
    _print_key_values(
        {
            "error_id": row["error_id"],
            "task_id": row["task_id"],
            "component": row["component"],
            "error_type": row["error_type"],
            "severity": row["severity"],
            "retryable": bool(row["retryable"]),
            "user_message": row["user_message"],
            "developer_message": row["developer_message"],
            "message": row["message"],
            "payload_json": row["payload_json"],
            "created_at": row["created_at"],
        }
    )


@ocr_app.command("run")
def ocr_run(
    doc_id: str = typer.Argument(..., help="Document ID to OCR."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    engine: str = typer.Option(
        "sidecar",
        "--engine",
        help="OCR engine. M6.2 supports sidecar for deterministic local OCR text.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Allow OCR to create a new current revision when the document already has one.",
    ),
) -> None:
    """Run explicit OCR for a document archived original."""
    with _existing_vault_connection(vault) as connection:
        result = run_ocr_for_document(connection, vault, doc_id, engine=engine, force=force)
    console.print(f"OCR task: {result.task_id}")
    console.print(f"Document: {result.doc_id}")
    console.print(f"Status: {result.status}")
    console.print(f"Engine: {result.engine}")
    console.print(f"Pages: {result.page_count}")
    console.print(f"Revision: {result.revision_id or ''}")
    console.print(f"Chunks: {result.chunk_count}")
    console.print(f"Indexed chunks: {result.indexed_chunks}")
    console.print(f"Review items: {result.review_items}")
    if result.error_id:
        console.print(f"Error: {result.error_id}")
    if result.status == "blocked":
        raise typer.Exit(1)
    if result.status == "failed":
        raise typer.Exit(2)
    if result.status == "completed_with_issues":
        raise typer.Exit(1)


@ocr_app.command("pages")
def ocr_pages(
    doc_id: str = typer.Argument(..., help="Document ID."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        min=1,
        help="Maximum pages to show.",
    ),
) -> None:
    """List OCR page records for a document."""
    with _existing_vault_connection(vault) as connection:
        rows = list_ocr_pages(connection, doc_id)[:limit]
    if not rows:
        console.print("No OCR pages")
        return
    table = Table(title=f"OCR Pages: {doc_id}")
    table.add_column("Page", justify="right")
    table.add_column("Revision ID")
    table.add_column("Confidence")
    table.add_column("Quality")
    table.add_column("Engine")
    table.add_column("Text", overflow="fold")
    for row in rows:
        text = " ".join(str(row["text"] or "").split())
        table.add_row(
            str(row["page_number"]),
            row["revision_id"],
            "" if row["confidence"] is None else f"{float(row['confidence']):.2f}",
            row["quality_status"] or "",
            row["engine"] or "",
            text[:160],
        )
    console.print(table)
    for row in rows:
        console.print(f"ocr_page: {row['ocr_page_id']} page={row['page_number']} revision={row['revision_id']}")


@classification_app.command("suggest")
def classify_suggest(
    doc_id: str | None = typer.Argument(None, help="Optional document ID to classify."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    all_uncategorized: bool = typer.Option(
        False,
        "--all-uncategorized",
        help="Suggest categories for profiled uncategorized documents.",
    ),
    min_confidence: float = typer.Option(
        0.65,
        "--min-confidence",
        min=0.0,
        max=1.0,
        help="Minimum confidence required to create a pending suggestion.",
    ),
    limit: int = typer.Option(
        50,
        "--limit",
        min=1,
        help="Maximum active current documents to scan.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Supersede pending local suggestions for the same current revision.",
    ),
    legacy: bool = typer.Option(
        False,
        "--legacy",
        help="Use legacy classification_suggestions instead of taxonomy_suggestions.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON results.",
    ),
) -> None:
    """Create pending category suggestions from active profiles."""
    with _existing_vault_connection(vault) as connection:
        try:
            if legacy:
                result = suggest_classifications(
                    connection,
                    doc_id=doc_id,
                    min_confidence=min_confidence,
                    limit=limit,
                    force=force,
                )
            else:
                result = suggest_category_assignments(
                    connection,
                    doc_id=doc_id,
                    all_uncategorized=all_uncategorized,
                    min_confidence=min_confidence,
                    limit=limit,
                    force=force,
                )
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    payload = {
        "task_id": result.task_id,
        "scanned_documents": result.scanned_documents,
        "suggested_documents": result.suggested_documents,
        "skipped_documents": result.skipped_documents,
        "review_items": result.review_items,
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    console.print(f"Classification task: {result.task_id}")
    console.print(f"Scanned documents: {result.scanned_documents}")
    console.print(f"Suggested documents: {result.suggested_documents}")
    console.print(f"Skipped documents: {result.skipped_documents}")
    console.print(f"Review items: {result.review_items}")


@classification_app.command("list")
def classify_list(
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    status: str = typer.Option(
        "pending",
        "--status",
        help="Suggestion status to show, or 'all'.",
    ),
    doc_id: str | None = typer.Option(
        None,
        "--doc-id",
        help="Filter by document ID.",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        min=1,
        help="Maximum suggestions to show.",
    ),
    legacy: bool = typer.Option(
        False,
        "--legacy",
        help="List legacy classification_suggestions instead of taxonomy category suggestions.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON results.",
    ),
) -> None:
    """List pending category suggestions."""
    with _existing_vault_connection(vault) as connection:
        if legacy:
            rows = list_classification_suggestions(
                connection,
                status=None if status == "all" else status,
                doc_id=doc_id,
                limit=limit,
                active_current_only=status != "all",
            )
        else:
            rows = list_taxonomy_suggestions(
                connection,
                suggestion_type="category_assign",
                status=None if status == "all" else status,
                doc_id=doc_id,
                limit=limit,
                active_current_only=status != "all",
            )
    if json_output:
        if legacy:
            payload = {
                "suggestions": [
                    {
                        "suggestion_id": row["suggestion_id"],
                        "doc_id": row["doc_id"],
                        "revision_id": row["revision_id"],
                        "title": row["title"],
                        "suggested_category_id": row["suggested_category_id"],
                        "suggested_category_name": row["suggested_category_name"],
                        "confidence": row["confidence"],
                        "reason": row["reason"],
                        "suggested_tags": _json_list_for_cli(row["suggested_tags_json"]),
                        "status": row["status"],
                        "created_at": row["created_at"],
                    }
                    for row in rows
                ]
            }
        else:
            payload = {
                "suggestions": [
                    {
                        "suggestion_id": row["suggestion_id"],
                        "doc_id": row["doc_id"],
                        "revision_id": row["revision_id"],
                        "title": row["title"],
                        "suggested_category_id": json.loads(row["payload_json"]).get("category_id")
                        if row["payload_json"]
                        else row["target_id"],
                        "suggested_category_name": row["suggested_category_name"],
                        "confidence": row["confidence"],
                        "reason": json.loads(row["payload_json"]).get("reason") if row["payload_json"] else "",
                        "status": row["status"],
                        "created_at": row["created_at"],
                    }
                    for row in rows
                ]
            }
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if not rows:
        console.print("No category suggestions")
        return
    table = Table(title="Category Suggestions" if not legacy else "Classification Suggestions")
    table.add_column("ID")
    table.add_column("Doc")
    table.add_column("Category")
    if legacy:
        table.add_column("Tags")
    table.add_column("Confidence", justify="right")
    table.add_column("Status")
    for row in rows:
        if legacy:
            table.add_row(
                row["suggestion_id"],
                row["doc_id"],
                row["suggested_category_name"] or row["suggested_category_id"] or "",
                ", ".join(_json_list_for_cli(row["suggested_tags_json"])),
                "" if row["confidence"] is None else f"{float(row['confidence']):.2f}",
                row["status"],
            )
        else:
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
            table.add_row(
                row["suggestion_id"],
                row["doc_id"] or "",
                row["suggested_category_name"] or str(payload.get("category_id") or ""),
                "" if row["confidence"] is None else f"{float(row['confidence']):.2f}",
                row["status"],
            )
    console.print(table)


@classification_app.command("show")
def classify_show(
    suggestion_id: str = typer.Argument(..., help="Suggestion ID."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    legacy: bool = typer.Option(
        False,
        "--legacy",
        help="Read legacy classification_suggestions only.",
    ),
) -> None:
    """Show one category suggestion."""
    with _existing_vault_connection(vault) as connection:
        if legacy or suggestion_id.startswith("suggestion_"):
            row = get_classification_suggestion(connection, suggestion_id)
            if row is None or row["deleted_at"] is not None:
                console.print(f"[red]Classification suggestion not found:[/red] {suggestion_id}")
                raise typer.Exit(1)
            _print_key_values(
                {
                    "suggestion_id": row["suggestion_id"],
                    "doc_id": row["doc_id"],
                    "revision_id": row["revision_id"],
                    "title": row["title"],
                    "current_revision_id": row["current_revision_id"],
                    "document_status": row["document_status"],
                    "current_category_id": row["current_category_id"],
                    "suggested_category_id": row["suggested_category_id"],
                    "suggested_category_name": row["suggested_category_name"],
                    "confidence": row["confidence"],
                    "reason": row["reason"],
                    "alternative_category_ids_json": row["alternative_category_ids_json"],
                    "suggested_tags_json": row["suggested_tags_json"],
                    "needs_user_confirmation": bool(row["needs_user_confirmation"]),
                    "model": row["model"],
                    "prompt_version": row["prompt_version"],
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
            return
        row = get_taxonomy_suggestion(connection, suggestion_id)
    if row is None:
        legacy_row = get_classification_suggestion(connection, suggestion_id)
        if legacy_row is not None and legacy_row["deleted_at"] is None:
            _print_key_values(
                {
                    "suggestion_id": legacy_row["suggestion_id"],
                    "doc_id": legacy_row["doc_id"],
                    "revision_id": legacy_row["revision_id"],
                    "title": legacy_row["title"],
                    "suggested_category_id": legacy_row["suggested_category_id"],
                    "status": legacy_row["status"],
                }
            )
            return
        console.print(f"[red]Taxonomy suggestion not found:[/red] {suggestion_id}")
        raise typer.Exit(1)
    payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
    _print_key_values(
        {
            "suggestion_id": row["suggestion_id"],
            "type": row["type"],
            "doc_id": row["doc_id"],
            "revision_id": row["revision_id"],
            "title": row["title"],
            "current_revision_id": row["current_revision_id"],
            "document_status": row["document_status"],
            "current_category_id": row["current_category_id"],
            "suggested_category_id": payload.get("category_id") or row["target_id"],
            "suggested_category_name": row["suggested_category_name"],
            "confidence": row["confidence"],
            "reason": payload.get("reason"),
            "alternative_category_ids": payload.get("alternative_category_ids"),
            "source": row["source"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )


@classification_app.command("accept")
def classify_accept(
    suggestion_id: str = typer.Argument(..., help="Suggestion ID."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    no_category: bool = typer.Option(
        False,
        "--no-category",
        help="Legacy only: accept suggested tags without category.",
    ),
    no_tags: bool = typer.Option(
        False,
        "--no-tags",
        help="Legacy only: accept suggested category without tags.",
    ),
    force_category: bool = typer.Option(
        False,
        "--force-category",
        help="Allow replacing a non-uncategorized existing category.",
    ),
    legacy: bool = typer.Option(
        False,
        "--legacy",
        help="Force legacy classification_suggestions accept path.",
    ),
    reason: str | None = typer.Option(
        None,
        "--reason",
        help="Feedback reason.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON results.",
    ),
) -> None:
    """Accept a pending category suggestion."""
    with _existing_vault_connection(vault) as connection:
        try:
            if legacy or suggestion_id.startswith("suggestion_"):
                result = accept_classification_suggestion(
                    connection,
                    suggestion_id,
                    apply_category=not no_category,
                    apply_tags=not no_tags,
                    force_category=force_category,
                    reason=reason,
                )
                payload = {
                    "suggestion_id": result.suggestion_id,
                    "doc_id": result.doc_id,
                    "status": result.status,
                    "category_changed": result.category_changed,
                    "tags_added": list(result.tags_added),
                    "feedback_id": result.feedback_id,
                }
            else:
                taxonomy_row = get_taxonomy_suggestion(connection, suggestion_id)
                if taxonomy_row is None:
                    result = accept_classification_suggestion(
                        connection,
                        suggestion_id,
                        apply_category=not no_category,
                        apply_tags=not no_tags,
                        force_category=force_category,
                        reason=reason,
                    )
                    payload = {
                        "suggestion_id": result.suggestion_id,
                        "doc_id": result.doc_id,
                        "status": result.status,
                        "category_changed": result.category_changed,
                        "tags_added": list(result.tags_added),
                        "feedback_id": result.feedback_id,
                    }
                else:
                    if no_category or no_tags:
                        console.print("[yellow]--no-category/--no-tags apply only to legacy suggestions.[/yellow]")
                    result = accept_taxonomy_suggestion(
                        connection,
                        suggestion_id,
                        force_category=force_category,
                        reason=reason,
                    )
                    payload = {
                        "suggestion_id": result.suggestion_id,
                        "doc_id": result.doc_id,
                        "status": result.status,
                        "category_changed": result.category_changed,
                        "tags_added": list(result.tags_added),
                    }
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    console.print(f"Suggestion {payload['suggestion_id']}: accepted")
    console.print(f"Document: {payload['doc_id']}")
    console.print(f"Category changed: {_yes_no(payload.get('category_changed'))}")
    if payload.get("tags_added"):
        console.print(f"Tags added: {', '.join(payload['tags_added'])}")
    if payload.get("feedback_id"):
        console.print(f"Feedback: {payload['feedback_id']}")


@classification_app.command("reject")
def classify_reject(
    suggestion_id: str = typer.Argument(..., help="Suggestion ID."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    legacy: bool = typer.Option(
        False,
        "--legacy",
        help="Force legacy classification_suggestions reject path.",
    ),
    reason: str | None = typer.Option(
        None,
        "--reason",
        help="Feedback reason.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON results.",
    ),
) -> None:
    """Reject a pending category suggestion."""
    with _existing_vault_connection(vault) as connection:
        try:
            if legacy or suggestion_id.startswith("suggestion_"):
                result = reject_classification_suggestion(connection, suggestion_id, reason=reason)
                payload = {
                    "suggestion_id": result.suggestion_id,
                    "doc_id": result.doc_id,
                    "status": result.status,
                    "feedback_id": result.feedback_id,
                }
            else:
                taxonomy_row = get_taxonomy_suggestion(connection, suggestion_id)
                if taxonomy_row is None:
                    result = reject_classification_suggestion(connection, suggestion_id, reason=reason)
                    payload = {
                        "suggestion_id": result.suggestion_id,
                        "doc_id": result.doc_id,
                        "status": result.status,
                        "feedback_id": result.feedback_id,
                    }
                else:
                    result = reject_taxonomy_suggestion(connection, suggestion_id, reason=reason)
                    payload = {
                        "suggestion_id": result.suggestion_id,
                        "doc_id": result.doc_id,
                        "status": result.status,
                    }
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    console.print(f"Suggestion {payload['suggestion_id']}: rejected")
    console.print(f"Document: {payload['doc_id']}")
    if payload.get("feedback_id"):
        console.print(f"Feedback: {payload['feedback_id']}")


@translation_app.command("document")
def translate_document(
    doc_id: str = typer.Argument(..., help="Source document ID."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    revision_id: str = typer.Option(
        ...,
        "--revision",
        help="Current source revision ID to translate from.",
    ),
    target_language: str = typer.Option(
        ...,
        "--target-language",
        help="Target language for the translation output.",
    ),
    source_language: str | None = typer.Option(
        None,
        "--source-language",
        help="Optional source language override.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON results.",
    ),
) -> None:
    """Translate every current chunk in a document into one output Markdown file."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = translate_full_document(
                connection,
                vault,
                doc_id=doc_id,
                revision_id=revision_id,
                source_language=source_language,
                target_language=target_language,
            )
        except Exception as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    payload = {
        "translation_id": result.translation_id,
        "execution_id": result.execution_id,
        "task_id": result.task_id,
        "source_doc_id": result.source_doc_id,
        "source_revision_id": result.source_revision_id,
        "source_chunk_ids": list(result.source_chunk_ids),
        "target_language": result.target_language,
        "output_path": result.output_path,
        "status": result.status,
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    console.print(f"Translation: {result.translation_id}")
    console.print(f"Execution: {result.execution_id}")
    console.print(f"Task: {result.task_id}")
    console.print(f"Document: {result.source_doc_id}")
    console.print(f"Revision: {result.source_revision_id}")
    console.print(f"Chunks: {', '.join(result.source_chunk_ids)}")
    console.print(f"Target language: {result.target_language}")
    console.print(f"Output: {result.output_path}")


@translation_app.command("chunks")
def translate_chunks(
    doc_id: str = typer.Argument(..., help="Source document ID."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    revision_id: str = typer.Option(
        ...,
        "--revision",
        help="Current source revision ID to translate from.",
    ),
    chunk_ids: list[str] | None = typer.Option(
        None,
        "--chunk",
        help="Source chunk ID to translate. Repeat for multiple selected chunks.",
    ),
    target_language: str = typer.Option(
        ...,
        "--target-language",
        help="Target language for the translation output.",
    ),
    source_language: str | None = typer.Option(
        None,
        "--source-language",
        help="Optional source language override.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON results.",
    ),
) -> None:
    """Translate selected current chunks into an output Markdown file."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = translate_selected_chunks(
                connection,
                vault,
                doc_id=doc_id,
                revision_id=revision_id,
                chunk_ids=tuple(chunk_ids or ()),
                source_language=source_language,
                target_language=target_language,
            )
        except Exception as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    payload = {
        "translation_id": result.translation_id,
        "execution_id": result.execution_id,
        "task_id": result.task_id,
        "source_doc_id": result.source_doc_id,
        "source_revision_id": result.source_revision_id,
        "source_chunk_ids": list(result.source_chunk_ids),
        "target_language": result.target_language,
        "output_path": result.output_path,
        "status": result.status,
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    console.print(f"Translation: {result.translation_id}")
    console.print(f"Execution: {result.execution_id}")
    console.print(f"Task: {result.task_id}")
    console.print(f"Document: {result.source_doc_id}")
    console.print(f"Revision: {result.source_revision_id}")
    console.print(f"Chunks: {', '.join(result.source_chunk_ids)}")
    console.print(f"Target language: {result.target_language}")
    console.print(f"Output: {result.output_path}")


@translation_app.command("list")
def translate_list(
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    doc_id: str | None = typer.Option(
        None,
        "--doc-id",
        help="Filter by source document ID.",
    ),
    status: str | None = typer.Option(
        None,
        "--status",
        help="Filter by translation status.",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        min=1,
        help="Maximum translations to show.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON results.",
    ),
) -> None:
    """List translation outputs."""
    with _existing_vault_connection(vault) as connection:
        rows = list_translations(connection, doc_id=doc_id, status=status, limit=limit)
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "translations": [
                        {
                            "translation_id": row["translation_id"],
                            "execution_id": row["execution_id"],
                            "source_doc_id": row["source_doc_id"],
                            "source_revision_id": row["source_revision_id"],
                            "source_chunk_ids": _json_list_for_cli(row["source_chunk_ids_json"]),
                            "target_language": row["target_language"],
                            "output_path": row["output_path"],
                            "status": row["status"],
                            "created_at": row["created_at"],
                        }
                        for row in rows
                    ]
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if not rows:
        console.print("No translations")
        return
    table = Table(title="Translations")
    table.add_column("ID")
    table.add_column("Doc")
    table.add_column("Revision")
    table.add_column("Target")
    table.add_column("Status")
    table.add_column("Output")
    for row in rows:
        table.add_row(
            row["translation_id"],
            row["source_doc_id"],
            row["source_revision_id"],
            row["target_language"] or "",
            row["status"] or "",
            row["output_path"] or "",
        )
    console.print(table)
    for row in rows:
        console.print(f"translation: {row['translation_id']} doc={row['source_doc_id']} status={row['status']}")


@translation_app.command("show")
def translate_show(
    translation_id: str = typer.Argument(..., help="Translation ID."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
) -> None:
    """Show one translation record."""
    with _existing_vault_connection(vault) as connection:
        row = get_translation(connection, translation_id)
    if row is None or row["deleted_at"] is not None:
        console.print(f"[red]Translation not found:[/red] {translation_id}")
        raise typer.Exit(1)
    _print_key_values(
        {
            "translation_id": row["translation_id"],
            "execution_id": row["execution_id"],
            "source_doc_id": row["source_doc_id"],
            "source_revision_id": row["source_revision_id"],
            "source_chunk_ids_json": row["source_chunk_ids_json"],
            "source_language": row["source_language"],
            "target_language": row["target_language"],
            "translation_mode": row["translation_mode"],
            "output_path": row["output_path"],
            "model": row["model"],
            "prompt_version": row["prompt_version"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )


@translation_app.command("open")
def translate_open(
    translation_id: str = typer.Argument(..., help="Translation ID."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    folder: bool = typer.Option(
        False,
        "--folder",
        help="Open the containing folder.",
    ),
    print_path: bool = typer.Option(
        False,
        "--print-path",
        help="Print the resolved path without launching it.",
    ),
) -> None:
    """Open a translation output file or print its resolved path."""
    with _existing_vault_connection(vault) as connection:
        try:
            target = resolve_translation_output_path(connection, vault, translation_id, folder=folder)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    if print_path:
        console.print(str(target))
        return
    typer.launch(str(target))
    console.print(str(target))


@card_app.command("generate")
def card_generate(
    doc_id: str = typer.Argument(..., help="Source document ID."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    revision_id: str | None = typer.Option(
        None,
        "--revision",
        help="Current source revision ID. Defaults to the document current revision.",
    ),
    max_claims: int = typer.Option(
        3,
        "--max-claims",
        min=1,
        help="Maximum deterministic claim records to create.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON results.",
    ),
) -> None:
    """Create a source-bound candidate card record without writing an atomic note."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = generate_candidate_card(
                connection,
                doc_id=doc_id,
                revision_id=revision_id,
                max_claims=max_claims,
            )
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    payload = {
        "candidate_card_id": result.candidate_card_id,
        "task_id": result.task_id,
        "source_doc_id": result.source_doc_id,
        "source_revision_id": result.source_revision_id,
        "source_chunk_ids": list(result.source_chunk_ids),
        "claims_count": result.claims_count,
        "status": result.status,
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    console.print(f"Candidate card: {result.candidate_card_id}")
    console.print(f"Task: {result.task_id}")
    console.print(f"Document: {result.source_doc_id}")
    console.print(f"Revision: {result.source_revision_id}")
    console.print(f"Claims: {result.claims_count}")
    console.print(f"Status: {result.status}")


@card_app.command("list")
def card_list(
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    doc_id: str | None = typer.Option(
        None,
        "--doc-id",
        help="Filter by source document ID.",
    ),
    status: str = typer.Option(
        "reviewing",
        "--status",
        help="Filter by status, or use 'all'.",
    ),
    include_stale: bool = typer.Option(
        False,
        "--include-stale",
        help="Include cards bound to old revisions or archived documents.",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        min=1,
        help="Maximum candidate cards to show.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON results.",
    ),
) -> None:
    """List candidate card records. Defaults to active current documents only."""
    status_filter = None if status == "all" else status
    with _existing_vault_connection(vault) as connection:
        rows = list_candidate_cards(
            connection,
            doc_id=doc_id,
            status=status_filter,
            limit=limit,
            active_current_only=not include_stale,
        )
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "candidate_cards": [
                        {
                            "candidate_card_id": row["candidate_card_id"],
                            "source_doc_id": row["source_doc_id"],
                            "source_revision_id": row["source_revision_id"],
                            "title": row["title"],
                            "claims_count": len(_json_array_for_cli(row["claims_json"])),
                            "source_count": row["source_count"],
                            "status": row["status"],
                            "created_at": row["created_at"],
                        }
                        for row in rows
                    ]
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if not rows:
        console.print("No candidate cards")
        return
    table = Table(title="Candidate Cards")
    table.add_column("ID")
    table.add_column("Doc")
    table.add_column("Revision")
    table.add_column("Claims")
    table.add_column("Sources")
    table.add_column("Status")
    for row in rows:
        table.add_row(
            row["candidate_card_id"],
            row["source_doc_id"],
            row["source_revision_id"],
            str(len(_json_array_for_cli(row["claims_json"]))),
            str(row["source_count"]),
            row["status"],
        )
    console.print(table)
    for row in rows:
        console.print(f"candidate_card: {row['candidate_card_id']} doc={row['source_doc_id']} status={row['status']}")


@card_app.command("show")
def card_show(
    candidate_card_id: str = typer.Argument(..., help="Candidate card ID."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON results.",
    ),
) -> None:
    """Show one candidate card and its source chunk bindings."""
    with _existing_vault_connection(vault) as connection:
        row = get_candidate_card(connection, candidate_card_id)
        if row is None or row["deleted_at"] is not None:
            console.print(f"[red]Candidate card not found:[/red] {candidate_card_id}")
            raise typer.Exit(1)
        sources = list_candidate_card_sources(connection, candidate_card_id)
    claims = _json_array_for_cli(row["claims_json"])
    source_payload = [
        {
            "source_chunk_id": source["source_chunk_id"],
            "claim_id": source["claim_id"],
            "quote": source["quote"],
        }
        for source in sources
    ]
    payload = {
        "candidate_card_id": row["candidate_card_id"],
        "source_doc_id": row["source_doc_id"],
        "source_revision_id": row["source_revision_id"],
        "title": row["title"],
        "claims": claims,
        "sources": source_payload,
        "model": row["model"],
        "prompt_version": row["prompt_version"],
        "status": row["status"],
        "accepted_note_path": row["accepted_note_path"],
        "document_status": row["document_status"],
        "current_revision_id": row["current_revision_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    _print_key_values(
        {
            "candidate_card_id": row["candidate_card_id"],
            "source_doc_id": row["source_doc_id"],
            "source_revision_id": row["source_revision_id"],
            "title": row["title"],
            "claims_count": len(claims),
            "source_count": len(sources),
            "model": row["model"],
            "prompt_version": row["prompt_version"],
            "status": row["status"],
            "accepted_note_path": row["accepted_note_path"],
            "document_status": row["document_status"],
            "current_revision_id": row["current_revision_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )
    for source in sources:
        console.print(f"source_chunk_id: {source['source_chunk_id']} claim_id: {source['claim_id']}")


@card_app.command("accept")
def card_accept(
    candidate_card_id: str = typer.Argument(..., help="Candidate card ID."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    reviewer: str = typer.Option(
        "cli",
        "--reviewer",
        help="Reviewer identifier for task input metadata.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON results.",
    ),
) -> None:
    """Accept a reviewing candidate card and write an atomic note."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = accept_candidate_card(connection, vault, candidate_card_id, reviewer=reviewer)
        except (FileExistsError, ValueError) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    payload = {
        "candidate_card_id": result.candidate_card_id,
        "task_id": result.task_id,
        "source_doc_id": result.source_doc_id,
        "source_revision_id": result.source_revision_id,
        "status": result.status,
        "accepted_note_path": result.accepted_note_path,
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    console.print(f"Candidate card {result.candidate_card_id}: accepted")
    console.print(f"Task: {result.task_id}")
    console.print(f"Atomic note: {result.accepted_note_path}")


@card_app.command("reject")
def card_reject(
    candidate_card_id: str = typer.Argument(..., help="Candidate card ID."),
    vault: Path = typer.Option(
        Path("."),
        "--vault",
        help="Vault directory.",
    ),
    reason: str | None = typer.Option(
        None,
        "--reason",
        help="Optional rejection reason.",
    ),
    reviewer: str = typer.Option(
        "cli",
        "--reviewer",
        help="Reviewer identifier for task input metadata.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON results.",
    ),
) -> None:
    """Reject a reviewing candidate card without writing an atomic note."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = reject_candidate_card(connection, candidate_card_id, reason=reason, reviewer=reviewer)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    payload = {
        "candidate_card_id": result.candidate_card_id,
        "task_id": result.task_id,
        "source_doc_id": result.source_doc_id,
        "source_revision_id": result.source_revision_id,
        "status": result.status,
        "accepted_note_path": result.accepted_note_path,
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    console.print(f"Candidate card {result.candidate_card_id}: rejected")
    console.print(f"Task: {result.task_id}")


@contextmanager
def _existing_vault_connection(vault_path: Path) -> Iterator[sqlite3.Connection]:
    db_path = vault_path / ".indbase" / "db.sqlite"
    if not db_path.is_file():
        console.print(f"[red]Vault database not found:[/red] {db_path}")
        raise typer.Exit(2)
    connection = connect(db_path)
    try:
        yield connection
    finally:
        connection.close()


def _run_local_ingest(
    vault: Path,
    source_input: Path,
    *,
    recursive: bool,
    ingest_config: CoreIngestConfig | None = None,
) -> None:
    db_path = vault / ".indbase" / "db.sqlite"
    if not db_path.is_file():
        console.print(f"[red]Vault database not found:[/red] {db_path}")
        raise typer.Exit(2)

    try:
        result = run_m3_ingest_pipeline(vault, source_input, recursive=recursive, ingest_config=ingest_config)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    _print_ingest_result(result)


def _run_url_ingest(vault: Path, url: str) -> None:
    db_path = vault / ".indbase" / "db.sqlite"
    if not db_path.is_file():
        console.print(f"[red]Vault database not found:[/red] {db_path}")
        raise typer.Exit(2)

    try:
        result = run_m3_url_ingest_pipeline(vault, url)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    _print_ingest_result(result)


def _run_archive_ingest(vault: Path, archive_path: Path) -> None:
    db_path = vault / ".indbase" / "db.sqlite"
    if not db_path.is_file():
        console.print(f"[red]Vault database not found:[/red] {db_path}")
        raise typer.Exit(2)

    try:
        result = run_m3_archive_ingest_pipeline(vault, archive_path)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    _print_ingest_result(result)


def _print_ingest_result(result) -> None:
    console.print(f"Ingest run: {result.ingest_id}")
    console.print(f"Task: {result.task_id}")
    console.print(f"Status: {result.status}")
    console.print(f"Total: {result.total_items}")
    console.print(f"Revisions written: {result.written_revisions}")
    console.print(f"Unsupported: {result.unsupported_items}")
    console.print(f"Duplicates: {result.duplicate_items}")
    console.print(f"Failed: {result.failed_items}")
    console.print(f"Chunks written: {result.chunked_documents}")
    console.print(f"Indexed documents: {result.indexed_documents}")
    console.print(f"Indexed chunks: {result.indexed_chunks}")
    console.print(f"Index failures: {result.index_failed_documents}")
    console.print(f"Searchable: {_yes_no(result.searchable)}")
    if result.status == "completed_with_issues":
        raise typer.Exit(1)
    if result.status == "failed":
        raise typer.Exit(2)


def _load_vault_config_for_cli(vault: Path):
    config_path = vault / ".indbase" / "config" / "config.toml"
    if not config_path.is_file():
        console.print(f"[red]Vault config not found:[/red] {config_path}")
        raise typer.Exit(2)
    try:
        return load_config(config_path)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


def _require_feature(config, feature_name: str, action: str) -> None:
    if not getattr(config.features, feature_name):
        console.print(f"[red]Source type disabled:[/red] set features.{feature_name} = true to {action}.")
        raise typer.Exit(2)


def _media_ingest_config(config: CoreIngestConfig) -> CoreIngestConfig:
    tier2_extensions = tuple(dict.fromkeys((*config.tier2_extensions, *LOCAL_MEDIA_EXTENSIONS)))
    return replace(config, tier2_extensions=tier2_extensions)


def _fail_unwired_source_type(source_type: str, source_input: str) -> None:
    console.print(
        f"[red]{source_type} ingest is not wired to the indbase durable ingest chain yet.[/red]"
    )
    console.print(f"Source input: {source_input}")
    console.print("No document, revision, chunk, FTS, or citation state was written.")
    raise typer.Exit(2)


def _yes_no(value: object) -> str:
    return "yes" if bool(value) else "no"


def _search_options_for_vault(vault_path: Path, *, top_k: int | None, mode: str = "fts") -> SearchOptions:
    config_path = vault_path / ".indbase" / "config" / "config.toml"
    search_config = load_config(config_path).search if config_path.is_file() else SearchConfig()
    options = SearchOptions.from_config(search_config)
    return SearchOptions(
        top_k=options.top_k if top_k is None else top_k,
        log_queries=options.log_queries,
        persist_search_results=options.persist_search_results,
        cjk_strategy=options.cjk_strategy,
        mode=mode,
    )


def _retrieval_search_options_for_vault(vault_path: Path, *, mode: str = "hybrid") -> SearchOptions:
    config_path = vault_path / ".indbase" / "config" / "config.toml"
    search_config = load_config(config_path).search if config_path.is_file() else SearchConfig()
    options = SearchOptions.from_config(search_config)
    return SearchOptions(
        top_k=options.top_k,
        log_queries=options.log_queries,
        persist_search_results=False,
        cjk_strategy=options.cjk_strategy,
        mode=mode,
    )


def _load_document_for_cli(connection: sqlite3.Connection, doc_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT doc_id, title, status, archived_at, current_revision_id, source_type,
               source_uri, canonical_path, original_path, ingest_status, fts_status,
               quality_status, needs_review, category_id
        FROM documents
        WHERE doc_id = ?
          AND deleted_at IS NULL
        """,
        (doc_id,),
    ).fetchone()


def _resolve_doc_open_target(
    connection: sqlite3.Connection,
    vault: Path,
    doc_id: str,
    *,
    original: bool,
    folder: bool,
    revision_id: str | None,
) -> Path:
    if original and revision_id is not None:
        raise ValueError("--original and --revision cannot be used together.")
    if revision_id is not None:
        row = connection.execute(
            """
            SELECT markdown_path
            FROM document_revisions
            WHERE doc_id = ?
              AND revision_id = ?
              AND deleted_at IS NULL
            """,
            (doc_id, revision_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"Revision not found for document {doc_id}: {revision_id}")
        target = vault / row["markdown_path"]
    else:
        row = _load_document_for_cli(connection, doc_id)
        if row is None:
            raise ValueError(f"Document not found: {doc_id}")
        path_value = row["original_path"] if original else row["canonical_path"]
        path_label = "original_path" if original else "canonical_path"
        if not path_value:
            raise ValueError(f"Document {doc_id} has no {path_label}.")
        target = vault / path_value
    if folder:
        target = target.parent
    target = target.resolve(strict=False)
    if not target.exists():
        raise ValueError(f"Path does not exist: {target}")
    return target


def _print_key_values(values: dict[str, object]) -> None:
    for key, value in values.items():
        console.print(f"{key}: {'' if value is None else value}")


def _json_list_for_cli(value: object) -> list[str]:
    if value is None:
        return []
    parsed = json.loads(str(value))
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _json_array_for_cli(value: object) -> list[object]:
    if value is None:
        return []
    parsed = json.loads(str(value))
    if not isinstance(parsed, list):
        return []
    return parsed


@profile_app.command("build")
def profile_build(
    doc_id: str = typer.Argument(..., help="Document ID."),
    vault: Path = typer.Option(Path("."), "--vault", help="Vault directory."),
) -> None:
    """Build a deterministic profile for the current revision."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = build_document_profile(connection, doc_id)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    console.print(
        f"Profile {result.profile_id} for {result.doc_id} ({result.revision_id}): "
        f"{result.feature_count} features"
    )


@profile_app.command("rebuild")
def profile_rebuild(
    doc_id: str = typer.Argument(..., help="Document ID."),
    vault: Path = typer.Option(Path("."), "--vault", help="Vault directory."),
) -> None:
    """Rebuild the active profile and feature atoms for the current revision."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = build_document_profile(connection, doc_id, rebuild=True)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    console.print(
        f"Rebuilt profile {result.profile_id} for {result.doc_id}: {result.feature_count} features"
    )


@profile_app.command("show")
def profile_show(
    doc_id: str = typer.Argument(..., help="Document ID."),
    vault: Path = typer.Option(Path("."), "--vault", help="Vault directory."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Show the active document profile."""
    with _existing_vault_connection(vault) as connection:
        row = show_document_profile(connection, doc_id)
    if row is None:
        console.print(f"No active profile for document {doc_id}")
        raise typer.Exit(1)
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "profile_id": row["profile_id"],
                    "doc_id": row["doc_id"],
                    "revision_id": row["revision_id"],
                    "profile_version": row["profile_version"],
                    "summary_for_classification": row["summary_for_classification"],
                    "features_json": _json_array_for_cli(row["features_json"]),
                    "status": row["status"],
                    "created_at": row["created_at"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    console.print(f"Profile {row['profile_id']} ({row['status']})")
    console.print(f"Revision: {row['revision_id']}")
    console.print(row["summary_for_classification"] or "")


@profile_app.command("list")
def profile_list(
    vault: Path = typer.Option(Path("."), "--vault", help="Vault directory."),
    stale: bool = typer.Option(False, "--stale", help="List stale profiles only."),
    limit: int = typer.Option(50, "--limit", min=1, help="Maximum rows to show."),
) -> None:
    """List profile records."""
    with _existing_vault_connection(vault) as connection:
        if stale:
            rows = list_stale_profiles(connection, limit=limit)
            title = "Stale Profiles"
        else:
            rows = connection.execute(
                """
                SELECT profile_id, doc_id, revision_id, status, created_at
                FROM document_profiles
                WHERE status = 'active'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            title = "Active Profiles"
    table = Table(title=title)
    table.add_column("Profile")
    table.add_column("Doc")
    table.add_column("Revision")
    if stale:
        table.add_column("Current Revision")
    table.add_column("Updated")
    for row in rows:
        if stale:
            table.add_row(
                row["profile_id"],
                row["doc_id"],
                row["revision_id"],
                row["current_revision_id"] or "",
                row["updated_at"] or "",
            )
        else:
            table.add_row(row["profile_id"], row["doc_id"], row["revision_id"], row["created_at"])
    console.print(table if rows else f"No {title.lower()}")


@taxonomy_app.command("analyze")
def taxonomy_analyze(
    doc_id: str | None = typer.Argument(None, help="Document ID to analyze."),
    vault: Path = typer.Option(Path("."), "--vault", help="Vault directory."),
    all_profiled: bool = typer.Option(
        False,
        "--all-profiled",
        help="Analyze every document with an active current profile.",
    ),
    limit: int = typer.Option(50, "--limit", min=1, help="Maximum documents for --all-profiled."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Map active feature atoms to tag suggestions or candidates."""
    if all_profiled and doc_id is not None:
        console.print("[red]Provide either doc_id or --all-profiled, not both.[/red]")
        raise typer.Exit(1)
    if not all_profiled and doc_id is None:
        console.print("[red]Provide doc_id or --all-profiled.[/red]")
        raise typer.Exit(1)
    with _existing_vault_connection(vault) as connection:
        try:
            if all_profiled:
                run = analyze_all_profiled_documents(connection, limit=limit)
                payload = {
                    "scanned_documents": run.scanned_documents,
                    "results": [
                        {
                            "doc_id": item.doc_id,
                            "revision_id": item.revision_id,
                            "features_scanned": item.features_scanned,
                            "tag_assign_suggestions": item.tag_assign_suggestions,
                            "tag_candidates": item.tag_candidates,
                            "local_keywords": item.local_keywords,
                        }
                        for item in run.results
                    ],
                }
            else:
                result = analyze_document_taxonomy(connection, doc_id or "")
                payload = {
                    "doc_id": result.doc_id,
                    "revision_id": result.revision_id,
                    "features_scanned": result.features_scanned,
                    "tag_assign_suggestions": result.tag_assign_suggestions,
                    "tag_candidates": result.tag_candidates,
                    "local_keywords": result.local_keywords,
                }
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if all_profiled:
        console.print(f"Analyzed {payload['scanned_documents']} profiled documents")
        for item in payload["results"]:
            console.print(
                f"{item['doc_id']}: features={item['features_scanned']} "
                f"suggestions={item['tag_assign_suggestions']} "
                f"candidates={item['tag_candidates']} "
                f"local_keywords={item['local_keywords']}"
            )
        return
    console.print(f"Analyzed {payload['doc_id']} ({payload['revision_id']})")
    console.print(f"Features scanned: {payload['features_scanned']}")
    console.print(f"Tag suggestions: {payload['tag_assign_suggestions']}")
    console.print(f"Tag candidates: {payload['tag_candidates']}")
    console.print(f"Local keywords: {payload['local_keywords']}")


@taxonomy_app.command("audit")
def taxonomy_audit(
    vault: Path = typer.Option(Path("."), "--vault", help="Vault directory."),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON."),
) -> None:
    """Run deterministic taxonomy janitor audit (suggestions only)."""
    with _existing_vault_connection(vault) as connection:
        report = run_taxonomy_audit(connection)
    payload = {
        "findings": [
            {
                "code": finding.code,
                "severity": finding.severity,
                "message": finding.message,
                "suggestion_id": finding.suggestion_id,
            }
            for finding in report.findings
        ],
        "suggestions_created": report.suggestions_created,
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    console.print(f"Janitor findings: {len(report.findings)}")
    console.print(f"Suggestions created: {report.suggestions_created}")
    for finding in report.findings[:30]:
        console.print(f"[{finding.severity}] {finding.code}: {finding.message}")


@taxonomy_app.command("accept-suggestion")
def taxonomy_accept_suggestion(
    suggestion_id: str = typer.Argument(..., help="Taxonomy suggestion ID."),
    vault: Path = typer.Option(Path("."), "--vault", help="Vault directory."),
    force_category: bool = typer.Option(False, "--force-category", help="Force category overwrite."),
    reason: str | None = typer.Option(None, "--reason", help="Resolution note."),
) -> None:
    """Accept a taxonomy suggestion (tag_assign or category_assign)."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = accept_taxonomy_suggestion(
                connection,
                suggestion_id,
                force_category=force_category,
                reason=reason,
            )
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    console.print(f"Accepted {result.suggestion_type} suggestion {result.suggestion_id}")


@taxonomy_app.command("reject-suggestion")
def taxonomy_reject_suggestion(
    suggestion_id: str = typer.Argument(..., help="Taxonomy suggestion ID."),
    vault: Path = typer.Option(Path("."), "--vault", help="Vault directory."),
    reason: str | None = typer.Option(None, "--reason", help="Resolution note."),
) -> None:
    """Reject a taxonomy suggestion."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = reject_taxonomy_suggestion(connection, suggestion_id, reason=reason)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    console.print(f"Rejected suggestion {result.suggestion_id}")


@taxonomy_app.command("tag-candidates")
def taxonomy_tag_candidates(
    vault: Path = typer.Option(Path("."), "--vault", help="Vault directory."),
    status: str = typer.Option("pending", "--status", help="Candidate status filter."),
    limit: int = typer.Option(50, "--limit", min=1),
) -> None:
    """List tag candidates."""
    with _existing_vault_connection(vault) as connection:
        rows = list_tag_candidates(connection, status=status, limit=limit)
    table = Table(title="Tag Candidates")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Status")
    table.add_column("Docs", justify="right")
    for row in rows:
        table.add_row(
            row["candidate_id"],
            row["name"],
            row["type"],
            row["status"],
            str(row["distinct_doc_count"] or 0),
        )
    console.print(table if rows else "No tag candidates")


@taxonomy_app.command("pending-tags")
def taxonomy_pending_tags(
    vault: Path = typer.Option(Path("."), "--vault", help="Vault directory."),
    limit: int = typer.Option(50, "--limit", min=1),
) -> None:
    """List pending tag candidates."""
    taxonomy_tag_candidates(vault=vault, status="pending", limit=limit)


@taxonomy_app.command("promote-tag")
def taxonomy_promote_tag(
    candidate_id: str = typer.Argument(..., help="Tag candidate ID."),
    vault: Path = typer.Option(Path("."), "--vault", help="Vault directory."),
    tag_type: str | None = typer.Option(None, "--type", help="Optional type override on promotion."),
) -> None:
    """Promote a pending tag candidate to a formal tag."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = promote_tag_candidate(connection, candidate_id, tag_type=tag_type)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    console.print(f"Promoted {result.candidate_id} -> tag {result.tag_id} ({result.tag_name})")


@taxonomy_app.command("reject-tag")
def taxonomy_reject_tag(
    candidate_id: str = typer.Argument(..., help="Tag candidate ID."),
    vault: Path = typer.Option(Path("."), "--vault", help="Vault directory."),
) -> None:
    """Reject a pending tag candidate."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = reject_tag_candidate(connection, candidate_id)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    console.print(f"Rejected candidate {result.candidate_id}")


@taxonomy_app.command("add-alias")
def taxonomy_add_alias(
    tag_id: str = typer.Argument(..., help="Target tag ID."),
    alias: str = typer.Argument(..., help="Alias text."),
    vault: Path = typer.Option(Path("."), "--vault", help="Vault directory."),
) -> None:
    """Add an alias to a managed tag."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = add_tag_alias(connection, tag_id, alias)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    console.print(f"Alias {result.detail} added to tag {result.tag_id}")


@taxonomy_app.command("merge-tags")
def taxonomy_merge_tags(
    source_tag_id: str = typer.Argument(..., help="Source tag ID to merge away."),
    target_tag_id: str = typer.Argument(..., help="Target tag ID."),
    vault: Path = typer.Option(Path("."), "--vault", help="Vault directory."),
) -> None:
    """Merge source tag assignments into target and deprecate source."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = merge_tags(connection, source_tag_id, target_tag_id)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    console.print(f"Merged {result.tag_id}: {result.detail}")


@taxonomy_app.command("deprecate-tag")
def taxonomy_deprecate_tag(
    tag_id: str = typer.Argument(..., help="Tag ID."),
    vault: Path = typer.Option(Path("."), "--vault", help="Vault directory."),
) -> None:
    """Deprecate a managed tag."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = deprecate_tag(connection, tag_id)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    console.print(f"Deprecated tag {result.tag_id} ({result.detail})")


@taxonomy_app.command("archive-tag")
def taxonomy_archive_tag(
    tag_id: str = typer.Argument(..., help="Tag ID."),
    vault: Path = typer.Option(Path("."), "--vault", help="Vault directory."),
) -> None:
    """Archive a managed tag."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = archive_tag_mutation(connection, tag_id)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    console.print(f"Archived tag {result.tag_id} ({result.detail})")


app.add_typer(ingest_app, name="ingest")
app.add_typer(catalog_app, name="catalog")
app.add_typer(task_app, name="task")
app.add_typer(index_app, name="index")
app.add_typer(doc_app, name="doc")
app.add_typer(review_app, name="review")
app.add_typer(error_app, name="error")
app.add_typer(tag_app, name="tag")
app.add_typer(ocr_app, name="ocr")
app.add_typer(classification_app, name="classify")
app.add_typer(translation_app, name="translate")
app.add_typer(card_app, name="card")
app.add_typer(profile_app, name="profile")
app.add_typer(taxonomy_app, name="taxonomy")
app.add_typer(retrieval_app, name="retrieval")
eval_app.add_typer(eval_retrieval_app, name="retrieval")
app.add_typer(eval_app, name="eval")
app.add_typer(output_app, name="output")
output_app.add_typer(output_runtime_app, name="runtime")
output_app.add_typer(output_export_app, name="export")


@output_runtime_app.command("install")
def output_runtime_install(
    vault: Path = typer.Option(Path.cwd(), "--vault", help="Vault root path."),
    skip_npm: bool = typer.Option(
        False,
        "--skip-npm",
        help="Install bridge and config only (for tests); does not run npm install.",
    ),
) -> None:
    """Explicitly install the per-vault transition runtime."""
    try:
        result = install_runtime(vault, run_npm_install=not skip_npm)
    except TransitionRuntimeError as exc:
        console.print(f"[red]Runtime install failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"Transition runtime installed under {result.runtime_dir}")
    console.print(f"Pinned commit: {result.transition_commit}")
    if result.previous_run_count:
        console.print(
            f"[yellow]Note:[/yellow] {result.previous_run_count} existing output run(s) "
            "were built with a previous runtime commit."
        )


@output_runtime_app.command("status")
def output_runtime_status(
    vault: Path = typer.Option(Path.cwd(), "--vault", help="Vault root path."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Show transition runtime readiness for a vault."""
    status = runtime_status(vault)
    payload = {
        "vault_path": status.vault_path.as_posix(),
        "transition_output_enabled": status.transition_output_enabled,
        "runtime_dir": status.runtime_dir.as_posix(),
        "bridge_present": status.bridge_present,
        "config_present": status.config_present,
        "node_modules_present": status.node_modules_present,
        "node_available": status.node_available,
        "recorded_commit": status.recorded_commit,
        "existing_output_runs": status.existing_output_runs,
    }
    if json_output:
        console.print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for key, value in payload.items():
        console.print(f"{key}: {value}")


def _parse_export_targets(to: list[str]) -> tuple[str, ...]:
    return tuple(item.lower().removeprefix("--to").strip() for item in to if item.strip())


@output_export_app.command("source")
def output_export_source(
    doc_id: str = typer.Argument(..., help="Source document id."),
    revision_id: str | None = typer.Option(None, "--revision", help="Exact revision to export."),
    vault: Path = typer.Option(Path.cwd(), "--vault", help="Vault root path."),
    to: list[str] = typer.Option([], "--to", help="Optional html, pdf, or docx targets."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Export a source revision through the transition bridge."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = export_source_revision(
                connection,
                vault,
                doc_id=doc_id,
                revision_id_value=revision_id,
                targets=_parse_export_targets(to),
                bridge_runner=None,
            )
        except (TransitionRuntimeError, ValueError) as exc:
            console.print(f"[red]Export failed:[/red] {exc}")
            raise typer.Exit(1) from exc
    payload = {
        "output_run_id": result.output_run_id,
        "task_id": result.task_id,
        "status": result.status,
        "export_dir": result.export_dir,
        "warnings": list(result.warnings),
    }
    if json_output:
        console.print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    console.print(f"Output run: {result.output_run_id}")
    console.print(f"Status: {result.status}")
    if result.export_dir:
        console.print(f"Export dir: {result.export_dir}")
    for warning in result.warnings:
        console.print(f"[yellow]Warning:[/yellow] {warning}")


@output_export_app.command("translation")
def output_export_translation_cmd(
    translation_id: str = typer.Argument(..., help="Translation id."),
    vault: Path = typer.Option(Path.cwd(), "--vault", help="Vault root path."),
    to: list[str] = typer.Option([], "--to", help="Optional html, pdf, or docx targets."),
) -> None:
    """Export a translation output through the transition bridge."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = export_translation(
                connection,
                vault,
                translation_id=translation_id,
                targets=_parse_export_targets(to),
            )
        except (TransitionRuntimeError, ValueError) as exc:
            console.print(f"[red]Export failed:[/red] {exc}")
            raise typer.Exit(1) from exc
    console.print(f"Output run: {result.output_run_id} ({result.status})")


@output_app.command("list")
def output_list(
    vault: Path = typer.Option(Path.cwd(), "--vault", help="Vault root path."),
    status: str | None = typer.Option(None, "--status", help="Filter by output run status."),
    input_kind: str | None = typer.Option(None, "--input-kind", help="Filter by input kind."),
    doc_id: str | None = typer.Option(None, "--doc-id", help="Filter by source doc id."),
    limit: int = typer.Option(50, "--limit", help="Maximum rows to show."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """List transition output runs."""
    with _existing_vault_connection(vault) as connection:
        rows = list_output_runs(
            connection,
            status=status,
            input_kind=input_kind,
            source_doc_id=doc_id,
            limit=limit,
        )
    payload = [
        {
            "output_run_id": row["output_run_id"],
            "task_id": row["task_id"],
            "mode": row["mode"],
            "input_kind": row["input_kind"],
            "input_id": row["input_id"],
            "source_doc_id": row["source_doc_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
        }
        for row in rows
    ]
    if json_output:
        console.print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if not payload:
        console.print("No output runs found.")
        return
    table = Table(title="Output Runs")
    for column in ("output_run_id", "mode", "input_kind", "input_id", "status", "created_at"):
        table.add_column(column)
    for row in payload:
        table.add_row(
            str(row["output_run_id"]),
            str(row["mode"]),
            str(row["input_kind"]),
            str(row["input_id"] or ""),
            str(row["status"]),
            str(row["created_at"]),
        )
    console.print(table)


@output_app.command("show")
def output_show(
    output_run_id: str = typer.Argument(..., help="Output run id."),
    vault: Path = typer.Option(Path.cwd(), "--vault", help="Vault root path."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Show one output run and its artifacts."""
    with _existing_vault_connection(vault) as connection:
        view = get_output_run(connection, output_run_id)
    if view is None:
        console.print(f"[red]Output run not found:[/red] {output_run_id}")
        raise typer.Exit(1)
    payload = {
        "output_run_id": view.output_run_id,
        "task_id": view.task_id,
        "mode": view.mode,
        "input_kind": view.input_kind,
        "input_id": view.input_id,
        "status": view.status,
        "source_doc_id": view.source_doc_id,
        "source_revision_id": view.source_revision_id,
        "created_revision_id": view.created_revision_id,
        "evidence_manifest_path": view.evidence_manifest_path,
        "artifacts": [
            {
                "format": artifact.format,
                "path": artifact.path,
                "status": artifact.status,
                "sha256": artifact.sha256,
            }
            for artifact in view.artifacts
        ],
    }
    if json_output:
        console.print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for key, value in payload.items():
        if key == "artifacts":
            continue
        console.print(f"{key}: {value}")
    console.print("artifacts:")
    for artifact in view.artifacts:
        console.print(f"  - {artifact.format}: {artifact.path} ({artifact.status})")


@output_app.command("open")
def output_open(
    output_run_id: str = typer.Argument(..., help="Output run id."),
    vault: Path = typer.Option(Path.cwd(), "--vault", help="Vault root path."),
    format_name: str = typer.Option(
        "md",
        "--format",
        help="Artifact format: md, html, pdf, or docx.",
    ),
    folder: bool = typer.Option(False, "--folder", help="Open the artifact directory."),
) -> None:
    """Open an output artifact path."""
    import os
    import subprocess
    import sys

    with _existing_vault_connection(vault) as connection:
        try:
            target = resolve_output_artifact_path(
                connection,
                vault,
                output_run_id,
                format_name=format_name.lower(),
            )
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    open_target = target.parent if folder else target
    if sys.platform.startswith("win"):
        os.startfile(open_target)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", str(open_target)], check=False)
    else:
        subprocess.run(["xdg-open", str(open_target)], check=False)
    console.print(str(open_target))


@output_export_app.command("note")
def output_export_note_cmd(
    candidate_card_id: str = typer.Argument(..., help="Accepted candidate_card_id."),
    vault: Path = typer.Option(Path.cwd(), "--vault", help="Vault root path."),
    to: list[str] = typer.Option([], "--to", help="Optional html, pdf, or docx targets."),
) -> None:
    """Export an accepted atomic note through the transition bridge."""
    with _existing_vault_connection(vault) as connection:
        try:
            result = export_accepted_note(
                connection,
                vault,
                candidate_card_id=candidate_card_id,
                targets=_parse_export_targets(to),
            )
        except (TransitionRuntimeError, ValueError) as exc:
            console.print(f"[red]Export failed:[/red] {exc}")
            raise typer.Exit(1) from exc
    console.print(f"Output run: {result.output_run_id} ({result.status})")


@doc_app.command("normalize")
def doc_normalize_replace_current_cmd(
    doc_id: str = typer.Argument(..., help="Active source document id."),
    vault: Path = typer.Option(Path.cwd(), "--vault", help="Vault root path."),
    replace_current: bool = typer.Option(
        True,
        "--replace-current/--no-replace-current",
        help="Create a promoted replacement revision.",
    ),
) -> None:
    """Normalize current source Markdown via transition."""
    if not replace_current:
        console.print("[red]Only --replace-current is supported in v1.[/red]")
        raise typer.Exit(2)
    with _existing_vault_connection(vault) as connection:
        try:
            result = normalize_replace_current(connection, vault, doc_id=doc_id)
        except (TransitionRuntimeError, ValueError) as exc:
            console.print(f"[red]Normalize failed:[/red] {exc}")
            raise typer.Exit(1) from exc
    console.print(f"Normalize run: {result.output_run_id}")
    console.print(f"Status: {result.status}")
    if result.created_revision_id:
        console.print(f"New revision: {result.created_revision_id}")
