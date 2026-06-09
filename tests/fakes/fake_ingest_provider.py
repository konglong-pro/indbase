from __future__ import annotations

from pathlib import Path

from indbase_core.artifacts.evidence import ArtifactRef, ArtifactTrustLevel
from indbase_core.capabilities.contracts import (
    CAPABILITY_CONTRACT_VERSION,
    CapabilityManifest,
    EvidencePackageStatus,
    IngestEvidencePackage,
    IngestFileRequest,
    InputRef,
    IndbaseProviderErrorCode,
    ProviderError,
    ProviderProfile,
    ProviderWarning,
)


class FakeIngestProvider:
    provider_id = "fake_ingest"
    provider_version = "0.0.test"

    def __init__(self, *, status: str = "success", markdown: str = "# Fake\n\nBody.\n") -> None:
        self.status = status
        self.markdown = markdown

    def capabilities(self) -> list[CapabilityManifest]:
        return [
            CapabilityManifest(
                provider_id=self.provider_id,
                provider_package="fake_ingest",
                provider_version=self.provider_version,
                capability_id="fake_ingest.ingest.file",
                capability_contract_version=CAPABILITY_CONTRACT_VERSION,
                profiles=(ProviderProfile.LOCAL_CORE,),
                metadata={"input_kinds": ["file"], "output_kinds": ["markdown", "ingest_document"]},
            )
        ]

    async def ingest_file(
        self,
        request: IngestFileRequest,
        *,
        operation_id: str,
        cancel_token=None,
    ) -> IngestEvidencePackage:
        if cancel_token is not None and getattr(cancel_token, "cancelled", False):
            status = EvidencePackageStatus.CANCELLED
        else:
            status = EvidencePackageStatus(self.status)
        input_path = Path(request.input_ref.path or request.input_ref.uri or "fake-input")
        errors = ()
        warnings = ()
        candidate = None
        ingest_document = None
        if status in {EvidencePackageStatus.SUCCESS, EvidencePackageStatus.PARTIAL}:
            candidate = ArtifactRef(
                kind="markdown",
                provider_uri=f"fake://{operation_id}/document.md",
                indbase_uri=f"indbase://provider_runs/{operation_id}/evidence",
                trust_level=ArtifactTrustLevel.CONVERSION_CANDIDATE,
                role="candidate_markdown",
            )
            ingest_document = ArtifactRef(
                kind="ingest_document",
                provider_uri=f"fake://{operation_id}/ingest_document.json",
                indbase_uri=f"indbase://provider_runs/{operation_id}/evidence",
                trust_level=ArtifactTrustLevel.EVIDENCE,
                role="ingest_document",
            )
        if status == EvidencePackageStatus.PARTIAL:
            warnings = (ProviderWarning(code="FAKE_PARTIAL", message="partial ingest"),)
        if status == EvidencePackageStatus.FAILED:
            errors = (
                ProviderError(
                    provider_code="FAKE_FAILED",
                    indbase_code=IndbaseProviderErrorCode.PROVIDER_UNKNOWN_ERROR,
                    message="failed ingest",
                ),
            )
        if status == EvidencePackageStatus.CANCELLED:
            errors = (
                ProviderError(
                    provider_code="FAKE_CANCELLED",
                    indbase_code=IndbaseProviderErrorCode.PROVIDER_CANCELLED,
                    message="cancelled ingest",
                ),
            )
        return IngestEvidencePackage(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            capability_id="fake_ingest.ingest.file",
            transport_profile="local_core",
            operation_id=operation_id,
            provider_job_id=f"job_{operation_id}",
            status=status,
            input_ref=InputRef(kind="file", uri=input_path.as_posix()),
            raw_ref=None,
            candidate_markdown=candidate,
            ingest_document=ingest_document,
            manifest=None,
            trace=None,
            intermediate_artifacts=(),
            warnings=warnings,
            errors=errors,
            content_hash=None,
            quality={},
        )
