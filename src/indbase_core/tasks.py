"""Task and task event records."""

from __future__ import annotations

import json
import sqlite3

from indbase_core.ids import new_prefixed_id
from indbase_core.time import utc_now_iso

TERMINAL_STATUSES = {"succeeded", "completed_with_issues", "failed", "cancelled"}


def create_task(
    connection: sqlite3.Connection,
    task_type: str,
    input_data: dict[str, object] | None = None,
    created_by: str = "cli",
) -> str:
    task_id = new_prefixed_id("task")
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO tasks(
          task_id, type, status, input_json, created_at, updated_at, created_by
        )
        VALUES (?, ?, 'pending', ?, ?, ?, ?)
        """,
        (task_id, task_type, _json(input_data), now, now, created_by),
    )
    connection.commit()
    return task_id


def start_task(connection: sqlite3.Connection, task_id: str) -> None:
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE tasks
        SET status = 'running', started_at = COALESCE(started_at, ?), updated_at = ?
        WHERE task_id = ?
        """,
        (now, now, task_id),
    )
    connection.commit()


def finish_task(
    connection: sqlite3.Connection,
    task_id: str,
    status: str,
    result_data: dict[str, object] | None = None,
    error_data: dict[str, object] | None = None,
) -> None:
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"Invalid terminal task status: {status!r}")
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE tasks
        SET status = ?, result_json = ?, error_json = ?, finished_at = ?, updated_at = ?
        WHERE task_id = ?
        """,
        (status, _json(result_data), _json(error_data), now, now, task_id),
    )
    connection.commit()


def add_task_event(
    connection: sqlite3.Connection,
    task_id: str,
    event_type: str,
    message: str,
    payload: dict[str, object] | None = None,
) -> str:
    event_id = new_prefixed_id("event")
    connection.execute(
        """
        INSERT INTO task_events(event_id, task_id, event_type, message, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (event_id, task_id, event_type, message, _json(payload), utc_now_iso()),
    )
    connection.commit()
    return event_id


def list_tasks(connection: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT task_id, type, status, created_at, started_at, finished_at, created_by
            FROM tasks
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
    )


def get_task(connection: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT task_id, type, status, input_json, result_json, error_json, trace_id,
               created_at, updated_at, started_at, finished_at, progress_current,
               progress_total, created_by
        FROM tasks
        WHERE task_id = ?
        """,
        (task_id,),
    ).fetchone()


def list_task_events(connection: sqlite3.Connection, task_id: str) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT event_id, event_type, message, payload_json, created_at
            FROM task_events
            WHERE task_id = ?
            ORDER BY created_at
            """,
            (task_id,),
        )
    )


def _json(value: dict[str, object] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
