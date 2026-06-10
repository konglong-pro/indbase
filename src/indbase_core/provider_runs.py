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
    ProviderFailureClass,
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
    failure_class: str | ProviderFailureClass | None = None,
    provider_error: ProviderError | dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    now = utc_now_iso()
    provider_status_value = str(_enum_value(provider_status) or "")
    evidence_status_value = _enum_value(evidence_status) if evidence_status is not None else None
    primary_error_value = _enum_value(primary_error_code)
    provider_id = _provider_id_for_run(connection, provider_run_id)
    failure_class_value = _provider_failure_class_value(failure_class) or map_provider_failure_class(
        provider_id,
        provider_error_code,
        provider_status=provider_status_value,
        evidence_status=evidence_status_value,
        primary_error_code=primary_error_value,
    )
    merged_metadata = _merge_provider_run_metadata(
        connection,
        provider_run_id,
        metadata,
        failure_class=failure_class_value,
    )
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
            failure_class = ?,
            provider_error_json = ?,
            metadata_json = COALESCE(?, metadata_json),
            finished_at = ?,
            updated_at = ?
        WHERE provider_run_id = ?
        """,
        (
            provider_status_value,
            provider_version,
            evidence_status_value,
            provider_job_id,
            _artifact_json(manifest_artifact_ref),
            _artifact_json(trace_artifact_ref),
            warning_count,
            error_count,
            primary_error_value,
            provider_error_code,
            failure_class_value,
            _provider_error_json(provider_error),
            _json(merged_metadata),
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
               primary_error_code, provider_error_code, failure_class, provider_error_json,
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


def map_provider_failure_class(
    provider_id: str,
    provider_code: str | None,
    *,
    provider_status: str | EvidencePackageStatus | None = None,
    evidence_status: str | EvidenceStatus | None = None,
    primary_error_code: str | IndbaseProviderErrorCode | None = None,
    partial: bool = False,
) -> str | None:
    evidence_status_value = str(_enum_value(evidence_status) or "")
    if evidence_status_value == EvidenceStatus.COPY_FAILED.value:
        return ProviderFailureClass.PROVIDER_ARTIFACT_COPY_FAILED.value
    provider_status_value = str(_enum_value(provider_status) or "")
    if partial or provider_status_value == EvidencePackageStatus.PARTIAL.value:
        return ProviderFailureClass.PROVIDER_PARTIAL_SUCCESS.value
    if provider_status_value not in {"failed", "cancelled"} and not primary_error_code:
        return None
    indbase_code = str(_enum_value(primary_error_code) or "")
    if indbase_code:
        mapped = _ERROR_CODE_FAILURE_CLASS_MAP.get(indbase_code)
        if mapped:
            return mapped
    if not provider_code:
        return ProviderFailureClass.PROVIDER_UNKNOWN_FAILURE.value
    code = provider_code.upper()
    if provider_id == "swallow":
        return _SWALLOW_FAILURE_CLASS_MAP.get(code, ProviderFailureClass.PROVIDER_UNKNOWN_FAILURE.value)
    if provider_id == "transition":
        return _TRANSITION_FAILURE_CLASS_MAP.get(code, ProviderFailureClass.PROVIDER_UNKNOWN_FAILURE.value)
    return ProviderFailureClass.PROVIDER_UNKNOWN_FAILURE.value


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

_ERROR_CODE_FAILURE_CLASS_MAP = {
    IndbaseProviderErrorCode.PROVIDER_DEPENDENCY_MISSING.value: ProviderFailureClass.PROVIDER_UNAVAILABLE.value,
    IndbaseProviderErrorCode.PROVIDER_INPUT_UNSUPPORTED.value: ProviderFailureClass.PROVIDER_UNSUPPORTED_INPUT.value,
    IndbaseProviderErrorCode.PROVIDER_INPUT_TOO_LARGE.value: ProviderFailureClass.PROVIDER_UNSUPPORTED_INPUT.value,
    IndbaseProviderErrorCode.PROVIDER_QUALITY_REJECTED.value: ProviderFailureClass.PROVIDER_LOW_QUALITY_CANDIDATE.value,
    IndbaseProviderErrorCode.PROVIDER_TIMEOUT.value: ProviderFailureClass.PROVIDER_TIMEOUT.value,
    IndbaseProviderErrorCode.PROVIDER_CANCELLED.value: ProviderFailureClass.PROVIDER_UNKNOWN_FAILURE.value,
    IndbaseProviderErrorCode.PROVIDER_PARTIAL_SUCCESS.value: ProviderFailureClass.PROVIDER_PARTIAL_SUCCESS.value,
    IndbaseProviderErrorCode.PROVIDER_TRANSPORT_FAILED.value: ProviderFailureClass.PROVIDER_UNAVAILABLE.value,
    IndbaseProviderErrorCode.PROVIDER_CONFIG_ERROR.value: ProviderFailureClass.PROVIDER_CONTRACT_VIOLATION.value,
    IndbaseProviderErrorCode.PROVIDER_OUTPUT_WRITE_FAILED.value: ProviderFailureClass.PROVIDER_ARTIFACT_COPY_FAILED.value,
    IndbaseProviderErrorCode.PROVIDER_UNKNOWN_ERROR.value: ProviderFailureClass.PROVIDER_UNKNOWN_FAILURE.value,
}

_SWALLOW_FAILURE_CLASS_MAP = {
    "WORKER_NOT_REGISTERED": ProviderFailureClass.PROVIDER_UNAVAILABLE.value,
    "QUALITY_BELOW_THRESHOLD": ProviderFailureClass.PROVIDER_LOW_QUALITY_CANDIDATE.value,
    "INPUT_TOO_LARGE_SYNC": ProviderFailureClass.PROVIDER_UNSUPPORTED_INPUT.value,
    "BROWSER_CAPTURE_INVALID": ProviderFailureClass.PROVIDER_UNSUPPORTED_INPUT.value,
    "WORKER_TIMEOUT": ProviderFailureClass.PROVIDER_TIMEOUT.value,
}

_TRANSITION_FAILURE_CLASS_MAP = {
    "PRETTIER_FAILED": ProviderFailureClass.PROVIDER_CONTRACT_VIOLATION.value,
    "ZHLINT_FAILED": ProviderFailureClass.PROVIDER_CONTRACT_VIOLATION.value,
    "PANDOC_FAILED": ProviderFailureClass.PROVIDER_PARTIAL_SUCCESS.value,
    "PDF_ENGINE_MISSING": ProviderFailureClass.PROVIDER_UNAVAILABLE.value,
    "CHECK_CHANGES": ProviderFailureClass.PROVIDER_CONTRACT_VIOLATION.value,
    "CONFIG_OR_INPUT": ProviderFailureClass.PROVIDER_CONTRACT_VIOLATION.value,
    "WRITE_REFUSED": ProviderFailureClass.PROVIDER_ARTIFACT_COPY_FAILED.value,
}


def _artifact_json(ref: ArtifactRef | None) -> str | None:
    if ref is None:
        return None
    return json.dumps(ref.to_dict(), ensure_ascii=False, sort_keys=True)


def _provider_error_json(error: ProviderError | dict[str, Any] | None) -> str | None:
    if error is None:
        return None
    if isinstance(error, ProviderError):
        payload = error.to_dict()
    else:
        payload = error
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _provider_id_for_run(connection: sqlite3.Connection, provider_run_id: str) -> str:
    row = connection.execute(
        "SELECT provider_id FROM provider_runs WHERE provider_run_id = ?",
        (provider_run_id,),
    ).fetchone()
    if row is None:
        return ""
    return str(row["provider_id"] or "")


def _provider_failure_class_value(value: str | ProviderFailureClass | None) -> str | None:
    if value is None:
        return None
    return ProviderFailureClass(str(_enum_value(value))).value


def _merge_provider_run_metadata(
    connection: sqlite3.Connection,
    provider_run_id: str,
    metadata: dict[str, Any] | None,
    *,
    failure_class: str | None,
) -> dict[str, Any] | None:
    if metadata is None and failure_class is None:
        return None
    merged = _current_provider_run_metadata(connection, provider_run_id)
    if metadata:
        merged.update(metadata)
    if failure_class:
        merged["provider_run_policy"] = _policy_metadata_for_failure_class(failure_class)
    return merged


def _current_provider_run_metadata(connection: sqlite3.Connection, provider_run_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT metadata_json FROM provider_runs WHERE provider_run_id = ?",
        (provider_run_id,),
    ).fetchone()
    if row is None or not row["metadata_json"]:
        return {}
    try:
        payload = json.loads(str(row["metadata_json"]))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _policy_metadata_for_failure_class(failure_class: str) -> dict[str, Any]:
    retryable = failure_class in {
        ProviderFailureClass.PROVIDER_TIMEOUT.value,
        ProviderFailureClass.PROVIDER_UNAVAILABLE.value,
        ProviderFailureClass.PROVIDER_UNKNOWN_FAILURE.value,
    }
    fallback_candidate = failure_class in {
        ProviderFailureClass.PROVIDER_UNAVAILABLE.value,
        ProviderFailureClass.PROVIDER_TIMEOUT.value,
    }
    recommended_action = {
        ProviderFailureClass.PROVIDER_UNAVAILABLE.value: "check_provider_runtime",
        ProviderFailureClass.PROVIDER_TIMEOUT.value: "retry_or_review_timeout",
        ProviderFailureClass.PROVIDER_CONTRACT_VIOLATION.value: "review_provider_contract",
        ProviderFailureClass.PROVIDER_LOW_QUALITY_CANDIDATE.value: "review_candidate_quality",
        ProviderFailureClass.PROVIDER_ARTIFACT_COPY_FAILED.value: "repair_evidence_copy",
        ProviderFailureClass.PROVIDER_PARTIAL_SUCCESS.value: "review_partial_outputs",
        ProviderFailureClass.PROVIDER_UNSUPPORTED_INPUT.value: "choose_supported_input",
        ProviderFailureClass.PROVIDER_UNKNOWN_FAILURE.value: "inspect_provider_error",
    }.get(failure_class, "inspect_provider_error")
    return {
        "retryable": retryable,
        "fallback_candidate": fallback_candidate,
        "recommended_action": recommended_action,
    }


def _json(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _enum_value(value: object) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", value)  # type: ignore[no-any-return]
