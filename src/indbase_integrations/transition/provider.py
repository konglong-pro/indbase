"""Transition markdown output capability provider."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

from indbase_core.capabilities.contracts import (
    CAPABILITY_CONTRACT_VERSION,
    CapabilityManifest,
    ExportMarkdownRequest,
    NormalizeMarkdownRequest,
    OutputEvidencePackage,
    ProviderProfile,
)
from indbase_core.protected_spans import spans_for_export_markdown, spans_for_normalize_body
from indbase_core.transition_adapter import (
    TRANSITION_PIN_COMMIT,
    BridgeRunContext,
    build_request,
    invoke_transition_bridge,
)
from indbase_core.transition_contract import BridgeRequest, BridgeResponse

from .mapper import bridge_response_to_output_evidence

BridgeRunner = Callable[[BridgeRequest], BridgeResponse]


class TransitionOutputProvider:
    provider_id = "transition"
    provider_version = TRANSITION_PIN_COMMIT

    def __init__(
        self,
        *,
        vault_root: Path | str,
        runtime_dir: Path | str,
        bridge_path: Path | str,
        cache_dir: Path | str,
        timeout_seconds: int,
        bridge_runner: BridgeRunner | None = None,
        transport_profile: str = "node_bridge",
    ) -> None:
        self.vault_root = Path(vault_root)
        self.runtime_dir = Path(runtime_dir)
        self.bridge_path = Path(bridge_path)
        self.cache_dir = Path(cache_dir)
        self.timeout_seconds = timeout_seconds
        self.bridge_runner = bridge_runner
        self.transport_profile = transport_profile

    def capabilities(self) -> list[CapabilityManifest]:
        return [
            CapabilityManifest(
                provider_id=self.provider_id,
                provider_package="transition",
                provider_version=self.provider_version,
                capability_id="transition.markdown.normalize",
                capability_contract_version=CAPABILITY_CONTRACT_VERSION,
                profiles=(ProviderProfile.NODE_BRIDGE,),
                metadata={"input_kinds": ["markdown"], "output_kinds": ["markdown"]},
            ),
            CapabilityManifest(
                provider_id=self.provider_id,
                provider_package="transition",
                provider_version=self.provider_version,
                capability_id="transition.markdown.export",
                capability_contract_version=CAPABILITY_CONTRACT_VERSION,
                profiles=(ProviderProfile.NODE_BRIDGE,),
                metadata={"input_kinds": ["markdown"], "output_kinds": ["markdown", "html", "pdf", "docx"]},
            ),
        ]

    async def normalize(
        self,
        request: NormalizeMarkdownRequest,
        *,
        operation_id: str,
    ) -> OutputEvidencePackage:
        protected = spans_for_normalize_body(request.markdown)
        bridge_request = build_request(
            mode="source_normalize_replace_current" if request.replace_current else "source_normalize_candidate",
            input_kind="source_document",
            markdown=request.markdown,
            protected_spans=protected,
            targets=(),
            config_hash=str(request.metadata.get("config_hash") or ""),
            cache_dir=self.cache_dir / operation_id,
        )
        invocation = invoke_transition_bridge(bridge_request, self._context(), runner=self.bridge_runner)
        if invocation.response is None:
            raise RuntimeError(invocation.error or "transition_normalize_failed")
        return bridge_response_to_output_evidence(
            invocation.response,
            operation_id=operation_id,
            source_binding=request.source_binding,
            input_sha256=hashlib.sha256(request.markdown.encode("utf-8")).hexdigest(),
            capability_id="transition.markdown.normalize",
            transport_profile=self.transport_profile,
            provider_job_id=operation_id,
        )

    async def export(
        self,
        request: ExportMarkdownRequest,
        *,
        operation_id: str,
    ) -> OutputEvidencePackage:
        protected = spans_for_export_markdown(request.markdown, input_kind="source_revision")
        bridge_request = build_request(
            mode="export",
            input_kind="source_revision",
            markdown=request.markdown,
            protected_spans=protected,
            targets=request.targets,
            config_hash=str(request.metadata.get("config_hash") or ""),
            cache_dir=self.cache_dir / operation_id,
        )
        invocation = invoke_transition_bridge(bridge_request, self._context(), runner=self.bridge_runner)
        if invocation.response is None:
            raise RuntimeError(invocation.error or "transition_export_failed")
        return bridge_response_to_output_evidence(
            invocation.response,
            operation_id=operation_id,
            source_binding=request.source_binding,
            input_sha256=hashlib.sha256(request.markdown.encode("utf-8")).hexdigest(),
            capability_id="transition.markdown.export",
            transport_profile=self.transport_profile,
            provider_job_id=operation_id,
        )

    def _context(self) -> BridgeRunContext:
        return BridgeRunContext(
            vault_root=self.vault_root,
            runtime_dir=self.runtime_dir,
            bridge_path=self.bridge_path,
            cache_dir=self.cache_dir,
            timeout_seconds=self.timeout_seconds,
        )
