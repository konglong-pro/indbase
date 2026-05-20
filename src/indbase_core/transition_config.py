"""Whitelist validation for vault transition.config.json (Cfg1)."""

from __future__ import annotations

import json
from pathlib import Path
import hashlib

ALLOWED_TOP_LEVEL_KEYS = frozenset({"version", "render", "trace"})
ALLOWED_RENDER_KEYS = frozenset({"html", "pdf", "docx", "fonts"})
ALLOWED_TRACE_KEYS = frozenset({"level"})
FORBIDDEN_KEY_SUBSTRINGS = (
    "source_binding",
    "sourcebinding",
    "rewrite_binding",
    "config_search",
    "configsearch",
    "extends",
    "discover",
    "in_place",
    "inplace",
    "writeback",
)


def default_transition_config() -> dict[str, object]:
    return {
        "version": 1,
        "render": {
            "html": {"enabled": True},
            "pdf": {"enabled": True},
            "docx": {"enabled": True},
            "fonts": {"cjk": "auto"},
        },
        "trace": {"level": "info"},
    }


def config_hash(config: dict[str, object]) -> str:
    payload = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_transition_config(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise TransitionConfigError(f"transition config not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TransitionConfigError(f"transition config is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise TransitionConfigError("transition config must be a JSON object")
    validate_transition_config(payload)
    return payload


def validate_transition_config(payload: dict[str, object]) -> None:
    unknown = set(payload) - ALLOWED_TOP_LEVEL_KEYS
    if unknown:
        raise TransitionConfigError(f"unknown transition config keys: {', '.join(sorted(unknown))}")
    _reject_forbidden_keys(payload)
    render = payload.get("render")
    if render is not None:
        if not isinstance(render, dict):
            raise TransitionConfigError("render must be an object")
        unknown_render = set(render) - ALLOWED_RENDER_KEYS
        if unknown_render:
            raise TransitionConfigError(f"unknown render keys: {', '.join(sorted(unknown_render))}")
        _reject_forbidden_keys(render)
    trace = payload.get("trace")
    if trace is not None:
        if not isinstance(trace, dict):
            raise TransitionConfigError("trace must be an object")
        unknown_trace = set(trace) - ALLOWED_TRACE_KEYS
        if unknown_trace:
            raise TransitionConfigError(f"unknown trace keys: {', '.join(sorted(unknown_trace))}")
        _reject_forbidden_keys(trace)


def write_default_transition_config(path: Path) -> dict[str, object]:
    config = default_transition_config()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return config


class TransitionConfigError(ValueError):
    """Raised when transition.config.json violates Cfg1 rules."""


def _reject_forbidden_keys(payload: object, *, prefix: str = "") -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized = f"{prefix}.{key}" if prefix else str(key)
            lowered = normalized.lower().replace("-", "_")
            if any(token in lowered for token in FORBIDDEN_KEY_SUBSTRINGS):
                raise TransitionConfigError(f"forbidden transition config key: {normalized}")
            if isinstance(value, str) and _path_escapes_vault(value):
                raise TransitionConfigError(f"transition config path must stay in vault: {normalized}")
            _reject_forbidden_keys(value, prefix=normalized)
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            _reject_forbidden_keys(value, prefix=f"{prefix}[{index}]")


def _path_escapes_vault(value: str) -> bool:
    if ".." in value.replace("\\", "/"):
        return True
    return value.startswith(("/", "\\")) or (len(value) > 1 and value[1] == ":")
