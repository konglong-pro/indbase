import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from indbase_cli.main import app
from indbase_core.chunker import chunk_current_revision
from indbase_core.db import connect
from indbase_core.documents import set_document_category
from indbase_core.indexer import rebuild_fts_index
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.profile import build_document_profile
from indbase_core.retrieval import retrieve_chunks
from indbase_core.retrieval_evaluation import (
    READINESS_POLICY_VERSION,
    assess_answer_readiness,
    export_eval_cases_jsonl,
    import_eval_cases_from_jsonl,
    run_eval_suite,
    upsert_eval_case,
    validate_case_payload,
)
from indbase_core.tags import add_document_tag, add_tag
from indbase_core.vault import init_vault

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "v033_retrieval_eval" / "v033_core.jsonl"


def _ingest_index(vault: Path, source: Path) -> str:
    run_m3_ingest_pipeline(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc_id = connection.execute(
            "SELECT doc_id FROM documents WHERE normalized_source_uri LIKE ?",
            (f"%{source.name}",),
        ).fetchone()["doc_id"]
        chunk_current_revision(connection, vault, doc_id)
        rebuild_fts_index(connection, vault)
    return doc_id


def _vault_with_rag_doc(tmp_path: Path) -> tuple[Path, str]:
    vault = tmp_path / "vault"
    init_vault(vault, category_template="indbase_default_v1")
    source = tmp_path / "primary.md"
    source.write_text(
        "# AI\n\n"
        + "\n\n".join(
            f"## Section {index}\n\n"
            f"Paragraph {index} discusses RAG retrieval augmented generation hybrid search evidence."
            for index in range(12)
        )
        + "\n",
        encoding="utf-8",
    )
    doc_id = _ingest_index(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_tag(connection, "rag", tag_type="method")
        add_document_tag(connection, doc_id, "rag")
        set_document_category(connection, doc_id, "cat_computer_science")
        build_document_profile(connection, doc_id)
        connection.commit()
    return vault, doc_id


def test_eval_schema_rejects_invalid_status(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        now = "2026-05-21T00:00:00+00:00"
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO retrieval_eval_cases(
                  eval_case_id, suite, name, query_text, options_json, expectations_json,
                  status, source, created_at
                )
                VALUES ('retrcase_test', 's', 'n', 'q', '{}', '{}', 'bad', 'manual', ?)
                """,
                (now,),
            )


def test_readiness_verdict_constraint(tmp_path: Path) -> None:
    vault, _doc_id = _vault_with_rag_doc(tmp_path)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        result = retrieve_chunks(connection, "hybrid search", top_k=5, mode="fts")
        now = "2026-05-21T00:00:00+00:00"
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO answer_readiness_reports(
                  readiness_report_id, retrieval_run_id, policy_version, verdict,
                  score, blockers_json, warnings_json, metrics_json, created_at
                )
                VALUES ('answerready_test', ?, ?, 'maybe', 0.0, '[]', '[]', '{}', ?)
                """,
                (result.retrieval_run_id, READINESS_POLICY_VERSION, now),
            )


def test_import_rejects_invalid_jsonl_and_expectations() -> None:
    with pytest.raises(ValueError, match="unsupported expectations"):
        validate_case_payload(
            {
                "suite": "s",
                "name": "n",
                "query": "q",
                "expect": {"unknown_key": True},
            }
        )


def test_import_export_round_trip(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    roundtrip_path = tmp_path / "roundtrip.jsonl"
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        imported = import_eval_cases_from_jsonl(connection, FIXTURE, source="fixture")
        assert imported.rejected == 0
        exported = export_eval_cases_jsonl(connection, suite="v033_core")
        assert '"suite": "v033_core"' in exported
        assert "hybrid baseline" in exported
    roundtrip_path.write_text(exported, encoding="utf-8")
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        reimported = import_eval_cases_from_jsonl(connection, roundtrip_path, source="fixture")
    assert reimported.rejected == 0


def test_eval_run_links_retrieval_and_readiness(tmp_path: Path) -> None:
    vault, doc_id = _vault_with_rag_doc(tmp_path)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        upsert_eval_case(
            connection,
            suite="test_suite",
            name="doc hit",
            query_text="hybrid search",
            options={"mode": "fts", "top_k": 8},
                expectations={
                    "expected_doc_ids": [doc_id],
                    "min_result_count": 2,
                    "expected_readiness": "ready",
                },
            notes=None,
            source="manual",
        )
        connection.commit()
        result = run_eval_suite(connection, suite="test_suite")
        row = connection.execute(
            """
            SELECT retrieval_run_id, readiness_report_id, status
            FROM retrieval_eval_results
            WHERE eval_run_id = ?
            """,
            (result.eval_run_id,),
        ).fetchone()
    assert result.passed_count == 1
    assert row["retrieval_run_id"]
    assert row["readiness_report_id"]
    assert row["status"] == "passed"


def test_eval_expected_doc_missing_fails(tmp_path: Path) -> None:
    vault, _doc_id = _vault_with_rag_doc(tmp_path)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        upsert_eval_case(
            connection,
            suite="fail_suite",
            name="missing doc",
            query_text="hybrid search",
            options={"mode": "fts", "top_k": 5},
            expectations={"expected_doc_ids": ["doc_20990101_deadbeef"], "min_result_count": 1},
            notes=None,
            source="manual",
        )
        connection.commit()
        result = run_eval_suite(connection, suite="fail_suite")
    assert result.failed_count == 1
    assert "missing_expected_doc" in result.results[0].failures[0]


def test_readiness_zero_items_not_ready(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault, category_template="indbase_default_v1")
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        result = retrieve_chunks(connection, "category:计算机科学", top_k=5, mode="fts")
        report = assess_answer_readiness(connection, result.retrieval_run_id)
    assert result.status == "failed"
    assert report.verdict == "not_ready"
    assert "zero_items" in report.blockers or "retrieval_run_failed" in report.blockers


def test_readiness_sparse_package_needs_more_evidence(tmp_path: Path) -> None:
    vault, _doc_id = _vault_with_rag_doc(tmp_path)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        result = retrieve_chunks(connection, "hybrid search", top_k=1, per_doc_limit=1, mode="fts")
        report = assess_answer_readiness(connection, result.retrieval_run_id)
    assert len(result.items) == 1
    assert report.verdict == "needs_more_evidence"
    assert "low_item_count" in report.warnings


def test_readiness_rich_package_ready(tmp_path: Path) -> None:
    vault, _doc_id = _vault_with_rag_doc(tmp_path)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        result = retrieve_chunks(connection, "hybrid search", top_k=8, per_doc_limit=3, mode="fts")
        report = assess_answer_readiness(connection, result.retrieval_run_id)
    assert len(result.items) >= 2
    assert report.verdict == "ready"


def test_cli_eval_import_run_readiness_json(tmp_path: Path) -> None:
    runner = CliRunner()
    vault, _doc_id = _vault_with_rag_doc(tmp_path)
    imported = runner.invoke(
        app,
        ["eval", "retrieval", "import", str(FIXTURE), "--vault", str(vault), "--json"],
    )
    assert imported.exit_code == 0
    ran = runner.invoke(
        app,
        ["eval", "retrieval", "run", "--suite", "v033_core", "--limit", "1", "--vault", str(vault), "--json"],
    )
    assert ran.exit_code in {0, 1}
    payload = json.loads(ran.output)
    run_id = payload["eval_run_id"]
    readiness_run = payload["results"][0]["retrieval_run_id"]
    assert readiness_run
    readiness = runner.invoke(
        app,
        ["eval", "retrieval", "readiness", readiness_run, "--vault", str(vault), "--json"],
    )
    assert readiness.exit_code in {0, 1}
    assert "verdict" in json.loads(readiness.output)
    show = runner.invoke(app, ["eval", "retrieval", "show", run_id, "--vault", str(vault), "--json"])
    assert show.exit_code == 0
    listed = runner.invoke(app, ["eval", "retrieval", "list", "--vault", str(vault), "--json"])
    assert listed.exit_code == 0
    assert json.loads(listed.output)["runs"]
