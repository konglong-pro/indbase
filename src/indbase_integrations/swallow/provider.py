"""Swallow ingest capability provider."""

from __future__ import annotations

from pathlib import Path

from indbase_core.capabilities.contracts import (
    CAPABILITY_CONTRACT_VERSION,
    CapabilityManifest,
    IngestEvidencePackage,
    IngestFileRequest,
    ProviderProfile,
)
from indbase_core.config import SwallowIngestConfig
from indbase_core.swallow_adapter import SwallowIngestAdapter, swallow_package_version

from .mapper import candidate_to_ingest_evidence


class SwallowIngestProvider:
    provider_id = "swallow"

    def __init__(
        self,
        *,
        vault_path: Path | str,
        config: SwallowIngestConfig,
        transport_profile: str = "local_core",
    ) -> None:
        self.vault_path = Path(vault_path)
        self.config = config
        self.transport_profile = transport_profile
        self.provider_version = swallow_package_version()

    def capabilities(self) -> list[CapabilityManifest]:
        return [
            CapabilityManifest(
                provider_id=self.provider_id,
                provider_package="swallow",
                provider_version=self.provider_version,
                capability_id="swallow.ingest.file",
                capability_contract_version=CAPABILITY_CONTRACT_VERSION,
                profiles=(ProviderProfile.LOCAL_CORE,),
                metadata={"input_kinds": ["file"], "output_kinds": ["markdown", "ingest_document"]},
            ),
            CapabilityManifest(
                provider_id=self.provider_id,
                provider_package="swallow",
                provider_version=self.provider_version,
                capability_id="swallow.ingest.url",
                capability_contract_version=CAPABILITY_CONTRACT_VERSION,
                profiles=(ProviderProfile.CLI, ProviderProfile.QUEUE),
                metadata={"input_kinds": ["url"], "output_kinds": ["markdown", "ingest_document"]},
            ),
        ]

    async def ingest_file(
        self,
        request: IngestFileRequest,
        *,
        operation_id: str,
        cancel_token: object | None = None,
    ) -> IngestEvidencePackage:
        if cancel_token is not None and bool(getattr(cancel_token, "cancelled", False)):
            raise RuntimeError("swallow_ingest_cancelled")
        source = request.input_ref.path or request.input_ref.uri
        if not source:
            raise ValueError("ingest_file requires input_ref.path or input_ref.uri")
        adapter = SwallowIngestAdapter(vault_path=self.vault_path, config=self.config)
        candidate = adapter.convert_file(Path(source))
        return candidate_to_ingest_evidence(
            candidate,
            operation_id=operation_id,
            input_ref=request.input_ref,
            capability_id="swallow.ingest.file",
            transport_profile=self.transport_profile,
        )
