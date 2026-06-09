"""Transition provider error mapping helpers."""

from __future__ import annotations

from indbase_core.provider_runs import map_provider_error_code


def map_transition_error(provider_code: str | None, *, partial: bool = False) -> str:
    return map_provider_error_code("transition", provider_code, partial=partial)
