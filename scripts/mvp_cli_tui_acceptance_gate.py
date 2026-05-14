"""Run a manual-style CLI and TUI-lite acceptance pass."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3

from mvp_closeout_common import ROOT, run_indb


def main() -> None:
    root = ROOT / ".tmp" / f"mvp-cli-tui-acceptance-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    root.mkdir(parents=True)
    vault = root / "vault"
    sources = root / "sources"
    sources.mkdir()
    (sources / "workflow.md").write_text(
        "# CLI Workflow\nindbase MVP doctor SQLite hybrid search candidate card translation workflow.\n",
        encoding="utf-8",
    )
    (sources / "cjk.md").write_text("# CJK\n大语言模型 知识数据库 幻觉控制\n", encoding="utf-8")
    (sources / "empty.txt").write_text("", encoding="utf-8")
    (sources / "unsupported.png").write_text("unsupported", encoding="utf-8")

    commands_run: list[str] = []

    def run(expected: int | set[int], *args: str):
        commands_run.append("indb " + " ".join(args))
        return run_indb(expected, *args)

    run(0, "init", str(vault))
    run({0, 1}, "ingest", str(sources), "--recursive", "--vault", str(vault))
    run(0, "index", "status", "--vault", str(vault))
    run(0, "index", "rebuild", "--fts", "--vault", str(vault))
    run(0, "index", "rebuild", "--vectors", "--vault", str(vault))
    run(0, "search", "doctor SQLite", "--vault", str(vault))
    run(0, "search", "doctor SQLite", "--mode", "vector", "--vault", str(vault))
    run(0, "search", "doctor SQLite", "--mode", "hybrid", "--vault", str(vault))
    run(0, "search", "大语言模型", "--vault", str(vault))

    doc = _workflow_document(vault)
    run(0, "catalog", "list", "--vault", str(vault))
    run(0, "catalog", "add", "Acceptance", "--vault", str(vault))
    category_id = _scalar(vault, "SELECT category_id FROM categories WHERE name = 'Acceptance'")
    run(0, "catalog", "update", str(category_id), "--name", "Acceptance Updated", "--vault", str(vault))
    run(0, "catalog", "archive", str(category_id), "--vault", str(vault))
    run(0, "catalog", "restore", str(category_id), "--vault", str(vault))
    run(0, "doc", "list", "--vault", str(vault))
    run(0, "doc", "show", doc["doc_id"], "--vault", str(vault))
    run(0, "doc", "revisions", doc["doc_id"], "--vault", str(vault))
    run(0, "doc", "open", doc["doc_id"], "--vault", str(vault), "--print-path")
    run(0, "doc", "open", doc["doc_id"], "--vault", str(vault), "--original", "--print-path")
    run(0, "doc", "set-category", doc["doc_id"], str(category_id), "--vault", str(vault))
    run(0, "tag", "list", "--vault", str(vault))
    run(0, "tag", "add", "acceptance-tag", "--vault", str(vault))
    tag_id = _scalar(vault, "SELECT tag_id FROM tags WHERE name = 'acceptance-tag'")
    run(0, "tag", "update", str(tag_id), "--description", "acceptance gate", "--vault", str(vault))
    run(0, "doc", "add-tag", doc["doc_id"], "acceptance-tag", "--vault", str(vault))
    run(0, "doc", "tags", doc["doc_id"], "--vault", str(vault))
    run(0, "doc", "remove-tag", doc["doc_id"], "acceptance-tag", "--vault", str(vault))
    run(0, "tag", "archive", str(tag_id), "--vault", str(vault))
    run(0, "tag", "restore", str(tag_id), "--vault", str(vault))
    run(0, "doc", "archive", doc["doc_id"], "--vault", str(vault))
    run(0, "doc", "restore", doc["doc_id"], "--vault", str(vault))

    classify = json.loads(
        run(
            0,
            "classify",
            "suggest",
            "--vault",
            str(vault),
            "--min-confidence",
            "0.1",
            "--json",
        ).stdout
    )
    suggestions = json.loads(run(0, "classify", "list", "--vault", str(vault), "--json").stdout)["suggestions"]
    if suggestions:
        first = suggestions[0]["suggestion_id"]
        run(0, "classify", "show", first, "--vault", str(vault))
        run(0, "classify", "accept", first, "--vault", str(vault), "--json")
    if len(suggestions) > 1:
        run(0, "classify", "reject", suggestions[1]["suggestion_id"], "--vault", str(vault), "--json")

    selected_translation = json.loads(
        run(
            0,
            "translate",
            "chunks",
            doc["doc_id"],
            "--revision",
            doc["revision_id"],
            "--chunk",
            doc["chunk_id"],
            "--target-language",
            "zh",
            "--vault",
            str(vault),
            "--json",
        ).stdout
    )
    run(
        0,
        "translate",
        "document",
        doc["doc_id"],
        "--revision",
        doc["revision_id"],
        "--target-language",
        "ja",
        "--vault",
        str(vault),
        "--json",
    )
    run(0, "translate", "list", "--vault", str(vault), "--json")
    run(0, "translate", "show", selected_translation["translation_id"], "--vault", str(vault))
    run(0, "translate", "open", selected_translation["translation_id"], "--vault", str(vault), "--print-path")

    accepted_card = json.loads(run(0, "card", "generate", doc["doc_id"], "--vault", str(vault), "--json").stdout)
    run(0, "card", "list", "--vault", str(vault), "--json")
    run(0, "card", "show", accepted_card["candidate_card_id"], "--vault", str(vault), "--json")
    run(0, "card", "accept", accepted_card["candidate_card_id"], "--vault", str(vault), "--json")
    rejected_card = json.loads(run(0, "card", "generate", doc["doc_id"], "--vault", str(vault), "--json").stdout)
    run(0, "card", "reject", rejected_card["candidate_card_id"], "--vault", str(vault), "--json")

    run(0, "review", "list", "--vault", str(vault))
    review_id = _optional_scalar(vault, "SELECT review_id FROM review_items ORDER BY created_at LIMIT 1")
    if review_id:
        run(0, "review", "show", str(review_id), "--vault", str(vault))
        run(0, "review", "resolve", str(review_id), "--note", "acceptance reviewed", "--resolved-by", "gate", "--vault", str(vault))
    run(0, "error", "list", "--vault", str(vault))
    error_id = _optional_scalar(vault, "SELECT error_id FROM errors ORDER BY created_at LIMIT 1")
    if error_id:
        run(0, "error", "show", str(error_id), "--vault", str(vault))
    run(0, "task", "list", "--vault", str(vault))
    task_id = _scalar(vault, "SELECT task_id FROM tasks ORDER BY created_at DESC LIMIT 1")
    run(0, "task", "show", str(task_id), "--vault", str(vault))

    extra = root / "extra-source.md"
    extra.write_text("# TUI Extra\nTUI acceptance ingest workflow.\n", encoding="utf-8")
    run(0, "tui", "--action", "dashboard", "--vault", str(vault))
    run(0, "tui", "--action", "search-panel", "--query", "TUI acceptance", "--vault", str(vault))
    run(0, "tui", "--action", "task-queue", "--vault", str(vault))
    run(0, "tui", "--action", "review-list", "--vault", str(vault))
    run(0, "tui", "--action", "error-viewer", "--vault", str(vault))
    run(0, "tui", "--action", "settings-summary", "--vault", str(vault))
    run(0, "tui", "--action", "ingest-wizard", "--source", str(extra), "--vault", str(vault))

    doctor = json.loads(run({0, 1}, "doctor", "--vault", str(vault), "--json").stdout)
    summary = {
        **_counts(vault),
        "commands_run": len(commands_run),
        "cli_core_commands_run": sum(1 for command in commands_run if not command.startswith("indb tui")),
        "tui_actions_run": sum(1 for command in commands_run if command.startswith("indb tui")),
        "classification_scanned_documents": classify["scanned_documents"],
        "critical_doctor_findings": sum(
            1 for finding in doctor["findings"] if finding["severity"] in {"error", "critical"}
        ),
        "doctor_exit_code": doctor["exit_code"],
    }
    if summary["commands_run"] < 50 or summary["tui_actions_run"] < 7:
        raise RuntimeError(f"acceptance command coverage too small: {summary}")
    if summary["critical_doctor_findings"] != 0:
        raise RuntimeError(f"acceptance doctor has critical findings: {doctor}")
    (root / "acceptance_commands.txt").write_text("\n".join(commands_run), encoding="utf-8")
    (root / "cli_tui_acceptance_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    print("MVP_CLI_TUI_ACCEPTANCE_GATE=passed")
    print(f"DOGFOOD_ROOT={root}")


def _workflow_document(vault: Path) -> dict[str, str]:
    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT d.doc_id, d.current_revision_id AS revision_id, c.chunk_id
            FROM documents d
            JOIN chunks c ON c.doc_id = d.doc_id
             AND c.revision_id = d.current_revision_id
             AND c.is_current = 1
            WHERE d.normalized_source_uri LIKE '%workflow.md'
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise RuntimeError("workflow document not found")
    return {"doc_id": str(row["doc_id"]), "revision_id": str(row["revision_id"]), "chunk_id": str(row["chunk_id"])}


def _counts(vault: Path) -> dict[str, int]:
    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        return {
            "documents": _count(connection, "SELECT COUNT(*) FROM documents WHERE deleted_at IS NULL"),
            "chunks": _count(connection, "SELECT COUNT(*) FROM chunks WHERE deleted_at IS NULL"),
            "fts_rows": _count(connection, "SELECT COUNT(*) FROM chunks_fts"),
            "embeddings": _count(connection, "SELECT COUNT(*) FROM embeddings WHERE deleted_at IS NULL"),
            "translations": _count(connection, "SELECT COUNT(*) FROM translations WHERE deleted_at IS NULL"),
            "accepted_cards": _count(connection, "SELECT COUNT(*) FROM candidate_cards WHERE status = 'accepted'"),
            "rejected_cards": _count(connection, "SELECT COUNT(*) FROM candidate_cards WHERE status = 'rejected'"),
            "tasks": _count(connection, "SELECT COUNT(*) FROM tasks"),
            "reviews": _count(connection, "SELECT COUNT(*) FROM review_items"),
            "errors": _count(connection, "SELECT COUNT(*) FROM errors"),
        }


def _scalar(vault: Path, query: str) -> object:
    value = _optional_scalar(vault, query)
    if value is None:
        raise RuntimeError(f"query returned no rows: {query}")
    return value


def _optional_scalar(vault: Path, query: str) -> object | None:
    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        row = connection.execute(query).fetchone()
    return None if row is None else row[0]


def _count(connection: sqlite3.Connection, query: str) -> int:
    return int(connection.execute(query).fetchone()[0] or 0)


if __name__ == "__main__":
    main()
