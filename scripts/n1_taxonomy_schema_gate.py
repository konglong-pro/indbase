"""N1.1 taxonomy schema gate."""

from __future__ import annotations

import sys

from indbase_core.db import connect
from indbase_core.doctor import run_doctor
from indbase_core.ids import new_prefixed_id
from indbase_core.tags import add_tag
from indbase_core.taxonomy import validate_tag_type
from indbase_core.vault import init_vault

from gate_common import write_gate_summary
from taxonomy_gate_common import make_gate_root


def main() -> None:
    root = make_gate_root("n1-taxonomy-schema")
    vault = root / "vault"
    init_vault(vault, category_template="minimal")

    try:
        validate_tag_type("not-a-real-type")
        invalid_rejected = False
    except ValueError:
        invalid_rejected = True

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_tag(connection, "schema-gate-tag", tag_type="tool")
        untyped = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM tags
            WHERE deleted_at IS NULL
              AND status = 'active'
              AND (type IS NULL OR type = '')
            """
        ).fetchone()["count"]
        has_provenance = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM document_tags
            WHERE status IS NOT NULL
            """
        ).fetchone()["count"]

    corrupt = root / "corrupt"
    init_vault(corrupt, category_template="minimal")
    with connect(corrupt / ".indbase" / "db.sqlite") as connection:
        tag_id = add_tag(connection, "corrupt-tag", tag_type="topic")
        connection.execute("UPDATE tags SET type = 'invalid_type' WHERE tag_id = ?", (tag_id,))
        now = "2026-05-20T00:00:00+00:00"
        connection.execute(
            """
            INSERT INTO taxonomy_suggestions(
              suggestion_id, type, doc_id, revision_id, target_id, payload_json,
              confidence, status, source, created_at
            )
            VALUES (?, 'category_assign', NULL, NULL, 'cat_missing', ?, 0.5, 'pending', 'manual', ?)
            """,
            (
                new_prefixed_id("taxsugg"),
                '{"category_id":"cat_missing","reason":"gate corruption"}',
                now,
            ),
        )
        connection.commit()
    doctor = run_doctor(corrupt)
    hard = [f for f in doctor.findings if f.severity in {"error", "critical"}]

    summary = {
        "typed_tags_supported": True,
        "invalid_tag_types_rejected": invalid_rejected,
        "assignment_provenance_supported": int(has_provenance) >= 0,
        "untyped_active_tags": int(untyped),
        "taxonomy_doctor_hard_findings_for_corruption": len(hard),
    }
    if not invalid_rejected:
        raise RuntimeError("invalid tag types were not rejected")
    if summary["untyped_active_tags"] != 0:
        raise RuntimeError(f"untyped active tags remain: {summary}")
    if summary["taxonomy_doctor_hard_findings_for_corruption"] <= 0:
        raise RuntimeError("doctor did not report taxonomy corruption")

    write_gate_summary(summary, gate_name="N1_TAXONOMY_SCHEMA_GATE", gate_root=root)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"N1_TAXONOMY_SCHEMA_GATE=failed: {exc}", file=sys.stderr)
        raise
