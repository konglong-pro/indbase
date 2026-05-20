from pathlib import Path

import pytest

from indbase_core.db import connect, initialize_database, load_migrations
from indbase_core.documents import set_document_category
from indbase_core.tags import add_document_tag, add_tag
from indbase_core.taxonomy import validate_tag_type


def test_legacy_tag_backfill_assigns_types(tmp_path: Path) -> None:
    db_path = tmp_path / "vault" / ".indbase" / "db.sqlite"
    connection = connect(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version TEXT PRIMARY KEY,
              applied_at TEXT NOT NULL
            )
            """
        )
        now = "2026-05-20T00:00:00+00:00"
        for migration in load_migrations():
            if migration.version == "0008_taxonomy_foundation":
                break
            connection.executescript(migration.sql)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (migration.version, now),
            )
            connection.commit()

        for normalized in ("rag", "sqlite", "ai", "custom-topic"):
            connection.execute(
                """
                INSERT INTO tags(tag_id, name, normalized_name, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (f"tag_{normalized}", normalized, normalized, now),
            )
        connection.commit()

        taxonomy_migration = next(
            migration for migration in load_migrations() if migration.version == "0008_taxonomy_foundation"
        )
        connection.executescript(taxonomy_migration.sql)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            ("0008_taxonomy_foundation", now),
        )
        connection.commit()

        rows = {
            str(row["normalized_name"]): (str(row["type"]), str(row["created_by"]))
            for row in connection.execute(
                """
                SELECT normalized_name, type, created_by
                FROM tags
                WHERE deleted_at IS NULL
                """
            )
        }
    finally:
        connection.close()

    assert rows["rag"][0] == "method"
    assert rows["sqlite"][0] == "tool"
    assert rows["ai"][0] == "topic"
    assert rows["custom-topic"][0] == "topic"
    assert rows["rag"][1] == "legacy_migration"


def test_add_tag_requires_explicit_type(tmp_path: Path) -> None:
    db_path = tmp_path / "vault" / ".indbase" / "db.sqlite"
    initialize_database(db_path)

    with connect(db_path) as connection:
        tag_id = add_tag(connection, "Hybrid Search", tag_type="method")
        row = connection.execute("SELECT type, status FROM tags WHERE tag_id = ?", (tag_id,)).fetchone()

    assert row["type"] == "method"
    assert row["status"] == "active"


def test_invalid_tag_type_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid tag type"):
        validate_tag_type("not-a-real-type")


def test_add_document_tag_requires_existing_formal_tag(tmp_path: Path) -> None:
    db_path = tmp_path / "vault" / ".indbase" / "db.sqlite"
    initialize_database(db_path)

    with connect(db_path) as connection:
        now = "2026-05-20T00:00:00+00:00"
        connection.execute(
            """
            INSERT INTO documents(
              doc_id, status, ingest_status, created_at
            )
            VALUES ('doc_20260520_ab12cd', 'active', 'revisioned', ?)
            """,
            (now,),
        )
        connection.commit()
        with pytest.raises(ValueError, match="Create it first"):
            add_document_tag(connection, "doc_20260520_ab12cd", "missing-tag")


def test_set_document_category_writes_provenance(tmp_path: Path) -> None:
    db_path = tmp_path / "vault" / ".indbase" / "db.sqlite"
    initialize_database(db_path)

    with connect(db_path) as connection:
        now = "2026-05-20T00:00:00+00:00"
        connection.execute(
            """
            INSERT INTO categories(
              category_id, name, sort_order, is_active, is_system, created_at
            )
            VALUES ('cat_test', 'Test', 1, 1, 0, ?)
            """,
            (now,),
        )
        connection.execute(
            """
            INSERT INTO documents(
              doc_id, status, category_id, ingest_status, created_at
            )
            VALUES ('doc_20260520_ab12cd', 'active', 'cat_uncategorized', 'revisioned', ?)
            """,
            (now,),
        )
        connection.commit()
        set_document_category(
            connection,
            "doc_20260520_ab12cd",
            "cat_test",
            category_source="manual",
            category_updated_by="test-user",
        )
        row = connection.execute(
            """
            SELECT category_id, category_source, category_updated_by, category_updated_at
            FROM documents
            WHERE doc_id = 'doc_20260520_ab12cd'
            """
        ).fetchone()

    assert row["category_id"] == "cat_test"
    assert row["category_source"] == "manual"
    assert row["category_updated_by"] == "test-user"
    assert row["category_updated_at"] is not None
