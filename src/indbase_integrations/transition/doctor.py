"""Transition provider doctor helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import shutil

from indbase_core.transition_adapter import TRANSITION_PIN_COMMIT


@dataclass(frozen=True)
class TransitionDoctorStatus:
    configured: bool
    package_version: str
    binding_profile: str
    node_ok: bool
    pandoc: str
    xelatex: str
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def check_transition_provider(*, configured: bool, binding_profile: str) -> TransitionDoctorStatus:
    node_ok = shutil.which("node") is not None
    pandoc = "ok" if shutil.which("pandoc") else "missing_optional"
    xelatex = "ok" if shutil.which("xelatex") else "missing_optional"
    warnings = () if node_ok else ("node_missing",)
    return TransitionDoctorStatus(
        configured=configured,
        package_version=TRANSITION_PIN_COMMIT,
        binding_profile=binding_profile,
        node_ok=node_ok,
        pandoc=pandoc,
        xelatex=xelatex,
        warnings=warnings,
    )
