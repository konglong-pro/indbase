"""TUI-lite guided CLI actions for M4."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from indbase_core.categories import template_names
from indbase_core.config import SearchConfig, load_config
from indbase_core.db import connect
from indbase_core.doctor import run_doctor
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.reviews import list_review_items
from indbase_core.search import SearchOptions, search_chunks
from indbase_core.tasks import list_tasks
from indbase_core.errors import list_errors
from indbase_core.vault import init_vault


TUI_ACTIONS = (
    "dashboard",
    "init",
    "ingest",
    "search",
    "tasks",
    "review",
    "errors",
    "settings",
    "quit",
)


def run_tui_lite(
    *,
    console: Console,
    vault: Path,
    action: str | None = None,
    source_input: Path | None = None,
    query: str | None = None,
    limit: int = 10,
    recursive: bool = False,
    category_template: str = "minimal",
) -> int:
    selected = _normalize_action(action or _prompt_action())
    if selected == "quit":
        console.print("Exiting TUI-lite")
        return 0
    if selected == "init":
        return _run_init(console, vault, category_template=category_template)
    if selected in {"dashboard", "tasks", "review", "errors", "settings", "search", "ingest"}:
        if not _vault_db_path(vault).is_file():
            console.print(f"[red]Vault database not found:[/red] {_vault_db_path(vault)}")
            console.print("Run `indb tui --action init --vault <path>` or `indb init <path>` first.")
            return 2

    if selected == "dashboard":
        return _render_dashboard(console, vault)
    if selected == "tasks":
        return _render_tasks(console, vault, limit=limit)
    if selected == "review":
        return _render_reviews(console, vault, limit=limit)
    if selected == "errors":
        return _render_errors(console, vault, limit=limit)
    if selected == "settings":
        return _render_settings(console, vault)
    if selected == "search":
        return _run_search(console, vault, query=query, limit=limit)
    if selected == "ingest":
        return _run_ingest(console, vault, source_input=source_input, recursive=recursive)

    console.print(f"[red]Unknown TUI-lite action:[/red] {selected}")
    console.print(f"Supported actions: {', '.join(TUI_ACTIONS)}")
    return 2


def _run_init(console: Console, vault: Path, *, category_template: str) -> int:
    if category_template not in template_names():
        console.print(f"[red]Unknown category template:[/red] {category_template}")
        console.print(f"Supported templates: {', '.join(template_names())}")
        return 2
    result = init_vault(vault, category_template=category_template)
    console.print(Panel.fit("TUI-lite Init", style="bold"))
    console.print(f"Vault: {result.vault_path}")
    console.print(f"DB: {result.db_path}")
    console.print(f"Task: {result.task_id}")
    console.print(f"Categories inserted: {result.inserted_categories}")
    return 0


def _render_dashboard(console: Console, vault: Path) -> int:
    with _connect(vault) as connection:
        stats = _dashboard_stats(connection)
    report = run_doctor(vault)

    console.print(Panel.fit("indbase TUI-lite Dashboard", style="bold"))
    table = Table(title="Foundation State")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    for key, value in stats.items():
        table.add_row(key, str(value))
    table.add_row("doctor_exit", str(report.exit_code))
    console.print(table)

    if report.findings:
        findings = Table(title="Doctor Summary")
        findings.add_column("Severity")
        findings.add_column("Code")
        findings.add_column("Message")
        for finding in report.findings[:5]:
            findings.add_row(finding.severity, finding.code, finding.message)
        console.print(findings)
    return 0


def _render_tasks(console: Console, vault: Path, *, limit: int) -> int:
    with _connect(vault) as connection:
        rows = list_tasks(connection, limit=limit)
    table = Table(title="TUI-lite Task Queue")
    table.add_column("Task ID")
    table.add_column("Type")
    table.add_column("Status")
    table.add_column("Created")
    for row in rows:
        table.add_row(row["task_id"], row["type"], row["status"], row["created_at"])
    console.print(table if rows else "No tasks")
    return 0


def _render_reviews(console: Console, vault: Path, *, limit: int) -> int:
    with _connect(vault) as connection:
        rows = list_review_items(connection, status="pending", limit=limit)
    table = Table(title="TUI-lite Review List")
    table.add_column("Review ID")
    table.add_column("Type")
    table.add_column("Target")
    table.add_column("Priority", justify="right")
    table.add_column("Reason")
    for row in rows:
        table.add_row(
            row["review_id"],
            row["type"],
            f"{row['target_type']}:{row['target_id']}",
            str(row["priority"] or ""),
            row["reason"] or "",
        )
    console.print(table if rows else "No pending review items")
    for row in rows:
        console.print(f"review_item: {row['review_id']} type={row['type']} target={row['target_type']}:{row['target_id']}")
    return 0


def _render_errors(console: Console, vault: Path, *, limit: int) -> int:
    with _connect(vault) as connection:
        rows = list_errors(connection, limit=limit)
    table = Table(title="TUI-lite Error Viewer")
    table.add_column("Error ID")
    table.add_column("Component")
    table.add_column("Type")
    table.add_column("Severity")
    table.add_column("Message")
    for row in rows:
        table.add_row(
            row["error_id"],
            row["component"] or "",
            row["error_type"] or "",
            row["severity"] or "",
            row["user_message"] or row["message"] or "",
        )
    console.print(table if rows else "No errors")
    for row in rows:
        console.print(f"error: {row['error_id']} type={row['error_type'] or ''} component={row['component'] or ''}")
    return 0


def _render_settings(console: Console, vault: Path) -> int:
    config_path = vault / ".indbase" / "config" / "config.toml"
    config = load_config(config_path)
    console.print(Panel.fit("TUI-lite Settings Summary", style="bold"))

    table = Table(title="Config")
    table.add_column("Setting")
    table.add_column("Value")
    rows = {
        "vault_path": config.vault_path.as_posix(),
        "default_language": config.default_language,
        "ingest.recursive": config.ingest.recursive,
        "search.top_k": config.search.top_k,
        "search.log_queries": config.search.log_queries,
        "search.persist_search_results": config.search.persist_search_results,
        "search.cjk_strategy": config.search.cjk_strategy,
        "tui.mode": config.tui.mode,
        "features.ocr": config.features.ocr,
        "features.embedding": config.features.embedding,
        "features.ask": config.features.ask,
        "features.translation": config.features.translation,
        "features.candidate_cards": config.features.candidate_cards,
    }
    for key, value in rows.items():
        table.add_row(key, str(value).lower() if isinstance(value, bool) else str(value))
    console.print(table)
    return 0


def _run_search(console: Console, vault: Path, *, query: str | None, limit: int) -> int:
    search_query = query or _prompt_text("Search query")
    if not search_query.strip():
        console.print("[red]Search query must not be empty.[/red]")
        return 2
    options = _search_options_for_vault(vault, top_k=limit)
    with _connect(vault) as connection:
        result = search_chunks(connection, search_query, options=options)

    table = Table(title=f"TUI-lite Search: {result.query_text}")
    table.add_column("Rank", justify="right")
    table.add_column("Doc ID")
    table.add_column("Revision ID")
    table.add_column("Chunk ID")
    table.add_column("Snippet")
    for row in result.results:
        table.add_row(str(row.rank), row.doc_id, row.revision_id, row.chunk_id, row.snippet)
    console.print(table if result.results else "No results")
    return 0


def _run_ingest(
    console: Console,
    vault: Path,
    *,
    source_input: Path | None,
    recursive: bool,
) -> int:
    source = source_input or Path(_prompt_text("Source file or folder"))
    result = run_m3_ingest_pipeline(vault, source, recursive=recursive)
    table = Table(title="TUI-lite Ingest Result")
    table.add_column("Field")
    table.add_column("Value")
    values = {
        "ingest_id": result.ingest_id,
        "task_id": result.task_id,
        "status": result.status,
        "total_items": result.total_items,
        "succeeded_items": result.succeeded_items,
        "failed_items": result.failed_items,
        "unsupported_items": result.unsupported_items,
        "duplicate_items": result.duplicate_items,
        "written_revisions": result.written_revisions,
        "indexed_chunks": result.indexed_chunks,
        "searchable": result.searchable,
    }
    for key, value in values.items():
        table.add_row(key, str(value).lower() if isinstance(value, bool) else str(value))
    console.print(table)
    if result.status == "failed":
        return 2
    if result.status == "completed_with_issues":
        return 1
    return 0


def _dashboard_stats(connection) -> dict[str, int]:
    queries: dict[str, str] = {
        "documents_total": "SELECT COUNT(*) FROM documents WHERE deleted_at IS NULL",
        "documents_active": "SELECT COUNT(*) FROM documents WHERE status = 'active' AND deleted_at IS NULL",
        "documents_archived": "SELECT COUNT(*) FROM documents WHERE status = 'archived' AND deleted_at IS NULL",
        "revisions": "SELECT COUNT(*) FROM document_revisions WHERE deleted_at IS NULL",
        "current_chunks": "SELECT COUNT(*) FROM chunks WHERE is_current = 1 AND deleted_at IS NULL",
        "fts_rows": "SELECT COUNT(*) FROM chunks_fts",
        "pending_reviews": "SELECT COUNT(*) FROM review_items WHERE status = 'pending'",
        "errors": "SELECT COUNT(*) FROM errors",
        "tasks": "SELECT COUNT(*) FROM tasks",
    }
    return {key: int(connection.execute(query).fetchone()[0] or 0) for key, query in queries.items()}


def _search_options_for_vault(vault_path: Path, *, top_k: int) -> SearchOptions:
    config_path = vault_path / ".indbase" / "config" / "config.toml"
    search_config = load_config(config_path).search if config_path.is_file() else SearchConfig()
    return SearchOptions(
        top_k=top_k,
        log_queries=search_config.log_queries,
        persist_search_results=search_config.persist_search_results,
        cjk_strategy=search_config.cjk_strategy,
    )


def _prompt_action() -> str:
    return _inquirer_select(
        message="Choose an indbase action",
        choices=list(TUI_ACTIONS),
    )


def _prompt_text(message: str) -> str:
    from InquirerPy import inquirer

    return str(inquirer.text(message=message).execute())


def _inquirer_select(*, message: str, choices: list[str]) -> str:
    from InquirerPy import inquirer

    return str(inquirer.select(message=message, choices=choices).execute())


def _normalize_action(action: str) -> str:
    normalized = action.strip().lower().replace("-", "_")
    aliases = {
        "task": "tasks",
        "task_queue": "tasks",
        "queue": "tasks",
        "ingest_wizard": "ingest",
        "search_panel": "search",
        "reviews": "review",
        "review_list": "review",
        "error": "errors",
        "error_viewer": "errors",
        "setting": "settings",
        "settings_summary": "settings",
        "config": "settings",
    }
    return aliases.get(normalized, normalized)


def _vault_db_path(vault: Path) -> Path:
    return vault / ".indbase" / "db.sqlite"


@contextmanager
def _connect(vault: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(_vault_db_path(vault))
    try:
        yield connection
    finally:
        connection.close()
