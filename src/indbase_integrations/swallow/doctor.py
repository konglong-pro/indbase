"""Swallow provider doctor helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from indbase_core.swallow_adapter import SwallowAdapterUnavailable, swallow_package_version


@dataclass(frozen=True)
class SwallowDoctorStatus:
    configured: bool
    package_version: str | None
    binding_profile: str
    capabilities_ok: bool
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def check_swallow_provider(*, configured: bool, binding_profile: str) -> SwallowDoctorStatus:
    try:
        package_version = swallow_package_version()
        capabilities_ok = configured
        warnings: tuple[str, ...] = ()
    except SwallowAdapterUnavailable as exc:
        package_version = None
        capabilities_ok = False
        warnings = (str(exc),)
    return SwallowDoctorStatus(
        configured=configured,
        package_version=package_version,
        binding_profile=binding_profile,
        capabilities_ok=capabilities_ok,
        warnings=warnings,
    )
