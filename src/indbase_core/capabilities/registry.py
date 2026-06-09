"""Static capability binding registry for provider-backed workflows."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityBinding:
    indbase_capability: str
    provider: str
    provider_capability: str
    profile: str
    feature_flag: str | None = None


DEFAULT_PROVIDER_BINDINGS: dict[str, CapabilityBinding] = {
    "indbase.ingest.file": CapabilityBinding(
        indbase_capability="indbase.ingest.file",
        provider="swallow",
        provider_capability="swallow.ingest.file",
        profile="local_core",
    ),
    "indbase.ingest.url": CapabilityBinding(
        indbase_capability="indbase.ingest.url",
        provider="swallow",
        provider_capability="swallow.ingest.url",
        profile="cli_or_queue",
        feature_flag="swallow_url_ingest",
    ),
    "indbase.output.normalize": CapabilityBinding(
        indbase_capability="indbase.output.normalize",
        provider="transition",
        provider_capability="transition.markdown.normalize",
        profile="node_bridge",
    ),
    "indbase.output.export": CapabilityBinding(
        indbase_capability="indbase.output.export",
        provider="transition",
        provider_capability="transition.markdown.export",
        profile="node_bridge",
    ),
}


def get_binding(indbase_capability: str) -> CapabilityBinding:
    try:
        return DEFAULT_PROVIDER_BINDINGS[indbase_capability]
    except KeyError as exc:
        raise ValueError(f"Provider binding not found: {indbase_capability}") from exc
