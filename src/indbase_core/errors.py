"""Error record helpers."""

from __future__ import annotations

import json
import sqlite3

from indbase_core.ids import new_prefixed_id
from indbase_core.time import utc_now_iso


def record_error(
    connection: sqlite3.Connection,
    *,
    component: str,
    error_type: str,
    message: str,
    task_id: str | None = None,
    provider_run_id: str | None = None,
    severity: str = "error",
    retryable: bool = False,
    user_message: str | None = None,
    developer_message: str | None = None,
    payload: dict[str, object] | None = None,
) -> str:
    error_id = new_prefixed_id("error")
    connection.execute(
        """
        INSERT INTO errors(
          error_id, task_id, provider_run_id, component, error_type, severity, retryable,
          user_message, developer_message, message, payload_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            error_id,
            task_id,
            provider_run_id,
            component,
            error_type,
            severity,
            1 if retryable else 0,
            user_message,
            developer_message,
            message,
            _json(payload),
            utc_now_iso(),
        ),
    )
    return error_id


def list_errors(
    connection: sqlite3.Connection,
    *,
    component: str | None = None,
    severity: str | None = None,
    limit: int = 20,
) -> list[sqlite3.Row]:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    clauses: list[str] = []
    values: list[object] = []
    if component:
        clauses.append("component = ?")
        values.append(component)
    if severity:
        clauses.append("severity = ?")
        values.append(severity)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    values.append(limit)
    return list(
        connection.execute(
            f"""
            SELECT error_id, task_id, provider_run_id, component, error_type, severity, retryable,
                   user_message, developer_message, message, payload_json, created_at
            FROM errors
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            values,
        )
    )


def get_error(connection: sqlite3.Connection, error_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT error_id, task_id, provider_run_id, component, error_type, severity, retryable,
               user_message, developer_message, message, stack, payload_json, created_at
        FROM errors
        WHERE error_id = ?
        """,
        (error_id,),
    ).fetchone()


def _json(value: dict[str, object] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
