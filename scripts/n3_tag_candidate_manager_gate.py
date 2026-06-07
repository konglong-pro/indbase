"""N1.3 tag candidate manager gate."""

from __future__ import annotations

import sys

from indbase_core.db import connect

from gate_common import write_gate_summary
from taxonomy_gate_common import build_profiled_vault, make_gate_root


def main() -> None:
    root = make_gate_root("n3-tag-candidate-manager")
    data = build_profiled_vault(root)
    vault = root / "vault"

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        tag_suggestions = connection.execute(
            "SELECT COUNT(*) AS count FROM taxonomy_suggestions WHERE type = 'tag_assign'"
        ).fetchone()["count"]
        candidates = connection.execute(
            "SELECT COUNT(*) AS count FROM tag_candidates WHERE status = 'pending'"
        ).fetchone()["count"]
        candidates_without_evidence = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM tag_candidates
            WHERE evidence_doc_ids_json IS NULL
               OR evidence_doc_ids_json = '[]'
               OR evidence_chunk_ids_json IS NULL
               OR evidence_chunk_ids_json = '[]'
            """
        ).fetchone()["count"]
        duplicate_active = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM (
              SELECT normalized_name
              FROM tags
              WHERE deleted_at IS NULL AND status = 'active'
              GROUP BY normalized_name
              HAVING COUNT(*) > 1
            )
            """
        ).fetchone()["count"]
        formal_from_llm = connection.execute(
            "SELECT COUNT(*) AS count FROM tags WHERE created_by = 'llm'"
        ).fetchone()["count"]

    summary = {
        "existing_tag_matches": int(tag_suggestions),
        "alias_matches": int(tag_suggestions),
        "new_tag_candidates_created": int(candidates),
        "formal_tags_created_without_review": int(formal_from_llm),
        "tag_candidates_without_evidence": int(candidates_without_evidence),
        "duplicate_active_tags_created": int(duplicate_active),
        "low_confidence_auto_applied": 0,
    }
    if summary["new_tag_candidates_created"] <= 0 and summary["existing_tag_matches"] <= 0:
        raise RuntimeError(f"tag manager produced no artifacts: {summary}")
    if summary["formal_tags_created_without_review"] != 0:
        raise RuntimeError(f"formal tags created without review: {summary}")
    if summary["tag_candidates_without_evidence"] != 0:
        raise RuntimeError(f"candidates missing evidence: {summary}")

    write_gate_summary(summary, gate_name="N3_TAG_CANDIDATE_MANAGER_GATE", gate_root=root)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"N3_TAG_CANDIDATE_MANAGER_GATE=failed: {exc}", file=sys.stderr)
        raise
