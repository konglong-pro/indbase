"""SQLite connection and migration helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
import shutil
import sqlite3

MIGRATIONS_PACKAGE = "indbase_core.migrations"


@dataclass(frozen=True)
class Migration:
    version: str
    sql: str


class MigrationError(RuntimeError):
    """Raised when schema migration state is unsafe."""


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(db_path: Path | str) -> list[str]:
    path = Path(db_path)
    connection = connect(path)
    try:
        return apply_migrations(connection, path)
    finally:
        connection.close()


def apply_migrations(connection: sqlite3.Connection, db_path: Path | None = None) -> list[str]:
    _ensure_schema_table(connection)
    migrations = load_migrations()
    applied = _applied_versions(connection)
    known_versions = {migration.version for migration in migrations}
    unknown_versions = sorted(applied - known_versions)
    if unknown_versions:
        raise MigrationError(
            "Database has unknown schema migration version(s): "
            + ", ".join(unknown_versions)
        )
    pending = [migration for migration in migrations if migration.version not in applied]

    if pending and applied and db_path is not None and db_path.exists() and db_path.stat().st_size > 0:
        _backup_database(db_path)

    applied_now: list[str] = []
    for migration in pending:
        try:
            connection.executescript(migration.sql)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (migration.version, _now_iso()),
            )
            connection.commit()
            applied_now.append(migration.version)
        except sqlite3.Error:
            connection.rollback()
            raise

    return applied_now


def load_migrations() -> list[Migration]:
    migration_root = resources.files(MIGRATIONS_PACKAGE)
    migrations: list[Migration] = []
    for migration_file in sorted(migration_root.iterdir(), key=lambda path: path.name):
        if migration_file.name.endswith(".sql"):
            migrations.append(
                Migration(
                    version=migration_file.name.removesuffix(".sql"),
                    sql=migration_file.read_text(encoding="utf-8"),
                )
            )
    return migrations


def _ensure_schema_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version TEXT PRIMARY KEY,
          applied_at TEXT NOT NULL
        )
        """
    )
    connection.commit()


def _applied_versions(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT version FROM schema_migrations").fetchall()
    return {str(row["version"]) for row in rows}


def _backup_database(db_path: Path) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_name(f"{db_path.name}.bak.{timestamp}")
    shutil.copy2(db_path, backup_path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
