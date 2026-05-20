"""Durable evidence artifact policy helpers."""

from __future__ import annotations

from pathlib import PurePosixPath

ARTIFACT_PATH_PREFIX = ".indbase/artifacts/"
ORIGINAL_PATH_PREFIX = ".indbase/originals/"
LOCATOR_ARTIFACT_KEYS = ("artifact", "page_image_artifact", "normalized_audio_artifact")


def is_vault_artifact_path(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    normalized = value.replace("\\", "/")
    if _is_absolute_or_parent_escape(normalized):
        return False
    return normalized.startswith(ARTIFACT_PATH_PREFIX)


def is_vault_original_path(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    normalized = value.replace("\\", "/")
    if _is_absolute_or_parent_escape(normalized):
        return False
    return normalized.startswith(ORIGINAL_PATH_PREFIX)


def is_relative_vault_path(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    return not _is_absolute_or_parent_escape(value.replace("\\", "/"))


def locator_artifact_values(locator: dict[str, object]) -> tuple[str, ...]:
    values: list[str] = []
    for key in LOCATOR_ARTIFACT_KEYS:
        value = locator.get(key)
        if isinstance(value, str) and value:
            values.append(value)
    return tuple(values)


def _is_absolute_or_parent_escape(value: str) -> bool:
    if value.startswith("/"):
        return True
    if len(value) >= 3 and value[1] == ":" and value[2] == "/":
        return True
    path = PurePosixPath(value)
    return any(part == ".." for part in path.parts)
