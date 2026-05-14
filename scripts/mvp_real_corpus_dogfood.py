"""Run a repo-local or user-provided real corpus dogfood workflow."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import sqlite3

from mvp_closeout_common import ROOT, directory_size, run_indb, stage_real_corpus


def main() -> None:
    root = ROOT / ".tmp" / f"mvp-real-corpus-dogfood-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    root.mkdir(parents=True)
    corpus = stage_real_corpus(root, target_files=85)
    sources = Path(str(corpus["sources"]))
    vault = root / "vault"

    run_indb(0, "init", str(vault))
    run_indb({0, 1}, "ingest", str(sources), "--recursive", "--vault", str(vault))
    run_indb(0, "index", "rebuild", "--fts", "--vault", str(vault))
    run_indb(0, "index", "rebuild", "--vectors", "--vault", str(vault))

    doc = _select_workflow_document(vault)
    search_metrics = _run_searches(vault)
    run_indb(0, "catalog", "add", "Real Corpus", "--vault", str(vault))
    category_id = _scalar(vault, "SELECT category_id FROM categories WHERE name = 'Real Corpus'")
    run_indb(0, "doc", "set-category", doc["doc_id"], str(category_id), "--vault", str(vault))
    run_indb(0, "doc", "add-tag", doc["doc_id"], "real-dogfood", "--vault", str(vault))

    classify = json.loads(
        run_indb(
            0,
            "classify",
            "suggest",
            "--vault",
            str(vault),
            "--min-confidence",
            "0.1",
            "--limit",
            "20",
            "--json",
        ).stdout
    )
    suggestions = json.loads(run_indb(0, "classify", "list", "--vault", str(vault), "--json").stdout)[
        "suggestions"
    ]
    if suggestions:
        run_indb(0, "classify", "accept", suggestions[0]["suggestion_id"], "--vault", str(vault), "--json")
    if len(suggestions) > 1:
        run_indb(0, "classify", "reject", suggestions[1]["suggestion_id"], "--vault", str(vault), "--json")

    selected_translation = json.loads(
        run_indb(
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
    full_translation = json.loads(
        run_indb(
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
        ).stdout
    )
    accepted_card = json.loads(
        run_indb(0, "card", "generate", doc["doc_id"], "--vault", str(vault), "--json").stdout
    )
    accepted = json.loads(
        run_indb(0, "card", "accept", accepted_card["candidate_card_id"], "--vault", str(vault), "--json").stdout
    )
    rejected_card = json.loads(
        run_indb(0, "card", "generate", doc["doc_id"], "--vault", str(vault), "--json").stdout
    )
    run_indb(0, "card", "reject", rejected_card["candidate_card_id"], "--vault", str(vault), "--json")

    doc_markdown_path = _printed_path(run_indb(0, "doc", "open", doc["doc_id"], "--vault", str(vault), "--print-path").stdout)
    doc_original_path = _printed_path(
        run_indb(0, "doc", "open", doc["doc_id"], "--vault", str(vault), "--original", "--print-path").stdout
    )
    translation_path = _printed_path(
        run_indb(
            0,
            "translate",
            "open",
            selected_translation["translation_id"],
            "--vault",
            str(vault),
            "--print-path",
        ).stdout
    )
    accepted_note = vault / accepted["accepted_note_path"]
    _require_file(doc_markdown_path)
    _require_file(doc_original_path)
    _require_file(translation_path)
    _require_file(accepted_note)

    doctor_report = json.loads(run_indb({0, 1}, "doctor", "--vault", str(vault), "--json").stdout)
    summary = {
        **{key: value for key, value in corpus.items() if key != "sources"},
        **_db_counts(vault),
        **search_metrics,
        "classification_scanned_documents": classify["scanned_documents"],
        "classification_suggested_documents": classify["suggested_documents"],
        "selected_translation_id": selected_translation["translation_id"],
        "full_translation_id": full_translation["translation_id"],
        "accepted_card_id": accepted["candidate_card_id"],
        "accepted_note_exists": int(accepted_note.is_file()),
        "doc_open_markdown_exists": int(doc_markdown_path.is_file()),
        "doc_open_original_exists": int(doc_original_path.is_file()),
        "translation_open_exists": int(translation_path.is_file()),
        "critical_doctor_findings": _critical_findings(doctor_report),
        "doctor_exit_code": doctor_report["exit_code"],
        "db_size_bytes": (vault / ".indbase" / "db.sqlite").stat().st_size,
        "vault_size_bytes": directory_size(vault),
    }
    _assert_hard_metrics(summary)
    (root / "real_corpus_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    print("MVP_REAL_CORPUS_DOGFOOD=passed")
    print(f"DOGFOOD_ROOT={root}")


def _run_searches(vault: Path) -> dict[str, int]:
    queries = _search_queries_from_chunks(vault)
    metrics: dict[str, int] = {"search_failures": 0}
    for mode in ("fts", "hybrid", "vector"):
        total = 0
        missing = 0
        for query in queries:
            payload = json.loads(
                run_indb(0, "search", query, "--mode", mode, "--vault", str(vault), "--json").stdout
            )
            total += int(payload["result_count"])
            if int(payload["result_count"]) <= 0:
                missing += 1
        metrics[f"search_{mode}_results"] = total
        metrics[f"search_{mode}_missing_queries"] = missing
        metrics["search_failures"] += missing
    return metrics


def _search_queries_from_chunks(vault: Path) -> tuple[str, ...]:
    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        rows = connection.execute(
            """
            SELECT c.text
            FROM chunks c
            JOIN documents d ON d.doc_id = c.doc_id
            WHERE d.status = 'active'
              AND d.current_revision_id = c.revision_id
              AND c.is_current = 1
              AND c.deleted_at IS NULL
            ORDER BY d.created_at, d.doc_id, c.sequence
            LIMIT 25
            """
        ).fetchall()
    tokens: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", str(row[0] or "")):
            normalized = token.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            tokens.append(token)
            if len(tokens) >= 5:
                return tuple(tokens)
    if not tokens:
        raise RuntimeError("real corpus dogfood could not derive search queries from current chunks")
    return tuple(tokens)


def _select_workflow_document(vault: Path) -> dict[str, str]:
    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT d.doc_id, d.current_revision_id AS revision_id, c.chunk_id
            FROM documents d
            JOIN chunks c
              ON c.doc_id = d.doc_id
             AND c.revision_id = d.current_revision_id
             AND c.is_current = 1
             AND c.deleted_at IS NULL
            WHERE d.status = 'active'
              AND d.deleted_at IS NULL
              AND d.current_revision_id IS NOT NULL
            ORDER BY
              CASE WHEN d.title LIKE '%README%' THEN 0 ELSE 1 END,
              d.created_at,
              d.doc_id,
              c.sequence
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise RuntimeError("no active current document with chunks available for real dogfood workflow")
    return {"doc_id": str(row["doc_id"]), "revision_id": str(row["revision_id"]), "chunk_id": str(row["chunk_id"])}


def _db_counts(vault: Path) -> dict[str, int]:
    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        return {
            "documents": _count(connection, "SELECT COUNT(*) FROM documents WHERE deleted_at IS NULL"),
            "active_documents": _count(connection, "SELECT COUNT(*) FROM documents WHERE status = 'active' AND deleted_at IS NULL"),
            "source_shells": _count(connection, "SELECT COUNT(*) FROM documents WHERE current_revision_id IS NULL AND deleted_at IS NULL"),
            "chunks": _count(connection, "SELECT COUNT(*) FROM chunks WHERE deleted_at IS NULL"),
            "current_chunks": _count(connection, "SELECT COUNT(*) FROM chunks WHERE is_current = 1 AND deleted_at IS NULL"),
            "fts_rows": _count(connection, "SELECT COUNT(*) FROM chunks_fts"),
            "embeddings": _count(connection, "SELECT COUNT(*) FROM embeddings WHERE deleted_at IS NULL"),
            "translations": _count(connection, "SELECT COUNT(*) FROM translations WHERE deleted_at IS NULL"),
            "candidate_cards": _count(connection, "SELECT COUNT(*) FROM candidate_cards WHERE deleted_at IS NULL"),
            "accepted_candidate_cards": _count(connection, "SELECT COUNT(*) FROM candidate_cards WHERE status = 'accepted' AND deleted_at IS NULL"),
            "rejected_candidate_cards": _count(connection, "SELECT COUNT(*) FROM candidate_cards WHERE status = 'rejected' AND deleted_at IS NULL"),
            "accepted_atomic_notes": len(list((vault / "notes" / "atomic").rglob("*.md"))),
            "reviews": _count(connection, "SELECT COUNT(*) FROM review_items"),
            "errors": _count(connection, "SELECT COUNT(*) FROM errors"),
            "tasks": _count(connection, "SELECT COUNT(*) FROM tasks"),
            "zero_chunk_current_revisions": _count(
                connection,
                """
                SELECT COUNT(*)
                FROM documents d
                WHERE d.current_revision_id IS NOT NULL
                  AND d.deleted_at IS NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM chunks c
                    WHERE c.doc_id = d.doc_id
                      AND c.revision_id = d.current_revision_id
                      AND c.is_current = 1
                      AND c.deleted_at IS NULL
                  )
                """,
            ),
            "source_shells_searchable": _count(
                connection,
                """
                SELECT COUNT(*)
                FROM documents d
                WHERE d.current_revision_id IS NULL
                  AND d.deleted_at IS NULL
                  AND EXISTS (
                    SELECT 1 FROM chunks_fts f WHERE f.doc_id = d.doc_id
                  )
                """,
            ),
        }


def _scalar(vault: Path, query: str) -> object:
    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        row = connection.execute(query).fetchone()
    if row is None:
        raise RuntimeError(f"query returned no rows: {query}")
    return row[0]


def _count(connection: sqlite3.Connection, query: str) -> int:
    return int(connection.execute(query).fetchone()[0] or 0)


def _critical_findings(report: dict[str, object]) -> int:
    return sum(1 for finding in report["findings"] if finding["severity"] in {"error", "critical"})


def _require_file(path: Path) -> None:
    if not path.is_file():
        raise RuntimeError(f"expected file does not exist: {path}")


def _printed_path(stdout: str) -> Path:
    return Path("".join(line.strip() for line in stdout.splitlines() if line.strip()))


def _assert_hard_metrics(summary: dict[str, object]) -> None:
    required_zero = {
        "critical_doctor_findings": summary["critical_doctor_findings"],
        "zero_chunk_current_revisions": summary["zero_chunk_current_revisions"],
        "source_shells_searchable": summary["source_shells_searchable"],
        "search_failures": summary["search_failures"],
    }
    if any(int(value) != 0 for value in required_zero.values()):
        raise RuntimeError(f"real corpus dogfood hard metrics failed: {required_zero}")
    required_positive = {
        "documents": summary["documents"],
        "current_chunks": summary["current_chunks"],
        "fts_rows": summary["fts_rows"],
        "embeddings": summary["embeddings"],
        "translations": summary["translations"],
        "accepted_candidate_cards": summary["accepted_candidate_cards"],
        "rejected_candidate_cards": summary["rejected_candidate_cards"],
        "accepted_atomic_notes": summary["accepted_atomic_notes"],
        "accepted_note_exists": summary["accepted_note_exists"],
    }
    if any(int(value) <= 0 for value in required_positive.values()):
        raise RuntimeError(f"real corpus dogfood positive metrics failed: {required_positive}")


if __name__ == "__main__":
    main()
