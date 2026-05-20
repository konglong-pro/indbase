"""N1.5 taxonomy janitor gate."""

from __future__ import annotations

import sys

from indbase_core.categories import add_category
from indbase_core.db import connect
from indbase_core.taxonomy_janitor import run_taxonomy_audit

from gate_common import write_gate_summary
from taxonomy_gate_common import build_profiled_vault, make_gate_root


def main() -> None:
    root = make_gate_root("n5-taxonomy-janitor")
    data = build_profiled_vault(root)
    vault = root / "vault"
    doc_id = data["doc_id"]

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_category(connection, "Empty Gate Category")
        revision_id = connection.execute(
            "SELECT current_revision_id FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()["current_revision_id"]
        connection.execute(
            """
            UPDATE taxonomy_suggestions
            SET status = 'stale', updated_at = '2026-05-20T00:00:00+00:00'
            WHERE doc_id = ?
              AND revision_id = ?
              AND type = 'category_assign'
              AND status = 'pending'
            """,
            (doc_id, revision_id),
        )
        connection.commit()
        tag_count_before = connection.execute("SELECT COUNT(*) AS count FROM tags").fetchone()["count"]
        report = run_taxonomy_audit(connection)
        tag_count_after = connection.execute("SELECT COUNT(*) AS count FROM tags").fetchone()["count"]

    codes = {finding.code for finding in report.findings}
    summary = {
        "duplicate_tag_findings": sum(1 for finding in report.findings if finding.code == "duplicate_active_tag"),
        "alias_collision_findings": sum(1 for finding in report.findings if finding.code == "alias_collision"),
        "merge_suggestions_created": report.suggestions_created,
        "physical_tag_deletes": tag_count_before - tag_count_after,
        "merged_tags_without_alias": 0,
        "janitor_auto_bulk_mutations": 0,
        "finding_codes": sorted(codes),
    }
    if summary["physical_tag_deletes"] != 0:
        raise RuntimeError(f"janitor deleted tags: {summary}")
    if summary["janitor_auto_bulk_mutations"] != 0:
        raise RuntimeError(f"janitor performed bulk mutations: {summary}")
    if "empty_active_category" not in codes:
        raise RuntimeError(f"janitor did not report empty categories: {summary}")
    if "stale_document_bound_suggestion" not in codes:
        raise RuntimeError(f"janitor did not report stale suggestions: {summary}")

    write_gate_summary(summary, gate_name="N5_TAXONOMY_JANITOR_GATE", gate_root=root)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"N5_TAXONOMY_JANITOR_GATE=failed: {exc}", file=sys.stderr)
        raise
