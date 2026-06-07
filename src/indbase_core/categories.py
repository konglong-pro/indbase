"""Category templates and manual category operations."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from indbase_core.category_profiles import (
    PROFILE_VERSION_V1,
    CategoryLocalizationSeed,
    CategoryProfileSeed,
    insert_category_profile,
    upsert_category_localization,
)
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


DEFAULT_TEMPLATE_KEY = "indbase_default_v1"

_LEGACY_TEMPLATE_NAMES = frozenset({"minimal", "academic", "full"})

_DEFAULT_V1_PROFILES: dict[str, CategoryProfileSeed] = {
    "cat_uncategorized": CategoryProfileSeed(
        "cat_uncategorized",
        "Default bucket when no confident Big Category applies.",
        positive_cues=(),
        negative_cues=("research paper", "project plan", "theorem", "llm", "rag"),
        classification_ready=False,
    ),
    "cat_computer_science": CategoryProfileSeed(
        "cat_computer_science",
        "Computing, software, AI systems, databases, and information technology.",
        positive_cues=(
            "computer science",
            "software engineering",
            "programming",
            "algorithm",
            "database",
            "llm",
            "rag",
            "embedding",
            "人工智能",
            "大语言模型",
            "检索增强",
            "向量",
            "python",
            "sqlite",
        ),
        negative_cues=("clinical trial", "poetry", "macroeconomics", "recipe"),
        example_titles=("RAG retrieval notes", "SQLite FTS design"),
    ),
    "cat_math_statistics": CategoryProfileSeed(
        "cat_math_statistics",
        "Mathematics, statistics, probability, and quantitative modeling.",
        positive_cues=(
            "mathematics",
            "statistics",
            "probability",
            "regression",
            "linear algebra",
            "数学",
            "统计",
            "概率",
        ),
        negative_cues=("marketing campaign", "novel", "compiler backend"),
    ),
    "cat_natural_science": CategoryProfileSeed(
        "cat_natural_science",
        "Natural sciences such as physics, chemistry, and biology.",
        positive_cues=("physics", "chemistry", "biology", "experiment", "自然科学", "物理", "化学"),
        negative_cues=("constitutional law", "javascript", "budget spreadsheet"),
    ),
    "cat_social_science": CategoryProfileSeed(
        "cat_social_science",
        "Social sciences, policy, economics, and organizational behavior.",
        positive_cues=("social science", "policy", "economics", "society", "社会科学", "经济", "政策"),
        negative_cues=("neural network", "poem", "kubernetes"),
    ),
    "cat_humanities": CategoryProfileSeed(
        "cat_humanities",
        "Humanities including history, philosophy, and literature.",
        positive_cues=("history", "philosophy", "literature", "humanities", "人文", "历史", "哲学"),
        negative_cues=("api endpoint", "gradient descent", "invoice"),
    ),
    "cat_tools_reference": CategoryProfileSeed(
        "cat_tools_reference",
        "Tools, references, manuals, and how-to documentation.",
        positive_cues=("reference", "manual", "documentation", "workflow", "cli", "工具", "参考", "手册"),
        negative_cues=("original research", "memoir", "clinical study"),
    ),
    "cat_projects_work": CategoryProfileSeed(
        "cat_projects_work",
        "Work projects, deliverables, meetings, and professional execution.",
        positive_cues=("project plan", "meeting notes", "deliverable", "milestone", "项目", "工作", "会议"),
        negative_cues=("quantum field", "sonnet", "recipe"),
    ),
    "cat_personal_management": CategoryProfileSeed(
        "cat_personal_management",
        "Personal productivity, habits, goals, and life management.",
        positive_cues=("habit", "goal tracking", "journal", "personal management", "个人管理", "习惯", "目标"),
        negative_cues=("compiler", "constitutional law", "particle physics"),
    ),
    "cat_interdisciplinary": CategoryProfileSeed(
        "cat_interdisciplinary",
        "Cross-domain work spanning multiple fields without a single dominant lens.",
        positive_cues=(
            "interdisciplinary",
            "cross discipline",
            "cross-disciplinary",
            "mixed methods",
            "mixed-methods",
            "multidisciplinary",
            "transdisciplinary",
            "跨领域",
            "跨学科",
        ),
        negative_cues=("single-domain tutorial", "pure mathematics proof only", "sqlite", "python"),
    ),
}

_DEFAULT_V1_LOCALIZATIONS: tuple[CategoryLocalizationSeed, ...] = (
    CategoryLocalizationSeed("cat_uncategorized", "en", "Uncategorized"),
    CategoryLocalizationSeed("cat_uncategorized", "zh-CN", "未分类"),
    CategoryLocalizationSeed("cat_computer_science", "en", "Computer Science"),
    CategoryLocalizationSeed("cat_computer_science", "zh-CN", "计算机科学"),
    CategoryLocalizationSeed("cat_math_statistics", "en", "Mathematics and Statistics"),
    CategoryLocalizationSeed("cat_math_statistics", "zh-CN", "数学与统计"),
    CategoryLocalizationSeed("cat_natural_science", "en", "Natural Science"),
    CategoryLocalizationSeed("cat_natural_science", "zh-CN", "自然科学"),
    CategoryLocalizationSeed("cat_social_science", "en", "Social Science"),
    CategoryLocalizationSeed("cat_social_science", "zh-CN", "社会科学"),
    CategoryLocalizationSeed("cat_humanities", "en", "Humanities"),
    CategoryLocalizationSeed("cat_humanities", "zh-CN", "人文"),
    CategoryLocalizationSeed("cat_tools_reference", "en", "Tools and Reference"),
    CategoryLocalizationSeed("cat_tools_reference", "zh-CN", "工具与参考"),
    CategoryLocalizationSeed("cat_projects_work", "en", "Projects and Work"),
    CategoryLocalizationSeed("cat_projects_work", "zh-CN", "项目与工作"),
    CategoryLocalizationSeed("cat_personal_management", "en", "Personal Management"),
    CategoryLocalizationSeed("cat_personal_management", "zh-CN", "个人管理"),
    CategoryLocalizationSeed("cat_interdisciplinary", "en", "Interdisciplinary"),
    CategoryLocalizationSeed("cat_interdisciplinary", "zh-CN", "跨领域"),
)

TEMPLATES: dict[str, tuple[CategorySeed, ...]] = {
    DEFAULT_TEMPLATE_KEY: (
        CategorySeed("cat_uncategorized", "Uncategorized", 0),
        CategorySeed("cat_computer_science", "Computer Science", 10),
        CategorySeed("cat_math_statistics", "Mathematics and Statistics", 20),
        CategorySeed("cat_natural_science", "Natural Science", 30),
        CategorySeed("cat_social_science", "Social Science", 40),
        CategorySeed("cat_humanities", "Humanities", 50),
        CategorySeed("cat_tools_reference", "Tools and Reference", 60),
        CategorySeed("cat_projects_work", "Projects and Work", 70),
        CategorySeed("cat_personal_management", "Personal Management", 80),
        CategorySeed("cat_interdisciplinary", "Interdisciplinary", 90),
    ),
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
    return (DEFAULT_TEMPLATE_KEY,)


def legacy_template_names() -> tuple[str, ...]:
    return tuple(sorted(_LEGACY_TEMPLATE_NAMES))


def apply_category_template(connection: sqlite3.Connection, template_name: str) -> int:
    if template_name in _LEGACY_TEMPLATE_NAMES:
        raise UnknownCategoryTemplate(
            f"Legacy category template {template_name!r} is retired for new vault initialization. "
            f"Use {DEFAULT_TEMPLATE_KEY!r} or catalog migrate-default for explicit migration."
        )
    seeds = TEMPLATES.get(template_name)
    if seeds is None:
        raise UnknownCategoryTemplate(
            f"Unknown category template {template_name!r}; expected one of {', '.join(template_names())}."
        )

    now = utc_now_iso()
    inserted = 0
    for seed in seeds:
        profile = _DEFAULT_V1_PROFILES.get(seed.category_id)
        classification_state = "manual_only"
        if profile is not None and profile.classification_ready:
            classification_state = "classification_ready"
        elif seed.category_id == "cat_uncategorized":
            classification_state = "manual_only"
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO categories(
              category_id, name, sort_order, is_active, is_system,
              template_key, classification_state, profile_version, locale_preference,
              created_at, updated_at
            )
            VALUES (?, ?, ?, 1, 1, ?, ?, ?, 'en', ?, ?)
            """,
            (
                seed.category_id,
                seed.name,
                seed.sort_order,
                template_name,
                classification_state,
                PROFILE_VERSION_V1,
                now,
                now,
            ),
        )
        inserted += cursor.rowcount
        if cursor.rowcount and template_name == DEFAULT_TEMPLATE_KEY:
            _seed_default_v1_profile(connection, seed.category_id)
    if template_name == DEFAULT_TEMPLATE_KEY:
        for localization in _DEFAULT_V1_LOCALIZATIONS:
            upsert_category_localization(
                connection,
                category_id=localization.category_id,
                locale=localization.locale,
                label=localization.label,
                description=localization.description,
            )
    connection.commit()
    return inserted


def _seed_default_v1_profile(connection: sqlite3.Connection, category_id: str) -> None:
    profile = _DEFAULT_V1_PROFILES.get(category_id)
    if profile is None:
        return
    existing = connection.execute(
        """
        SELECT category_profile_id
        FROM category_profiles
        WHERE category_id = ? AND profile_version = ?
        """,
        (category_id, PROFILE_VERSION_V1),
    ).fetchone()
    if existing is not None:
        return
    insert_category_profile(
        connection,
        category_id=profile.category_id,
        description=profile.description,
        positive_cues=list(profile.positive_cues) or ["__not_for_auto_classification__"],
        negative_cues=list(profile.negative_cues),
        example_titles=list(profile.example_titles),
        example_quotes=list(profile.example_quotes),
        aliases=list(profile.aliases),
        profile_version=PROFILE_VERSION_V1,
        classification_ready=profile.classification_ready,
    )


def add_category(connection: sqlite3.Connection, name: str, description: str | None = None) -> str:
    clean_name = _clean_category_name(name)
    category_id = new_prefixed_id("cat")
    now = utc_now_iso()
    sort_order = _next_sort_order(connection)
    connection.execute(
        """
        INSERT INTO categories(
          category_id, name, description, sort_order, is_active, is_system,
          classification_state, profile_version, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, 1, 0, 'manual_only', ?, ?, ?)
        """,
        (category_id, clean_name, description, sort_order, PROFILE_VERSION_V1, now, now),
    )
    connection.commit()
    return category_id


def list_categories(
    connection: sqlite3.Connection,
    include_inactive: bool = False,
    *,
    locale: str | None = None,
) -> list[sqlite3.Row]:
    label_select = "c.name AS display_name"
    join_clause = ""
    if locale:
        label_select = "COALESCE(cl.label, c.name) AS display_name"
        join_clause = "LEFT JOIN category_localizations cl ON cl.category_id = c.category_id AND cl.locale = ?"
    base = f"""
        SELECT c.category_id, c.name, {label_select}, c.description, c.parent_id,
               c.sort_order, c.is_active, c.is_system, c.classification_state,
               c.profile_version, c.template_key
        FROM categories c
        {join_clause}
    """
    if include_inactive:
        query = base + " ORDER BY c.sort_order, c.name"
        params: tuple[object, ...] = (locale,) if locale else ()
    else:
        query = base + " WHERE c.is_active = 1 AND c.deleted_at IS NULL ORDER BY c.sort_order, c.name"
        params = (locale,) if locale else ()
    return list(connection.execute(query, params))


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
