"""Run the v0.1 release-candidate hardening gate."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

from indbase_core import db as db_module
from indbase_core.db import Migration, MigrationError, apply_migrations, connect, load_migrations


ROOT = Path.cwd()
IND_B = ROOT / ".venv" / "Scripts" / "indb.exe"


def main() -> None:
    gate_root = ROOT / ".tmp" / f"v01-release-candidate-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    gate_root.mkdir(parents=True)

    sub_gates = [
        "m3_dogfood_gate.py",
        "m4_tui_lite_gate.py",
        "m5_catalog_review_gate.py",
        "doctor_negative_gate.py",
        "metadata_consistency_gate.py",
    ]
    summaries: dict[str, dict[str, object]] = {}
    vault_roots: list[Path] = []
    for script_name in sub_gates:
        summary, dogfood_root = _run_gate_script(script_name)
        summaries[script_name] = summary
        if dogfood_root is not None and (dogfood_root / "vault").is_dir():
            vault_roots.append(dogfood_root / "vault")

    _run_migration_backup_tests(gate_root)

    aggregate = {
        "critical_doctor_findings": 0,
        "unexpected_errors": 0,
        "index_integrity_errors": 0,
        "orphan_records": 0,
        "orphan_files": 0,
        "fts_desync": 0,
        "zero_chunk_current_revisions": 0,
        "unsupported_documents_created": 0,
    }

    for summary in summaries.values():
        aggregate["critical_doctor_findings"] += int(summary.get("critical_doctor_findings", 0) or 0)
        aggregate["unexpected_errors"] += int(summary.get("unexpected_errors", 0) or 0)
        aggregate["index_integrity_errors"] += int(summary.get("index_integrity_errors", 0) or 0)
        aggregate["zero_chunk_current_revisions"] += int(summary.get("zero_chunk_current_revisions", 0) or 0)
        aggregate["unsupported_documents_created"] += int(summary.get("unsupported_documents_created", 0) or 0)

    for vault in vault_roots:
        vault_metrics = _clean_vault_metrics(vault)
        for key in aggregate:
            aggregate[key] += vault_metrics.get(key, 0)

    hard_failures = {
        key: value
        for key, value in aggregate.items()
        if key
        in {
            "critical_doctor_findings",
            "unexpected_errors",
            "index_integrity_errors",
            "zero_chunk_current_revisions",
            "unsupported_documents_created",
        }
        and value != 0
    }
    if hard_failures:
        raise RuntimeError(f"v0.1 RC hard standards failed: {hard_failures}")

    print(json.dumps(aggregate, sort_keys=True))
    print("V01_RELEASE_CANDIDATE_GATE=passed")
    print(f"DOGFOOD_ROOT={gate_root}")


def _run_gate_script(script_name: str) -> tuple[dict[str, object], Path | None]:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script_name)],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{script_name} failed with exit {completed.returncode}\n{completed.stdout}")
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    summary: dict[str, object] | None = None
    dogfood_root: Path | None = None
    for line in lines:
        if line.startswith("{") and summary is None:
            summary = json.loads(line)
        if line.startswith("DOGFOOD_ROOT="):
            dogfood_root = Path(line.split("=", 1)[1])
    if summary is None:
        raise RuntimeError(f"{script_name} did not emit a JSON summary\n{completed.stdout}")
    return summary, dogfood_root


def _clean_vault_metrics(vault: Path) -> dict[str, int]:
    doctor = subprocess.run(
        [str(IND_B), "doctor", "--vault", str(vault), "--json"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if doctor.returncode not in {0, 1}:
        raise RuntimeError(f"clean vault doctor failed for {vault}: {doctor.returncode}\n{doctor.stdout}")
    report = json.loads(doctor.stdout)
    findings = report["findings"]
    metrics = {
        "critical_doctor_findings": sum(1 for finding in findings if finding["severity"] in {"error", "critical"}),
        "orphan_records": sum(
            1
            for finding in findings
            if finding["code"] in {"orphan_source_file", "orphan_revision", "orphan_chunk"}
        ),
        "orphan_files": sum(
            1
            for finding in findings
            if finding["code"] in {"orphan_original_file", "orphan_markdown_file"}
        ),
        "fts_desync": sum(
            1
            for finding in findings
            if finding["code"] in {"fts_missing_chunk", "fts_stale_row"}
        ),
        "zero_chunk_current_revisions": 0,
        "unsupported_documents_created": 0,
    }
    db_path = vault / ".indbase" / "db.sqlite"
    with sqlite3.connect(db_path) as connection:
        metrics["zero_chunk_current_revisions"] = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM documents d
                WHERE d.current_revision_id IS NOT NULL
                  AND d.deleted_at IS NULL
                  AND NOT EXISTS (
                    SELECT 1
                    FROM chunks c
                    WHERE c.doc_id = d.doc_id
                      AND c.revision_id = d.current_revision_id
                      AND c.is_current = 1
                      AND c.deleted_at IS NULL
                  )
                """
            ).fetchone()[0]
        )
        metrics["unsupported_documents_created"] = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM documents
                WHERE source_type IS NOT NULL
                  AND source_type NOT IN ('md', 'txt', 'html', 'csv', 'json', 'docx', 'xlsx', 'pptx', 'pdf')
                """
            ).fetchone()[0]
        )
    return metrics


def _run_migration_backup_tests(root: Path) -> None:
    migrations = load_migrations()
    if len(migrations) < 2:
        raise RuntimeError("migration backup test requires at least two migrations")

    db_path = root / "migration" / "db.sqlite"
    connection = connect(db_path)
    try:
        connection.executescript(migrations[0].sql)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (migrations[0].version, _now_iso()),
        )
        connection.commit()

        applied = apply_migrations(connection, db_path)
        if applied != [migration.version for migration in migrations[1:]]:
            raise RuntimeError(f"unexpected migrations applied: {applied}")
        backups = sorted(db_path.parent.glob("db.sqlite.bak.*"))
        if not backups:
            raise RuntimeError("migration did not create a pre-migration backup")

        applied_again = apply_migrations(connection, db_path)
        if applied_again:
            raise RuntimeError(f"repeat migration was not idempotent: {applied_again}")

        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES ('9999_future', ?)",
            (_now_iso(),),
        )
        connection.commit()
        try:
            apply_migrations(connection, db_path)
        except MigrationError:
            pass
        else:
            raise RuntimeError("higher/unknown schema version was not rejected")
    finally:
        connection.close()

    failure_db_path = root / "migration_failure" / "db.sqlite"
    failure_connection = connect(failure_db_path)
    original_loader = db_module.load_migrations
    try:
        for migration in migrations:
            failure_connection.executescript(migration.sql)
            failure_connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (migration.version, _now_iso()),
            )
            failure_connection.commit()
        bad_migration = Migration(version="9999_bad", sql="THIS IS NOT SQL;")
        db_module.load_migrations = lambda: [*migrations, bad_migration]
        try:
            apply_migrations(failure_connection, failure_db_path)
        except sqlite3.Error:
            pass
        else:
            raise RuntimeError("bad migration did not fail")
        bad_applied = failure_connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = '9999_bad'"
        ).fetchone()[0]
        if int(bad_applied) != 0:
            raise RuntimeError("failed migration version was recorded")
        if not sorted(failure_db_path.parent.glob("db.sqlite.bak.*")):
            raise RuntimeError("failed migration did not create a recoverable backup")
    finally:
        db_module.load_migrations = original_loader
        failure_connection.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    main()
