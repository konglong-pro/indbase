"""Swallow provider doctor helpers."""

from __future__ import annotations

from dataclasses import dataclass

from indbase_core.capabilities.contracts import (
    ProviderFailureClass,
    ProviderHealth,
    ProviderHealthFinding,
    ProviderSmokeStatus,
)
from indbase_core.swallow_adapter import SwallowAdapterUnavailable, swallow_package_version


@dataclass(frozen=True)
class SwallowDoctorStatus:
    configured: bool
    binding_profile: str
    provider_version: str | None
    capabilities_ok: bool
    runtime_ok: bool
    smoke_status: str
    findings: tuple[ProviderHealthFinding, ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload = ProviderHealth(
            provider_id="swallow",
            configured=self.configured,
            binding_profile=self.binding_profile,
            provider_version=self.provider_version,
            capabilities_ok=self.capabilities_ok,
            runtime_ok=self.runtime_ok,
            smoke_status=self.smoke_status,
            findings=self.findings,
        ).to_dict()
        payload["package_version"] = self.provider_version
        payload["warnings"] = [finding.code for finding in self.findings if finding.severity != "info"]
        return payload


def check_swallow_provider(*, configured: bool, binding_profile: str) -> SwallowDoctorStatus:
    findings: list[ProviderHealthFinding] = []
    try:
        package_version = swallow_package_version()
        runtime_ok = True
    except SwallowAdapterUnavailable as exc:
        package_version = None
        runtime_ok = False
        findings.append(
            ProviderHealthFinding(
                severity="error" if configured else "info",
                code="swallow_package_missing",
                message=str(exc),
                failure_class=ProviderFailureClass.PROVIDER_UNAVAILABLE,
                required=configured,
                skipped=not configured,
            )
        )
    if not configured:
        findings.append(
            ProviderHealthFinding(
                severity="info",
                code="swallow_not_configured",
                message="swallow ingest feature is disabled for this vault.",
                skipped=True,
            )
        )
    capabilities_ok = configured and runtime_ok
    return SwallowDoctorStatus(
        configured=configured,
        binding_profile=binding_profile,
        provider_version=package_version,
        capabilities_ok=capabilities_ok,
        runtime_ok=runtime_ok,
        smoke_status=ProviderSmokeStatus.NOT_RUN.value,
        findings=tuple(findings),
    )
