"""Map swallow conversion candidates into indbase evidence packages."""

from __future__ import annotations

from pathlib import Path

from indbase_core.artifacts.evidence import ArtifactRef, ArtifactTrustLevel
from indbase_core.capabilities.contracts import (
    EvidencePackageStatus,
    IndbaseProviderErrorCode,
    IngestEvidencePackage,
    InputRef,
    ProviderError,
    ProviderWarning,
)
from indbase_core.conversion import hash_markdown
from indbase_core.swallow_adapter import ConversionCandidate


def candidate_to_ingest_evidence(
    candidate: ConversionCandidate,
    *,
    operation_id: str,
    input_ref: InputRef,
    capability_id: str,
    transport_profile: str,
) -> IngestEvidencePackage:
    provenance = candidate.provenance
    provider_version = provenance.swallow_version if provenance else "unknown"
    provider_job_id = provenance.swallow_job_id if provenance else None
    manifest = _provider_artifact(provenance.manifest_path if provenance else None, kind="manifest")
    trace = _provider_artifact(provenance.trace_path if provenance else None, kind="trace")
    ingest_document = _provider_artifact(
        provenance.ingest_document_path if provenance else None,
        kind="ingest_document",
    )
    intermediates = tuple(
        ref
        for ref in (
            _provider_artifact(path, kind="intermediate", role="intermediate_artifact")
            for path in candidate.artifact_manifest.required
        )
        if ref is not None
    )
    errors = tuple(
        ProviderError(
            provider_code=code,
            indbase_code=IndbaseProviderErrorCode.PROVIDER_UNKNOWN_ERROR,
            message=code,
        )
        for code in candidate.errors
    )
    return IngestEvidencePackage(
        provider_id="swallow",
        provider_version=provider_version,
        capability_id=capability_id,
        transport_profile=transport_profile,
        operation_id=operation_id,
        provider_job_id=provider_job_id,
        status=EvidencePackageStatus.SUCCESS if candidate.status == "success" else candidate.status,
        input_ref=input_ref,
        raw_ref=None,
        candidate_markdown=ArtifactRef(
            kind="markdown",
            provider_uri=f"swallow://jobs/{provider_job_id or operation_id}/document.md",
            trust_level=ArtifactTrustLevel.CONVERSION_CANDIDATE,
            role="candidate_markdown",
        ),
        ingest_document=ingest_document,
        manifest=manifest,
        trace=trace,
        intermediate_artifacts=intermediates,
        warnings=tuple(ProviderWarning(code="SWALLOW_WARNING", message=warning) for warning in candidate.warnings),
        errors=errors,
        content_hash=hash_markdown(candidate.markdown_body),
        quality={"quality_score": candidate.quality_score},
    )


def _provider_artifact(path: str | None, *, kind: str, role: str | None = None) -> ArtifactRef | None:
    if not path:
        return None
    return ArtifactRef(
        kind=kind,
        provider_uri=f"swallow://{Path(path).as_posix()}",
        trust_level=ArtifactTrustLevel.EVIDENCE,
        role=role or kind,
    )
