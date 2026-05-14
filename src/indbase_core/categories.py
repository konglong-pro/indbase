"""Category templates and manual category operations."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from indbase_core.ids import new_prefixed_id
from indbase_core.indexer import refresh_document_fts_metadata
from indbase_core.time import utc_now_iso


@dataclass(frozen=True)
class CategorySeed:
    category_id: str
    name: str
    sort_order: int


@dataclass(frozen=True)
class CategoryChange:
    category_id: str
    changed: bool


TEMPLATES: dict[str, tuple[CategorySeed, ...]] = {
    "minimal": (
        CategorySeed("cat_uncategorized", "未分类", 0),
        CategorySeed("cat_work", "工作", 10),
        CategorySeed("cat_learning", "学习", 20),
        CategorySeed("cat_reference", "资料", 30),
        CategorySeed("cat_personal", "个人", 40),
        CategorySeed("cat_tools", "工具", 50),
    ),
    "academic": (
        CategorySeed("cat_uncategorized", "未分类", 0),
        CategorySeed("cat_computer_science", "计算机科学", 10),
        CategorySeed("cat_math_statistics", "数学与统计", 20),
        CategorySeed("cat_natural_science", "自然科学", 30),
        CategorySeed("cat_social_science", "社会科学", 40),
        CategorySeed("cat_humanities", "人文", 50),
        CategorySeed("cat_tools_reference", "工具与参考", 60),
        CategorySeed("cat_interdisciplinary", "跨学科", 70),
    ),
    "full": (
        CategorySeed("cat_uncategorized", "未分类", 0),
        CategorySeed("cat_computer_science", "计算机科学", 10),
        CategorySeed("cat_math_statistics", "数学与统计", 20),
        CategorySeed("cat_physics", "物理", 30),
        CategorySeed("cat_life_science", "生命科学", 40),
        CategorySeed("cat_philosophy", "哲学", 50),
        CategorySeed("cat_social_science", "社会科学", 60),
        CategorySeed("cat_economics_business", "经济与商业", 70),
        CategorySeed("cat_history_culture", "历史与文化", 80),
        CategorySeed("cat_language_literature", "语言与文学", 90),
        CategorySeed("cat_art_design", "艺术与设计", 100),
        CategorySeed("cat_personal_management", "个人管理", 110),
        CategorySeed("cat_tools_reference", "工具与参考", 120),
        CategorySeed("cat_interdisciplinary", "跨学科", 130),
    ),
}


class UnknownCategoryTemplate(ValueError):
    """Raised when a category template name is not supported."""


def template_names() -> tuple[str, ...]:
    return tuple(TEMPLATES)


def apply_category_template(connection: sqlite3.Connection, template_name: str) -> int:
    seeds = TEMPLATES.get(template_name)
    if seeds is None:
        raise UnknownCategoryTemplate(
            f"Unknown category template {template_name!r}; expected one of {', '.join(template_names())}."
        )

    now = utc_now_iso()
    inserted = 0
    for seed in seeds:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO categories(
              category_id, name, sort_order, is_active, is_system, created_at, updated_at
            )
            VALUES (?, ?, ?, 1, 1, ?, ?)
            """,
            (seed.category_id, seed.name, seed.sort_order, now, now),
        )
        inserted += cursor.rowcount
    connection.commit()
    return inserted


def add_category(connection: sqlite3.Connection, name: str, description: str | None = None) -> str:
    clean_name = _clean_category_name(name)
    category_id = new_prefixed_id("cat")
    now = utc_now_iso()
    sort_order = _next_sort_order(connection)
    connection.execute(
        """
        INSERT INTO categories(
          category_id, name, description, sort_order, is_active, is_system, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, 1, 0, ?, ?)
        """,
        (category_id, clean_name, description, sort_order, now, now),
    )
    connection.commit()
    return category_id


def list_categories(connection: sqlite3.Connection, include_inactive: bool = False) -> list[sqlite3.Row]:
    if include_inactive:
        return list(
            connection.execute(
                """
                SELECT category_id, name, description, parent_id, sort_order, is_active, is_system
                FROM categories
                ORDER BY sort_order, name
                """
            )
        )
    return list(
        connection.execute(
            """
            SELECT category_id, name, description, parent_id, sort_order, is_active, is_system
            FROM categories
            WHERE is_active = 1 AND deleted_at IS NULL
            ORDER BY sort_order, name
            """
        )
    )


def update_category(
    connection: sqlite3.Connection,
    category_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    sort_order: int | None = None,
) -> CategoryChange:
    row = _load_category(connection, category_id)
    if row is None:
        raise ValueError(f"Category not found: {category_id}")
    if row["deleted_at"] is not None:
        raise ValueError(f"Category is archived: {category_id}")
    updates: list[str] = []
    values: list[object] = []
    if name is not None:
        updates.append("name = ?")
        values.append(_clean_category_name(name))
    if description is not None:
        updates.append("description = ?")
        values.append(description)
    if sort_order is not None:
        updates.append("sort_order = ?")
        values.append(sort_order)
    if not updates:
        return CategoryChange(category_id=category_id, changed=False)

    updates.append("updated_at = ?")
    values.append(utc_now_iso())
    values.append(category_id)
    connection.execute(
        f"""
        UPDATE categories
        SET {", ".join(updates)}
        WHERE category_id = ?
        """,
        values,
    )
    _refresh_assigned_documents_fts_metadata(connection, category_id)
    connection.commit()
    return CategoryChange(category_id=category_id, changed=True)


def archive_category(connection: sqlite3.Connection, category_id: str) -> CategoryChange:
    row = _load_category(connection, category_id)
    if row is None:
        raise ValueError(f"Category not found: {category_id}")
    if int(row["is_system"] or 0) == 1:
        raise ValueError(f"System category cannot be archived: {category_id}")
    if row["deleted_at"] is not None or int(row["is_active"] or 0) == 0:
        return CategoryChange(category_id=category_id, changed=False)
    active_docs = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM documents
        WHERE category_id = ?
          AND deleted_at IS NULL
        """,
        (category_id,),
    ).fetchone()
    if int(active_docs["count"] or 0) > 0:
        raise ValueError(f"Category is still assigned to {active_docs['count']} document(s): {category_id}")

    now = utc_now_iso()
    connection.execute(
        """
        UPDATE categories
        SET is_active = 0, deleted_at = ?, updated_at = ?
        WHERE category_id = ?
        """,
        (now, now, category_id),
    )
    connection.commit()
    return CategoryChange(category_id=category_id, changed=True)


def restore_category(connection: sqlite3.Connection, category_id: str) -> CategoryChange:
    row = _load_category(connection, category_id)
    if row is None:
        raise ValueError(f"Category not found: {category_id}")
    if row["deleted_at"] is None and int(row["is_active"] or 0) == 1:
        return CategoryChange(category_id=category_id, changed=False)
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE categories
        SET is_active = 1, deleted_at = NULL, updated_at = ?
        WHERE category_id = ?
        """,
        (now, category_id),
    )
    connection.commit()
    return CategoryChange(category_id=category_id, changed=True)


def _next_sort_order(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT COALESCE(MAX(sort_order), 0) AS max_sort FROM categories").fetchone()
    return int(row["max_sort"]) + 10


def _load_category(connection: sqlite3.Connection, category_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT category_id, is_active, is_system, deleted_at
        FROM categories
        WHERE category_id = ?
        """,
        (category_id,),
    ).fetchone()


def _clean_category_name(name: str) -> str:
    clean = " ".join(name.strip().split())
    if not clean:
        raise ValueError("Category name must not be empty.")
    return clean


def _refresh_assigned_documents_fts_metadata(connection: sqlite3.Connection, category_id: str) -> None:
    rows = connection.execute(
        """
        SELECT doc_id
        FROM documents
        WHERE category_id = ?
          AND deleted_at IS NULL
        """,
        (category_id,),
    ).fetchall()
    for row in rows:
        refresh_document_fts_metadata(connection, str(row["doc_id"]))
