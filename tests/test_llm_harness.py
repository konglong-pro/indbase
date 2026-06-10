from pathlib import Path

import pytest

from indbase_core.db import connect
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.llm_harness import LLMHarness, assert_no_direct_provider_usage
from indbase_core.llm_harness.schemas import validate_taxonomy_arbitration_output
from indbase_core.llm_harness.errors import HarnessValidationError
from indbase_core.vault import init_vault


def test_validate_taxonomy_output_rejects_invalid_decision() -> None:
    with pytest.raises(HarnessValidationError):
        validate_taxonomy_arbitration_output({"decision": "write_tags_directly", "confidence": 0.5, "reason": "x"})


def test_harness_logs_model_calls_and_rejects_invalid_schema(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "note.md"
    source.write_text("# Note\nSQLite FTS and hybrid search in this document body.\n", encoding="utf-8")
    init_vault(vault, category_template="indbase_default_v1")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        revision_id = connection.execute(
            "SELECT current_revision_id FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()["current_revision_id"]
        chunks = connection.execute(
            "SELECT chunk_id, text FROM chunks WHERE doc_id = ? AND revision_id = ?",
            (doc_id, revision_id),
        ).fetchall()
        chunk_quotes = {str(row["chunk_id"]): str(row["text"]) for row in chunks}
        harness = LLMHarness(connection, vault_path=vault)
        valid = harness.call_taxonomy_arbitration(
            prompt_name="taxonomy.category_arbitration",
            doc_id=doc_id,
            revision_id=str(revision_id),
            input_payload={"doc_id": doc_id, "default_category_id": "cat_uncategorized", "chunk_quotes": chunk_quotes},
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
        calls = connection.execute("SELECT status FROM model_calls").fetchall()
        direct_assignments = connection.execute(
            "SELECT COUNT(*) AS count FROM document_tags WHERE source = 'llm_suggestion'"
        ).fetchone()["count"]

    assert valid.status == "completed"
    assert len(valid.suggestion_ids) >= 0
    assert invalid.rejected
    assert timeout.rejected
    assert len(calls) >= 3
    assert direct_assignments == 0
    assert assert_no_direct_provider_usage() == []


def test_harness_rejects_quote_not_in_chunk(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault, category_template="indbase_default_v1")
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        now = "2026-05-20T00:00:00+00:00"
        connection.execute(
            """
            INSERT INTO documents(
              doc_id, current_revision_id, status, ingest_status, created_at
            )
            VALUES ('doc_20260520_ab12cd', 'rev_doc_20260520_ab12cd_0001', 'active', 'revisioned', ?)
            """,
            (now,),
        )
        connection.execute(
            """
            INSERT INTO document_revisions(
              revision_id, doc_id, sequence, markdown_path, content_hash, created_at
            )
            VALUES (
              'rev_doc_20260520_ab12cd_0001', 'doc_20260520_ab12cd', 1,
              'sources/2026/05/note.md', 'hash', ?
            )
            """,
            (now,),
        )
        connection.execute(
            """
            INSERT INTO chunks(
              chunk_id, doc_id, revision_id, sequence, text, is_current, created_at
            )
            VALUES (
              'chunk_rev_doc_20260520_ab12cd_0001_0001',
              'doc_20260520_ab12cd',
              'rev_doc_20260520_ab12cd_0001',
              1,
              'real chunk text only',
              1,
              ?
            )
            """,
            (now,),
        )
        connection.commit()
        harness = LLMHarness(connection, vault_path=vault)
        result = harness.call_taxonomy_arbitration(
            prompt_name="taxonomy.tag_arbitration",
            doc_id="doc_20260520_ab12cd",
            revision_id="rev_doc_20260520_ab12cd_0001",
            input_payload={
                "chunk_quotes": {"chunk_rev_doc_20260520_ab12cd_0001_0001": "real chunk text only"},
                "feature_text": "candidate",
            },
            scenario="invalid_quote",
        )
    assert result.rejected
