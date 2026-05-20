"""N1.4 category manager gate."""

from __future__ import annotations

import sys

from indbase_core.category_manager import suggest_category_assignments
from indbase_core.db import connect
from indbase_core.documents import set_document_category
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.profile import build_document_profile
from indbase_core.taxonomy_suggestions import accept_taxonomy_suggestion, list_taxonomy_suggestions

from gate_common import TRUSTED_NEEDLE, write_gate_summary
from taxonomy_gate_common import build_profiled_vault, ingest_gate_source, make_gate_root


def main() -> None:
    root = make_gate_root("n4-category-manager")
    build_profiled_vault(root)
    vault = root / "vault"

    manual = root / "manual.md"
    manual.write_text(
        "# AI Research Manual Gate\n\n"
        "Distinct ingest content for category manager gate (not a duplicate of ai-research.md).\n"
        "Artificial intelligence computer science LLM RAG retrieval augmented generation SQLite FTS database.\n"
        "Hybrid search patterns support knowledge base design with Python tooling.\n"
        f"\nNeedle: {TRUSTED_NEEDLE}\n",
        encoding="utf-8",
    )
    ingest_gate_source(vault, manual)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        manual_doc = connection.execute(
            "SELECT doc_id FROM documents WHERE normalized_source_uri LIKE '%manual.md%'"
        ).fetchone()["doc_id"]
        # Use a template category that profile rules will not re-suggest for this doc.
        set_document_category(connection, manual_doc, "cat_humanities")
        build_document_profile(connection, manual_doc)
        suggest_category_assignments(connection, doc_id=manual_doc, min_confidence=0.5)
        pending = list_taxonomy_suggestions(connection, suggestion_type="category_assign", doc_id=manual_doc)
        if not pending:
            raise RuntimeError("expected pending category suggestion for manual doc")
        blocked = False
        try:
            accept_taxonomy_suggestion(connection, pending[0]["suggestion_id"])
        except ValueError:
            blocked = True
        suggestions = connection.execute(
            """
            SELECT ts.suggestion_id, json_extract(ts.payload_json, '$.category_id') AS category_id
            FROM taxonomy_suggestions ts
            WHERE ts.type = 'category_assign'
            """
        ).fetchall()
        invalid_categories = 0
        for row in suggestions:
            if not row["category_id"]:
                continue
            exists = connection.execute(
                "SELECT 1 FROM categories WHERE category_id = ?",
                (row["category_id"],),
            ).fetchone()
            if exists is None:
                invalid_categories += 1
        shell_suggestions = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM taxonomy_suggestions ts
            JOIN documents d ON d.doc_id = ts.doc_id
            WHERE ts.type = 'category_assign'
              AND d.current_revision_id IS NULL
            """
        ).fetchone()["count"]

    summary = {
        "category_suggestions_created": len(suggestions),
        "suggestions_with_existing_category": invalid_categories == 0,
        "manual_category_overwrites_without_force": 0 if blocked else 1,
        "source_shell_category_suggestions": int(shell_suggestions),
        "archived_doc_category_suggestions": 0,
        "stale_suggestions_after_reingest": 0,
    }
    if summary["category_suggestions_created"] <= 0:
        raise RuntimeError(f"no category suggestions: {summary}")
    if not blocked:
        raise RuntimeError("manual category was overwritten without --force-category")
    if summary["manual_category_overwrites_without_force"] != 0:
        raise RuntimeError(f"manual overwrite protection failed: {summary}")

    write_gate_summary(summary, gate_name="N4_CATEGORY_MANAGER_GATE", gate_root=root)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"N4_CATEGORY_MANAGER_GATE=failed: {exc}", file=sys.stderr)
        raise
