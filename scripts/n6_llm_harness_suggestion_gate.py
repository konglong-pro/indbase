"""N1.6 LLM harness suggestion gate (fake provider only)."""

from __future__ import annotations

import sys

from indbase_core.db import connect
from indbase_core.llm_harness import LLMHarness, assert_no_direct_provider_usage
from gate_common import write_gate_summary
from taxonomy_gate_common import build_profiled_vault, make_gate_root


def main() -> None:
    root = make_gate_root("n6-llm-harness")
    data = build_profiled_vault(root)
    vault = root / "vault"

    violations = assert_no_direct_provider_usage()
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc_id = data["doc_id"]
        revision_id = connection.execute(
            "SELECT current_revision_id FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()["current_revision_id"]
        chunks = connection.execute(
            """
            SELECT chunk_id, text
            FROM chunks
            WHERE doc_id = ?
              AND revision_id = ?
              AND is_current = 1
            """,
            (doc_id, revision_id),
        ).fetchall()
        chunk_quotes = {str(row["chunk_id"]): str(row["text"]) for row in chunks}
        category_id = connection.execute(
            """
            SELECT category_id
            FROM categories
            WHERE category_id != 'cat_uncategorized'
            LIMIT 1
            """
        ).fetchone()["category_id"]

        harness = LLMHarness(connection, vault_path=vault)
        valid = harness.call_taxonomy_arbitration(
            prompt_name="taxonomy.category_arbitration",
            doc_id=doc_id,
            revision_id=str(revision_id),
            input_payload={
                "doc_id": doc_id,
                "default_category_id": category_id,
                "chunk_quotes": chunk_quotes,
            },
            scenario="valid_category",
        )
        invalid = harness.call_taxonomy_arbitration(
            prompt_name="taxonomy.category_arbitration",
            doc_id=doc_id,
            revision_id=str(revision_id),
            input_payload={"doc_id": doc_id, "chunk_quotes": chunk_quotes},
            scenario="invalid_schema",
        )
        timeout = harness.call_taxonomy_arbitration(
            prompt_name="taxonomy.category_arbitration",
            doc_id=doc_id,
            revision_id=str(revision_id),
            input_payload={"doc_id": doc_id, "chunk_quotes": chunk_quotes},
            scenario="timeout",
        )
        quote_invalid = harness.call_taxonomy_arbitration(
            prompt_name="taxonomy.tag_arbitration",
            doc_id=doc_id,
            revision_id=str(revision_id),
            input_payload={"doc_id": doc_id, "chunk_quotes": chunk_quotes, "feature_text": "rag"},
            scenario="invalid_quote",
        )
        model_calls = connection.execute("SELECT COUNT(*) AS count FROM model_calls").fetchone()["count"]
        rejected = connection.execute(
            "SELECT COUNT(*) AS count FROM model_calls WHERE status IN ('rejected', 'timeout')"
        ).fetchone()["count"]
        llm_suggestions = connection.execute(
            "SELECT COUNT(*) AS count FROM taxonomy_suggestions WHERE source = 'llm'"
        ).fetchone()["count"]
        direct_tag_writes = connection.execute(
            "SELECT COUNT(*) AS count FROM document_tags WHERE source = 'llm_suggestion'"
        ).fetchone()["count"]

    summary = {
        "model_calls_logged": int(model_calls),
        "schema_invalid_outputs_rejected": int(invalid.rejected),
        "provider_timeouts_record_errors": int(timeout.rejected),
        "valid_suggestion_ids": len(valid.suggestion_ids),
        "quote_invalid_rejected": int(quote_invalid.rejected),
        "business_direct_provider_calls": len(violations),
        "llm_outputs_without_schema": 0 if invalid.rejected else 1,
        "llm_taxonomy_mutations_without_manager": int(direct_tag_writes),
        "llm_suggestions_created": int(llm_suggestions),
    }
    if summary["model_calls_logged"] <= 0:
        raise RuntimeError(f"model calls not logged: {summary}")
    if summary["schema_invalid_outputs_rejected"] <= 0:
        raise RuntimeError(f"invalid schema not rejected: {summary}")
    if summary["provider_timeouts_record_errors"] <= 0:
        raise RuntimeError(f"timeout not recorded: {summary}")
    if summary["business_direct_provider_calls"] != 0:
        raise RuntimeError(f"business modules call providers directly: {violations}")
    if summary["llm_taxonomy_mutations_without_manager"] != 0:
        raise RuntimeError(f"LLM wrote document_tags directly: {summary}")

    write_gate_summary(summary, gate_name="N6_LLM_HARNESS_SUGGESTION_GATE", gate_root=root)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"N6_LLM_HARNESS_SUGGESTION_GATE=failed: {exc}", file=sys.stderr)
        raise
