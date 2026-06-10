"""Vault initialization service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from indbase_core.categories import apply_category_template
from indbase_core.config import default_config, save_config
from indbase_core.db import connect, initialize_database
from indbase_core.paths import VaultPaths, vault_paths
from indbase_core.tasks import add_task_event, create_task, finish_task, start_task


@dataclass(frozen=True)
class InitResult:
    vault_path: Path
    db_path: Path
    config_path: Path
    applied_migrations: tuple[str, ...]
    inserted_categories: int
    task_id: str


def init_vault(vault_path: Path | str, category_template: str = "indbase_default_v1") -> InitResult:
    paths = vault_paths(vault_path)
    paths.ensure_layout()

    applied_migrations = tuple(initialize_database(paths.db_path))

    connection = connect(paths.db_path)
    try:
        task_id = create_task(
            connection,
            "vault_init",
            input_data={
                "vault_path": paths.root.as_posix(),
                "category_template": category_template,
            },
        )
        start_task(connection, task_id)
        add_task_event(connection, task_id, "layout_created", "Vault directory layout is present.")
        add_task_event(
            connection,
            task_id,
            "migrations_applied",
            "Database migrations applied.",
            {"versions": list(applied_migrations)},
        )
        inserted_categories = apply_category_template(connection, category_template)
        add_task_event(
            connection,
            task_id,
            "categories_written",
            "Category template written to DB.",
            {"template": category_template, "inserted": inserted_categories},
        )
        config = default_config(paths.root)
        save_config(config, paths.config_path)
        add_task_event(connection, task_id, "config_written", "Vault config written.")
        finish_task(
            connection,
            task_id,
            "succeeded",
            result_data={
                "vault_path": paths.root.as_posix(),
                "db_path": paths.db_path.as_posix(),
                "config_path": paths.config_path.as_posix(),
                "applied_migrations": list(applied_migrations),
                "inserted_categories": inserted_categories,
            },
        )
    except Exception as exc:
        # Preserve a visible task failure if init breaks after task creation.
        if "task_id" in locals():
            finish_task(
                connection,
                task_id,
                "failed",
                error_data={"type": type(exc).__name__, "message": str(exc)},
            )
        raise
    finally:
        connection.close()

    return InitResult(
        vault_path=paths.root,
        db_path=paths.db_path,
        config_path=paths.config_path,
        applied_migrations=applied_migrations,
        inserted_categories=inserted_categories,
        task_id=task_id,
    )


def open_vault(vault_path: Path | str) -> VaultPaths:
    return vault_paths(vault_path)
