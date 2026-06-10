"""Map transition bridge responses into indbase evidence packages."""

from __future__ import annotations

from indbase_core.artifacts.evidence import ArtifactRef, ArtifactTrustLevel
from indbase_core.capabilities.contracts import (
    EvidencePackageStatus,
    IndbaseProviderErrorCode,
    OutputEvidencePackage,
    ProviderError,
    ProviderWarning,
    SourceBinding,
)
from indbase_core.transition_adapter import TRANSITION_PIN_COMMIT
from indbase_core.transition_contract import BridgeResponse


def bridge_response_to_output_evidence(
    response: BridgeResponse,
    *,
    operation_id: str,
    source_binding: SourceBinding,
    input_sha256: str,
    capability_id: str,
    transport_profile: str,
    provider_job_id: str | None,
) -> OutputEvidencePackage:
    status = EvidencePackageStatus.PARTIAL if response.status == "partial" else response.status
    target_refs = tuple(
        ArtifactRef(
            kind=target.format,
            provider_uri=target.path,
            trust_level=ArtifactTrustLevel.DERIVED_OUTPUT,
            role="export_output",
        )
        for target in response.targets
        if target.path
    )
    errors = tuple(
        ProviderError(
            provider_code=error,
            indbase_code=IndbaseProviderErrorCode.PROVIDER_UNKNOWN_ERROR,
            message=error,
        )
        for error in response.errors
    )
    warnings = tuple(
        ProviderWarning(code="TARGET_FAILED", message=target.error or target.format)
        for target in response.targets
        if target.status != "succeeded"
    )
    return OutputEvidencePackage(
        provider_id="transition",
        provider_version=TRANSITION_PIN_COMMIT,
        capability_id=capability_id,
        transport_profile=transport_profile,
        operation_id=operation_id,
        provider_job_id=provider_job_id,
        status=status,
        source_binding=source_binding,
        input_sha256=input_sha256,
        normalized_markdown=ArtifactRef(
            kind="markdown",
            provider_uri=f"transition://jobs/{provider_job_id or operation_id}/normalized.md",
            trust_level=ArtifactTrustLevel.DERIVED_CANDIDATE,
            role="normalized_markdown",
        ),
        derived_outputs=target_refs,
        manifest=_provider_artifact(response.evidence_paths.manifest_path, kind="manifest"),
        trace=_provider_artifact(response.evidence_paths.trace_path, kind="trace"),
        reports=tuple(
            ref
            for ref in (_provider_artifact(response.evidence_paths.report_path, kind="report"),)
            if ref is not None
        ),
        warnings=warnings,
        errors=errors,
    )


def _provider_artifact(path: str | None, *, kind: str) -> ArtifactRef | None:
    if not path:
        return None
    return ArtifactRef(
        kind=kind,
        provider_uri=f"transition://{path}",
        trust_level=ArtifactTrustLevel.EVIDENCE,
        role=kind,
    )
