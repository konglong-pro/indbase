"""Transition provider doctor helpers."""

from __future__ import annotations

from dataclasses import dataclass
import shutil

from indbase_core.capabilities.contracts import (
    ProviderFailureClass,
    ProviderHealth,
    ProviderHealthFinding,
    ProviderSmokeStatus,
)
from indbase_core.transition_adapter import TRANSITION_PIN_COMMIT


@dataclass(frozen=True)
class TransitionDoctorStatus:
    configured: bool
    binding_profile: str
    provider_version: str
    capabilities_ok: bool
    runtime_ok: bool
    smoke_status: str
    node_ok: bool
    pandoc: str
    xelatex: str
    findings: tuple[ProviderHealthFinding, ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload = ProviderHealth(
            provider_id="transition",
            configured=self.configured,
            binding_profile=self.binding_profile,
            provider_version=self.provider_version,
            capabilities_ok=self.capabilities_ok,
            runtime_ok=self.runtime_ok,
            smoke_status=self.smoke_status,
            findings=self.findings,
        ).to_dict()
        payload["package_version"] = self.provider_version
        payload["node_ok"] = self.node_ok
        payload["pandoc"] = self.pandoc
        payload["xelatex"] = self.xelatex
        payload["warnings"] = [finding.code for finding in self.findings if finding.severity != "info"]
        return payload


def check_transition_provider(*, configured: bool, binding_profile: str) -> TransitionDoctorStatus:
    node_ok = shutil.which("node") is not None
    pandoc = "ok" if shutil.which("pandoc") else "missing_optional"
    xelatex = "ok" if shutil.which("xelatex") else "missing_optional"
    findings: list[ProviderHealthFinding] = []
    if not configured:
        findings.append(
            ProviderHealthFinding(
                severity="info",
                code="transition_not_configured",
                message="transition output feature is disabled for this vault.",
                skipped=True,
            )
        )
    if not node_ok:
        findings.append(
            ProviderHealthFinding(
                severity="error" if configured else "info",
                code="transition_node_missing",
                message="Node.js runtime is required for transition node_bridge output.",
                failure_class=ProviderFailureClass.PROVIDER_UNAVAILABLE,
                required=configured,
                skipped=not configured,
            )
        )
    if pandoc != "ok":
        findings.append(
            ProviderHealthFinding(
                severity="info",
                code="transition_pandoc_missing_optional",
                message="Pandoc is not installed; document export targets may be unavailable.",
                skipped=True,
            )
        )
    if xelatex != "ok":
        findings.append(
            ProviderHealthFinding(
                severity="info",
                code="transition_xelatex_missing_optional",
                message="XeLaTeX is not installed; PDF export may be unavailable.",
                skipped=True,
            )
        )
    capabilities_ok = configured and node_ok
    return TransitionDoctorStatus(
        configured=configured,
        binding_profile=binding_profile,
        provider_version=TRANSITION_PIN_COMMIT,
        capabilities_ok=capabilities_ok,
        runtime_ok=node_ok,
        smoke_status=ProviderSmokeStatus.NOT_RUN.value,
        node_ok=node_ok,
        pandoc=pandoc,
        xelatex=xelatex,
        findings=tuple(findings),
    )
