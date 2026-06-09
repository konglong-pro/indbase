from __future__ import annotations

from pathlib import Path
import json
import sys

import pytest

_CONSOLER_SDK = Path(__file__).resolve().parents[2] / "consoler" / "sdks" / "python"
if _CONSOLER_SDK.is_dir():
    sys.path.insert(0, str(_CONSOLER_SDK))
    from indbase_agent.artifact_view import build_indbase_artifact_view
else:
    build_indbase_artifact_view = None

import indbase_core.conversion as conversion_module
from indbase_core.capabilities.contracts import IndbaseProviderErrorCode
from indbase_core.db import connect
from indbase_core.errors import record_error
from indbase_core.ids import new_prefixed_id
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.output_service import export_source_revision
from indbase_core.paths import vault_paths
from indbase_core.provider_runs import create_provider_run, finish_provider_run
from indbase_core.reviews import create_review_item
from indbase_core.transition_adapter import run_fake_bridge_failed_with_evidence
from indbase_core.transition_contract import (
    CONTRACT_VERSION,
    BridgeEvidencePaths,
    BridgeResponse,
    BridgeTargetResult,
)
from indbase_core.transition_runtime import install_runtime
from indbase_core.vault import init_vault
from conftest_output import insert_minimal_document


def test_ingest_success_records_provider_run_and_copied_evidence(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "trusted.md"
    source.write_text("# Trusted\n\nProvider evidence trusted body.\n", encoding="utf-8")

    result = run_m3_ingest_pipeline(vault, source)

    assert result.written_revisions == 1
    connection = connect(vault_paths(vault).db_path)
    try:
        run = connection.execute(
            """
            SELECT provider_run_id, provider_id, capability_id, provider_status,
                   evidence_status, evidence_root
            FROM provider_runs
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        assert run["provider_id"] == "swallow"
        assert run["capability_id"] == "swallow.ingest.file"
        assert run["provider_status"] == "success"
        assert run["evidence_status"] == "copied"
        converter = connection.execute(
            "SELECT adopted_provider_run_id FROM converter_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        ingest = connection.execute(
            "SELECT adopted_provider_run_id FROM ingest_runs WHERE ingest_id = ?",
            (result.ingest_id,),
        ).fetchone()
    finally:
        connection.close()

    evidence_root = vault / str(run["evidence_root"])
    assert (evidence_root / "document.md").is_file()
    assert (evidence_root / "trace.jsonl").is_file()
    assert (evidence_root / "provider.json").is_file()
    assert (evidence_root / "evidence_index.json").is_file()
    assert converter["adopted_provider_run_id"] == run["provider_run_id"]
    assert ingest["adopted_provider_run_id"] == run["provider_run_id"]


def test_ingest_failure_maps_provider_run_to_error_and_review(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "broken.md"
    source.write_text("# Broken\n\nWill fail.\n", encoding="utf-8")

    class FailingSwallowAdapter:
        def __init__(self, *, vault_path, config) -> None:
            pass

        def convert_file(self, path: Path):
            raise RuntimeError("WORKER_TIMEOUT")

    monkeypatch.setattr(conversion_module, "SwallowIngestAdapter", FailingSwallowAdapter)

    result = run_m3_ingest_pipeline(vault, source)

    assert result.failed_items == 1
    connection = connect(vault_paths(vault).db_path)
    try:
        provider_run = connection.execute(
            "SELECT provider_run_id, provider_status, primary_error_code FROM provider_runs"
        ).fetchone()
        error = connection.execute(
            "SELECT provider_run_id, error_type FROM errors WHERE provider_run_id IS NOT NULL"
        ).fetchone()
        review = connection.execute(
            "SELECT provider_run_id, ingest_run_id FROM review_items WHERE provider_run_id IS NOT NULL"
        ).fetchone()
    finally:
        connection.close()

    assert provider_run["provider_status"] == "failed"
    assert provider_run["primary_error_code"] == "provider_timeout"
    assert error["provider_run_id"] == provider_run["provider_run_id"]
    assert review["provider_run_id"] == provider_run["provider_run_id"]
    assert review["ingest_run_id"] == result.ingest_id


def test_transition_partial_export_records_derived_artifact_trust(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    install_runtime(vault, run_npm_install=False)
    paths = vault_paths(vault)
    connection = connect(paths.db_path)
    try:
        doc_id = insert_minimal_document(connection, vault, body="# Export\n\nBody.\n")

        def partial_bridge(request):
            cache_dir = Path(request.cache_dir)
            cache_dir.mkdir(parents=True, exist_ok=True)
            manifest = cache_dir / "transition_manifest.json"
            trace = cache_dir / "transition_trace.jsonl"
            report = cache_dir / "transition_report.json"
            manifest.write_text(json.dumps({"status": "partial"}) + "\n", encoding="utf-8")
            trace.write_text('{"event":"partial"}\n', encoding="utf-8")
            report.write_text(json.dumps({"status": "partial"}) + "\n", encoding="utf-8")
            html = paths.outputs_exports / "partial.html"
            html.parent.mkdir(parents=True, exist_ok=True)
            html.write_text("<h1>Export</h1>\n", encoding="utf-8")
            return BridgeResponse(
                contract_version=CONTRACT_VERSION,
                status="partial",
                normalized_markdown=request.markdown,
                normalized_hash="sha256:test",
                targets=(
                    BridgeTargetResult(
                        format="html",
                        status="succeeded",
                        path=paths.relative_to_vault(html),
                    ),
                    BridgeTargetResult(
                        format="pdf",
                        status="failed",
                        error="PDF_ENGINE_MISSING",
                    ),
                ),
                evidence_paths=BridgeEvidencePaths(
                    manifest_path=manifest.as_posix(),
                    trace_path=trace.as_posix(),
                    report_path=report.as_posix(),
                ),
            )

        result = export_source_revision(
            connection,
            vault,
            doc_id=doc_id,
            targets=("html", "pdf"),
            bridge_runner=partial_bridge,
        )
        provider_run = connection.execute(
            """
            SELECT provider_status, evidence_status, primary_error_code
            FROM provider_runs
            WHERE output_run_id = ?
            """,
            (result.output_run_id,),
        ).fetchone()
        artifacts = connection.execute(
            """
            SELECT format, status, artifact_role, trust_level
            FROM output_artifacts
            WHERE output_run_id = ?
            ORDER BY format
            """,
            (result.output_run_id,),
        ).fetchall()
    finally:
        connection.close()

    assert result.status == "partial"
    assert provider_run["provider_status"] == "partial"
    assert provider_run["evidence_status"] == "copied"
    assert provider_run["primary_error_code"] == "provider_partial_success"
    assert {row["trust_level"] for row in artifacts} <= {"derived_candidate", "derived_output"}
    assert {row["artifact_role"] for row in artifacts} == {"export_output", "normalized_markdown"}


def test_transition_failure_preserves_provider_error_and_evidence(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    install_runtime(vault, run_npm_install=False)
    paths = vault_paths(vault)
    connection = connect(paths.db_path)
    try:
        doc_id = insert_minimal_document(connection, vault, body="# Failed\n\nBody.\n")
        try:
            export_source_revision(
                connection,
                vault,
                doc_id=doc_id,
                bridge_runner=run_fake_bridge_failed_with_evidence,
            )
        except Exception:
            pass
        provider_run = connection.execute(
            """
            SELECT provider_run_id, provider_status, evidence_status, provider_error_json,
                   evidence_root
            FROM provider_runs
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        error = connection.execute(
            "SELECT provider_run_id FROM errors WHERE provider_run_id IS NOT NULL"
        ).fetchone()
    finally:
        connection.close()

    assert provider_run["provider_status"] == "failed"
    assert provider_run["evidence_status"] == "copied"
    assert provider_run["provider_error_json"]
    assert error["provider_run_id"] == provider_run["provider_run_id"]
    assert (vault / str(provider_run["evidence_root"]) / "transition_manifest.json").is_file()


def test_cancelled_provider_run_can_be_recorded_as_visible_error_and_review(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    paths = vault_paths(vault)
    connection = connect(paths.db_path)
    try:
        task_id = new_prefixed_id("task")
        now = "2026-06-09T00:00:00+00:00"
        connection.execute(
            """
            INSERT INTO tasks(task_id, type, status, created_at, updated_at)
            VALUES (?, 'provider_fake', 'running', ?, ?)
            """,
            (task_id, now, now),
        )
        seed = create_provider_run(
            connection,
            paths,
            provider_id="fake_provider",
            provider_package="fake-provider",
            provider_version="0.0.test",
            capability_id="fake_provider.ingest.file",
            transport_profile="local_core",
            task_id=task_id,
        )
        finish_provider_run(
            connection,
            seed.provider_run_id,
            provider_status="cancelled",
            evidence_status="pending",
            error_count=1,
            primary_error_code=IndbaseProviderErrorCode.PROVIDER_CANCELLED,
            provider_error_code="FAKE_CANCELLED",
            provider_error={"code": "FAKE_CANCELLED", "message": "cancelled"},
        )
        error_id = record_error(
            connection,
            component="provider_fake",
            error_type="provider_cancelled",
            message="cancelled",
            task_id=task_id,
            provider_run_id=seed.provider_run_id,
        )
        review_id = create_review_item(
            connection,
            review_type="provider_cancelled",
            target_type="error",
            target_id=error_id,
            reason="cancelled",
            provider_run_id=seed.provider_run_id,
        )
        connection.commit()
        row = connection.execute(
            "SELECT primary_error_code FROM provider_runs WHERE provider_run_id = ?",
            (seed.provider_run_id,),
        ).fetchone()
        error = connection.execute(
            "SELECT provider_run_id FROM errors WHERE error_id = ?",
            (error_id,),
        ).fetchone()
        review = connection.execute(
            "SELECT provider_run_id FROM review_items WHERE review_id = ?",
            (review_id,),
        ).fetchone()
    finally:
        connection.close()

    assert row["primary_error_code"] == "provider_cancelled"
    assert error["provider_run_id"] == seed.provider_run_id
    assert review["provider_run_id"] == seed.provider_run_id


def test_provider_evidence_artifact_view_is_indbase_owned(tmp_path: Path) -> None:
    if build_indbase_artifact_view is None:
        pytest.skip("consoler_agent_sdk not available")
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "trusted.md"
    source.write_text("# Trusted\n\nProvider evidence trusted body.\n", encoding="utf-8")
    run_m3_ingest_pipeline(vault, source)
    connection = connect(vault_paths(vault).db_path)
    try:
        provider_run_id = connection.execute(
            "SELECT provider_run_id FROM provider_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()["provider_run_id"]
    finally:
        connection.close()

    view = build_indbase_artifact_view(
        artifact_uri=f"indbase://provider_runs/{provider_run_id}/evidence",
        kind="indbase.provider_evidence",
        block_id="provider-evidence",
        action_id="action-test",
        metadata={"vault_path": vault.as_posix()},
    )

    assert view["kind"] == "indbase.provider_evidence"
    summary = view["blocks"][-1]["content"]["evidence_summary"]
    assert summary["evidence_root"].startswith(".indbase/artifacts/provider_runs/")
    assert any(item["name"] == "evidence_index.json" for item in summary["files"])
