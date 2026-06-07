"""Category profile and localization services for v0.3.1 taxonomy."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3

from indbase_core.ids import new_prefixed_id
from indbase_core.time import utc_now_iso

PROFILE_VERSION_V1 = "v1"
SUPPORTED_LOCALES = ("en", "zh-CN")


@dataclass(frozen=True)
class CategoryProfileSeed:
    category_id: str
    description: str
    positive_cues: tuple[str, ...]
    negative_cues: tuple[str, ...]
    example_titles: tuple[str, ...] = ()
    example_quotes: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    classification_ready: bool = True


@dataclass(frozen=True)
class CategoryLocalizationSeed:
    category_id: str
    locale: str
    label: str
    description: str | None = None


def profile_boundary_valid(
    *,
    positive_cues: tuple[str, ...] | list[str],
    negative_cues: tuple[str, ...] | list[str],
) -> bool:
    return bool(positive_cues) and bool(negative_cues)


def insert_category_profile(
    connection: sqlite3.Connection,
    *,
    category_id: str,
    description: str,
    positive_cues: list[str],
    negative_cues: list[str],
    example_titles: list[str] | None = None,
    example_quotes: list[str] | None = None,
    aliases: list[str] | None = None,
    profile_version: str = PROFILE_VERSION_V1,
    classification_ready: bool | None = None,
) -> str:
    ready = classification_ready
    if ready is None:
        ready = profile_boundary_valid(positive_cues=positive_cues, negative_cues=negative_cues)
    if ready and not profile_boundary_valid(positive_cues=positive_cues, negative_cues=negative_cues):
        raise ValueError("Classification-ready profile requires non-empty positive and negative cues.")
    if not ready and not negative_cues:
        raise ValueError("Category profile requires non-empty negative cues.")
    profile_id = new_prefixed_id("catprof")
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO category_profiles(
          category_profile_id, category_id, profile_version, description,
          positive_cues_json, negative_cues_json, example_titles_json,
          example_quotes_json, aliases_json, classification_ready,
          created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            profile_id,
            category_id,
            profile_version,
            description,
            json.dumps(positive_cues, ensure_ascii=False),
            json.dumps(negative_cues, ensure_ascii=False),
            json.dumps(example_titles or [], ensure_ascii=False),
            json.dumps(example_quotes or [], ensure_ascii=False),
            json.dumps(aliases or [], ensure_ascii=False),
            1 if ready else 0,
            now,
            now,
        ),
    )
    connection.execute(
        """
        UPDATE categories
        SET profile_version = ?, classification_state = ?, updated_at = ?
        WHERE category_id = ?
        """,
        (
            profile_version,
            "classification_ready" if ready else "manual_only",
            now,
            category_id,
        ),
    )
    return profile_id


def upsert_category_localization(
    connection: sqlite3.Connection,
    *,
    category_id: str,
    locale: str,
    label: str,
    description: str | None = None,
) -> str:
    if locale not in SUPPORTED_LOCALES:
        raise ValueError(f"Unsupported locale {locale!r}; expected one of {', '.join(SUPPORTED_LOCALES)}.")
    clean_label = " ".join(label.strip().split())
    if not clean_label:
        raise ValueError("Localization label must not be empty.")
    existing = connection.execute(
        """
        SELECT category_localization_id
        FROM category_localizations
        WHERE category_id = ? AND locale = ?
        """,
        (category_id, locale),
    ).fetchone()
    now = utc_now_iso()
    if existing is not None:
        connection.execute(
            """
            UPDATE category_localizations
            SET label = ?, description = ?, updated_at = ?
            WHERE category_localization_id = ?
            """,
            (clean_label, description, now, existing["category_localization_id"]),
        )
        return str(existing["category_localization_id"])
    localization_id = new_prefixed_id("catloc")
    connection.execute(
        """
        INSERT INTO category_localizations(
          category_localization_id, category_id, locale, label, description, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (localization_id, category_id, locale, clean_label, description, now, now),
    )
    return localization_id


def get_active_profile(connection: sqlite3.Connection, category_id: str) -> sqlite3.Row | None:
    row = connection.execute(
        """
        SELECT cp.*, c.classification_state
        FROM categories c
        LEFT JOIN category_profiles cp
          ON cp.category_id = c.category_id
         AND cp.profile_version = COALESCE(c.profile_version, ?)
        WHERE c.category_id = ?
          AND c.deleted_at IS NULL
        """,
        (PROFILE_VERSION_V1, category_id),
    ).fetchone()
    return row


def list_classification_ready_profiles(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT cp.*, c.classification_state, c.name
            FROM categories c
            JOIN category_profiles cp
              ON cp.category_id = c.category_id
             AND cp.profile_version = COALESCE(c.profile_version, ?)
            WHERE c.is_active = 1
              AND c.deleted_at IS NULL
              AND c.classification_state = 'classification_ready'
              AND cp.classification_ready = 1
              AND c.category_id != 'cat_uncategorized'
            ORDER BY c.sort_order, c.name
            """,
            (PROFILE_VERSION_V1,),
        )
    )


def set_category_classification_state(
    connection: sqlite3.Connection,
    category_id: str,
    classification_state: str,
) -> None:
    valid = {
        "manual_only",
        "classification_ready",
        "inactive_for_classification",
        "archived",
    }
    if classification_state not in valid:
        raise ValueError(f"Invalid classification_state: {classification_state}")
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE categories
        SET classification_state = ?, updated_at = ?
        WHERE category_id = ?
        """,
        (classification_state, now, category_id),
    )


def localized_label(connection: sqlite3.Connection, category_id: str, locale: str = "en") -> str | None:
    row = connection.execute(
        """
        SELECT label
        FROM category_localizations
        WHERE category_id = ? AND locale = ?
        """,
        (category_id, locale),
    ).fetchone()
    if row is not None:
        return str(row["label"])
    fallback = connection.execute(
        """
        SELECT name
        FROM categories
        WHERE category_id = ?
        """,
        (category_id,),
    ).fetchone()
    return str(fallback["name"]) if fallback is not None else None


def profile_snapshot(connection: sqlite3.Connection) -> list[dict[str, object]]:
    rows = list_classification_ready_profiles(connection)
    snapshot: list[dict[str, object]] = []
    for row in rows:
        snapshot.append(
            {
                "category_id": row["category_id"],
                "profile_version": row["profile_version"],
                "description": row["description"],
                "positive_cues": json.loads(row["positive_cues_json"]),
                "negative_cues": json.loads(row["negative_cues_json"]),
            }
        )
    return snapshot


def parse_profile_cues(row: sqlite3.Row) -> tuple[list[str], list[str]]:
    return (
        json.loads(row["positive_cues_json"]),
        json.loads(row["negative_cues_json"]),
    )
