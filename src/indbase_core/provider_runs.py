"""Provider run records and provider error mapping."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from indbase_core.artifacts.evidence import ArtifactRef
from indbase_core.capabilities.contracts import (
    CAPABILITY_CONTRACT_VERSION,
    EvidencePackageStatus,
    EvidenceStatus,
    IndbaseProviderErrorCode,
    ProviderError,
)
from indbase_core.ids import new_prefixed_id
from indbase_core.paths import VaultPaths
from indbase_core.time import utc_now_iso


@dataclass(frozen=True)
class ProviderRunSeed:
    provider_run_id: str
    operation_id: str
    evidence_root: str


def new_operation_id(prefix: str = "op") -> str:
    return f"{prefix}_{uuid4().hex}"


def create_provider_run(
    connection: sqlite3.Connection,
    paths: VaultPaths,
    *,
    provider_id: str,
    provider_version: str,
    capability_id: str,
    transport_profile: str,
    provider_package: str | None = None,
    operation_id: str | None = None,
    action_id: str | None = None,
    task_id: str | None = None,
    ingest_run_id: str | None = None,
    converter_run_id: str | None = None,
    output_run_id: str | None = None,
    input_sha256: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ProviderRunSeed:
    provider_run_id = new_prefixed_id("provider_run")
    operation = operation_id or new_operation_id()
    evidence_root_path = paths.provider_run_evidence_dir(provider_run_id)
    evidence_root_path.mkdir(parents=True, exist_ok=True)
    evidence_root = paths.relative_to_vault(evidence_root_path)
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO provider_runs (
          provider_run_id, operation_id, action_id, task_id, ingest_run_id,
          converter_run_id, output_run_id, provider_id, provider_package,
          provider_version, capability_id, capability_contract_version,
          transport_profile, provider_status, evidence_status, started_at,
          input_sha256, evidence_root, metadata_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            provider_run_id,
            operation,
            action_id,
            task_id,
            ingest_run_id,
            converter_run_id,
            output_run_id,
            provider_id,
            provider_package,
            provider_version,
            capability_id,
            CAPABILITY_CONTRACT_VERSION,
            transport_profile,
            EvidenceStatus.PENDING.value,
            now,
            input_sha256,
            evidence_root,
            _json(metadata),
            now,
            now,
        ),
    )
    return ProviderRunSeed(provider_run_id=provider_run_id, operation_id=operation, evidence_root=evidence_root)


def attach_provider_run_to_ingest(
    connection: sqlite3.Connection,
    *,
    ingest_run_id: str,
    provider_run_id: str,
) -> None:
    connection.execute(
        "UPDATE ingest_runs SET adopted_provider_run_id = ?, updated_at = ? WHERE ingest_id = ?",
        (provider_run_id, utc_now_iso(), ingest_run_id),
    )


def attach_provider_run_to_converter(
    connection: sqlite3.Connection,
    *,
    converter_run_id: str,
    provider_run_id: str,
) -> None:
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE converter_runs
        SET adopted_provider_run_id = ?, updated_at = ?
        WHERE converter_run_id = ?
        """,
        (provider_run_id, now, converter_run_id),
    )
    connection.execute(
        """
        UPDATE provider_runs
        SET converter_run_id = COALESCE(converter_run_id, ?), updated_at = ?
        WHERE provider_run_id = ?
        """,
        (converter_run_id, now, provider_run_id),
    )


def attach_provider_run_to_output(
    connection: sqlite3.Connection,
    *,
    output_run_id: str,
    provider_run_id: str,
) -> None:
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE output_runs
        SET adopted_provider_run_id = ?, updated_at = ?
        WHERE output_run_id = ?
        """,
        (provider_run_id, now, output_run_id),
    )
    connection.execute(
        """
        UPDATE provider_runs
        SET output_run_id = COALESCE(output_run_id, ?), updated_at = ?
        WHERE provider_run_id = ?
        """,
        (output_run_id, now, provider_run_id),
    )


def finish_provider_run(
    connection: sqlite3.Connection,
    provider_run_id: str,
    *,
    provider_status: str | EvidencePackageStatus,
    evidence_status: str | EvidenceStatus | None = None,
    provider_job_id: str | None = None,
    provider_version: str | None = None,
    manifest_artifact_ref: ArtifactRef | None = None,
    trace_artifact_ref: ArtifactRef | None = None,
    warning_count: int = 0,
    error_count: int = 0,
    primary_error_code: str | IndbaseProviderErrorCode | None = None,
    provider_error_code: str | None = None,
    provider_error: ProviderError | dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE provider_runs
        SET provider_status = ?,
            provider_version = COALESCE(?, provider_version),
            evidence_status = COALESCE(?, evidence_status),
            provider_job_id = COALESCE(?, provider_job_id),
            manifest_artifact_ref_json = COALESCE(?, manifest_artifact_ref_json),
            trace_artifact_ref_json = COALESCE(?, trace_artifact_ref_json),
            warning_count = ?,
            error_count = ?,
            primary_error_code = ?,
            provider_error_code = ?,
            provider_error_json = ?,
            metadata_json = COALESCE(?, metadata_json),
            finished_at = ?,
            updated_at = ?
        WHERE provider_run_id = ?
        """,
        (
            _enum_value(provider_status),
            provider_version,
            _enum_value(evidence_status) if evidence_status is not None else None,
            provider_job_id,
            _artifact_json(manifest_artifact_ref),
            _artifact_json(trace_artifact_ref),
            warning_count,
            error_count,
            _enum_value(primary_error_code),
            provider_error_code,
            _provider_error_json(provider_error),
            _json(metadata),
            now,
            now,
            provider_run_id,
        ),
    )


def mark_provider_evidence(
    connection: sqlite3.Connection,
    provider_run_id: str,
    *,
    evidence_status: str | EvidenceStatus,
    manifest_artifact_ref: ArtifactRef | None = None,
    trace_artifact_ref: ArtifactRef | None = None,
) -> None:
    connection.execute(
        """
        UPDATE provider_runs
        SET evidence_status = ?,
            manifest_artifact_ref_json = COALESCE(?, manifest_artifact_ref_json),
            trace_artifact_ref_json = COALESCE(?, trace_artifact_ref_json),
            updated_at = ?
        WHERE provider_run_id = ?
        """,
        (
            _enum_value(evidence_status),
            _artifact_json(manifest_artifact_ref),
            _artifact_json(trace_artifact_ref),
            utc_now_iso(),
            provider_run_id,
        ),
    )


def get_provider_run(connection: sqlite3.Connection, provider_run_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT provider_run_id, operation_id, action_id, task_id, ingest_run_id,
               converter_run_id, output_run_id, provider_id, provider_package,
               provider_version, capability_id, capability_contract_version,
               transport_profile, provider_job_id, provider_status, evidence_status,
               started_at, finished_at, input_sha256, manifest_artifact_ref_json,
               trace_artifact_ref_json, evidence_root, warning_count, error_count,
               primary_error_code, provider_error_code, provider_error_json,
               metadata_json, created_at, updated_at
        FROM provider_runs
        WHERE provider_run_id = ?
        """,
        (provider_run_id,),
    ).fetchone()


def map_provider_error_code(provider_id: str, provider_code: str | None, *, partial: bool = False) -> str:
    if partial:
        return IndbaseProviderErrorCode.PROVIDER_PARTIAL_SUCCESS.value
    if not provider_code:
        return IndbaseProviderErrorCode.PROVIDER_UNKNOWN_ERROR.value
    code = provider_code.upper()
    if provider_id == "swallow":
        return _SWALLOW_ERROR_MAP.get(code, IndbaseProviderErrorCode.PROVIDER_UNKNOWN_ERROR.value)
    if provider_id == "transition":
        return _TRANSITION_ERROR_MAP.get(code, IndbaseProviderErrorCode.PROVIDER_UNKNOWN_ERROR.value)
    return IndbaseProviderErrorCode.PROVIDER_UNKNOWN_ERROR.value


def provider_run_indbase_uri(provider_run_id: str) -> str:
    return f"indbase://provider_runs/{provider_run_id}"


def provider_evidence_indbase_uri(provider_run_id: str) -> str:
    return f"indbase://provider_runs/{provider_run_id}/evidence"


def provider_evidence_root_path(paths: VaultPaths, provider_run_id: str) -> Path:
    return paths.provider_run_evidence_dir(provider_run_id)


_SWALLOW_ERROR_MAP = {
    "WORKER_NOT_REGISTERED": IndbaseProviderErrorCode.PROVIDER_DEPENDENCY_MISSING.value,
    "QUALITY_BELOW_THRESHOLD": IndbaseProviderErrorCode.PROVIDER_QUALITY_REJECTED.value,
    "INPUT_TOO_LARGE_SYNC": IndbaseProviderErrorCode.PROVIDER_INPUT_TOO_LARGE.value,
    "BROWSER_CAPTURE_INVALID": IndbaseProviderErrorCode.PROVIDER_INPUT_UNSUPPORTED.value,
    "WORKER_TIMEOUT": IndbaseProviderErrorCode.PROVIDER_TIMEOUT.value,
}

_TRANSITION_ERROR_MAP = {
    "PRETTIER_FAILED": IndbaseProviderErrorCode.PROVIDER_CONFIG_ERROR.value,
    "ZHLINT_FAILED": IndbaseProviderErrorCode.PROVIDER_CONFIG_ERROR.value,
    "PANDOC_FAILED": IndbaseProviderErrorCode.PROVIDER_PARTIAL_SUCCESS.value,
    "PDF_ENGINE_MISSING": IndbaseProviderErrorCode.PROVIDER_DEPENDENCY_MISSING.value,
    "CHECK_CHANGES": IndbaseProviderErrorCode.PROVIDER_CONFIG_ERROR.value,
    "CONFIG_OR_INPUT": IndbaseProviderErrorCode.PROVIDER_CONFIG_ERROR.value,
    "WRITE_REFUSED": IndbaseProviderErrorCode.PROVIDER_OUTPUT_WRITE_FAILED.value,
}


def _artifact_json(ref: ArtifactRef | None) -> str | None:
    if ref is None:
        return None
    return json.dumps(ref.to_dict(), ensure_ascii=False, sort_keys=True)


def _provider_error_json(error: ProviderError | dict[str, Any] | None) -> str | None:
    if error is None:
        return None
    if isinstance(error, ProviderError):
        payload = {
            "code": error.code,
            "message": error.message,
            "severity": error.severity,
            "mapped_code": error.mapped_code,
            "details": error.details,
        }
    else:
        payload = error
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _json(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _enum_value(value: object) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", value)  # type: ignore[no-any-return]
