"""Swallow provider error mapping helpers."""

from __future__ import annotations

from indbase_core.provider_runs import map_provider_error_code


def map_swallow_error(provider_code: str | None) -> str:
    return map_provider_error_code("swallow", provider_code)
