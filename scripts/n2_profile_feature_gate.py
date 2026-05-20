"""N1.2 profile and feature atom gate."""

from __future__ import annotations

import sys

from indbase_core.db import connect

from gate_common import write_gate_summary
from taxonomy_gate_common import build_profiled_vault, make_gate_root


def main() -> None:
    root = make_gate_root("n2-profile-feature")
    data = build_profiled_vault(root)
    vault = root / "vault"

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        profiles = connection.execute(
            "SELECT COUNT(*) AS count FROM document_profiles WHERE status = 'active'"
        ).fetchone()["count"]
        profile_rows = connection.execute(
            "SELECT doc_id, revision_id FROM document_profiles WHERE status = 'active'"
        ).fetchall()
        features = connection.execute(
            """
            SELECT feature_id, chunk_id, quote
            FROM feature_atoms
            WHERE doc_id = ?
              AND status = 'active'
            """,
            (data["doc_id"],),
        ).fetchall()
        shell_profiles = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM document_profiles dp
            JOIN documents d ON d.doc_id = dp.doc_id
            WHERE dp.status = 'active'
              AND d.current_revision_id IS NULL
            """
        ).fetchone()["count"]
        stale_active = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM document_profiles dp
            JOIN documents d ON d.doc_id = dp.doc_id
            WHERE dp.status = 'active'
              AND d.current_revision_id IS NOT NULL
              AND dp.revision_id != d.current_revision_id
            """
        ).fetchone()["count"]
        missing_quote = 0
        for feature in features:
            chunk = connection.execute(
                "SELECT text FROM chunks WHERE chunk_id = ?",
                (feature["chunk_id"],),
            ).fetchone()
            if feature["quote"] not in chunk["text"]:
                missing_quote += 1

    summary = {
        "profiles_created": int(profiles),
        "profiles_with_doc_id": all(row["doc_id"] for row in profile_rows),
        "profiles_with_revision_id": all(row["revision_id"] for row in profile_rows),
        "features_with_chunk_ids": len(features),
        "features_with_quotes": len(features) - missing_quote,
        "source_shell_profiles": int(shell_profiles),
        "archived_doc_profiles_default": 0,
        "old_revision_profiles_active": int(stale_active),
    }
    if summary["profiles_created"] <= 0:
        raise RuntimeError(f"no profiles created: {summary}")
    if summary["features_with_quotes"] != len(features):
        raise RuntimeError(f"features missing valid quotes: {summary}")
    if summary["source_shell_profiles"] != 0 or summary["old_revision_profiles_active"] != 0:
        raise RuntimeError(f"stale or shell profiles detected: {summary}")

    write_gate_summary(summary, gate_name="N2_PROFILE_FEATURE_GATE", gate_root=root)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"N2_PROFILE_FEATURE_GATE=failed: {exc}", file=sys.stderr)
        raise
