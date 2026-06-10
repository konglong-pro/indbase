from __future__ import annotations

from indbase_core.artifacts.evidence import ArtifactRef, ArtifactTrustLevel
from indbase_core.capabilities.contracts import (
    CAPABILITY_CONTRACT_VERSION,
    CapabilityManifest,
    EvidencePackageStatus,
    ExportMarkdownRequest,
    IndbaseProviderErrorCode,
    NormalizeMarkdownRequest,
    OutputEvidencePackage,
    ProviderError,
    ProviderProfile,
    ProviderWarning,
    SourceBinding,
)


class FakeOutputProvider:
    provider_id = "fake_output"
    provider_version = "0.0.test"

    def __init__(self, *, status: str = "success") -> None:
        self.status = EvidencePackageStatus(status)

    def capabilities(self) -> list[CapabilityManifest]:
        return [
            CapabilityManifest(
                provider_id=self.provider_id,
                provider_package="fake_output",
                provider_version=self.provider_version,
                capability_id="fake_output.markdown.export",
                capability_contract_version=CAPABILITY_CONTRACT_VERSION,
                profiles=(ProviderProfile.NODE_BRIDGE,),
                metadata={"input_kinds": ["markdown"], "output_kinds": ["markdown", "html", "pdf", "docx"]},
            )
        ]

    async def normalize(
        self,
        request: NormalizeMarkdownRequest,
        *,
        operation_id: str,
    ) -> OutputEvidencePackage:
        return self._package(operation_id=operation_id, source_binding=request.source_binding, include_normalized=True)

    async def export(
        self,
        request: ExportMarkdownRequest,
        *,
        operation_id: str,
    ) -> OutputEvidencePackage:
        return self._package(operation_id=operation_id, source_binding=request.source_binding, include_normalized=True)

    def _package(
        self,
        *,
        operation_id: str,
        source_binding: SourceBinding,
        include_normalized: bool,
    ) -> OutputEvidencePackage:
        errors = ()
        warnings = ()
        normalized = None
        outputs: tuple[ArtifactRef, ...] = ()
        if self.status in {EvidencePackageStatus.SUCCESS, EvidencePackageStatus.PARTIAL} and include_normalized:
            normalized = ArtifactRef(
                kind="markdown",
                provider_uri=f"fake://{operation_id}/normalized.md",
                indbase_uri=f"indbase://provider_runs/{operation_id}/evidence",
                trust_level=ArtifactTrustLevel.DERIVED_CANDIDATE,
                role="normalized_markdown",
            )
            outputs = (
                ArtifactRef(
                    kind="html",
                    provider_uri=f"fake://{operation_id}/source.html",
                    indbase_uri=f"indbase://provider_runs/{operation_id}/evidence",
                    trust_level=ArtifactTrustLevel.DERIVED_OUTPUT,
                    role="export_output",
                ),
            )
        if self.status == EvidencePackageStatus.PARTIAL:
            warnings = (ProviderWarning(code="FAKE_PDF_FAILED", message="pdf failed"),)
        if self.status == EvidencePackageStatus.FAILED:
            errors = (
                ProviderError(
                    provider_code="FAKE_FAILED",
                    indbase_code=IndbaseProviderErrorCode.PROVIDER_UNKNOWN_ERROR,
                    message="failed output",
                ),
            )
        if self.status == EvidencePackageStatus.CANCELLED:
            errors = (
                ProviderError(
                    provider_code="FAKE_CANCELLED",
                    indbase_code=IndbaseProviderErrorCode.PROVIDER_CANCELLED,
                    message="cancelled output",
                ),
            )
        return OutputEvidencePackage(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            capability_id="fake_output.markdown.export",
            transport_profile="node_bridge",
            operation_id=operation_id,
            provider_job_id=f"job_{operation_id}",
            status=self.status,
            source_binding=source_binding,
            input_sha256="sha256:test",
            normalized_markdown=normalized,
            derived_outputs=outputs,
            diff=None,
            manifest=None,
            trace=None,
            reports=(),
            warnings=warnings,
            errors=errors,
        )
