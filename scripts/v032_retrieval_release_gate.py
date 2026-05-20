"""v0.3.2 deterministic retrieval release gate."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from gate_common import TRUSTED_NEEDLE, configure_v02_vault, doctor_hard_metrics, install_deterministic_swallow_stub, write_gate_summary, ROOT
from indbase_core.categories import add_category
from indbase_core.db import connect
from indbase_core.documents import set_document_category
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.profile import build_document_profile
from indbase_core.retrieval import retrieve_chunks
from indbase_core.tags import add_document_tag, add_tag
from indbase_core.taxonomy_suggestions import insert_category_assign_suggestion
from indbase_core.vault import init_vault


def _ingest(vault: Path, path: Path) -> str:
    result = run_m3_ingest_pipeline(vault, path)
    if result.written_revisions < 1:
        raise RuntimeError(f"ingest failed for {path}: {result}")
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        return connection.execute(
            "SELECT doc_id FROM documents WHERE normalized_source_uri LIKE ?",
            (f"%{path.name}",),
        ).fetchone()["doc_id"]


def main() -> None:
    root = ROOT / ".tmp" / f"v032-retrieval-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    root.mkdir(parents=True)
    install_deterministic_swallow_stub()
    vault = root / "vault"
    init_vault(vault, category_template="academic")

    primary = root / "primary.md"
    primary.write_text(
        "# AI Research\n\n"
        "Artificial intelligence LLM RAG retrieval augmented generation SQLite FTS hybrid search.\n"
        f"Needle: {TRUSTED_NEEDLE}\n",
        encoding="utf-8",
    )
    long_doc = root / "long.md"
    long_doc.write_text(
        "# Long Document\n\n"
        + "\n\n".join(
            f"Section {index} discusses retrieval intelligence hybrid search taxonomy evidence."
            for index in range(12)
        )
        + f"\n\nNeedle: {TRUSTED_NEEDLE}\n",
        encoding="utf-8",
    )
    configure_v02_vault(vault, swallow_ingest=True, transition_output=False, min_markdown_chars=80)
    doc_primary = _ingest(vault, primary)
    doc_long = _ingest(vault, long_doc)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_tag(connection, "rag", tag_type="method")
        add_document_tag(connection, doc_primary, "rag")
        set_document_category(connection, doc_primary, "cat_computer_science")
        build_document_profile(connection, doc_primary)
        # Leave doc_long without a profile to exercise profile_missing warnings (R4).
        insert_category_assign_suggestion(
            connection,
            doc_id=doc_primary,
            revision_id=connection.execute(
                "SELECT current_revision_id FROM documents WHERE doc_id = ?",
                (doc_primary,),
            ).fetchone()["current_revision_id"],
            category_id="cat_math_statistics",
            confidence=0.9,
            reason="pending must not affect retrieval",
        )
        result = retrieve_chunks(
            connection,
            "retrieval hybrid search",
            top_k=8,
            per_doc_limit=3,
            mode="hybrid",
        )
        filtered = retrieve_chunks(
            connection,
            "retrieval tag:rag",
            top_k=5,
            mode="hybrid",
        )
        citations = connection.execute("SELECT COUNT(*) AS count FROM citations").fetchone()["count"]
        search_results = connection.execute("SELECT COUNT(*) AS count FROM search_results").fetchone()["count"]
        item_rows = connection.execute(
            "SELECT COUNT(*) AS count FROM retrieval_items WHERE retrieval_run_id = ?",
            (result.retrieval_run_id,),
        ).fetchone()["count"]

    doctor = doctor_hard_metrics(vault)
    per_doc_ok = True
    counts: dict[str, int] = {}
    for item in result.items:
        counts[item.doc_id] = counts.get(item.doc_id, 0) + 1
    per_doc_ok = all(count <= 3 for count in counts.values()) and len(counts) >= 1

    summary = {
        "retrieval_runs_persisted": 1,
        "retrieval_items_persisted": int(item_rows) > 0,
        "explicit_tag_filter_items": len(filtered.items),
        "taxonomy_boost_reasons": any("tag_match" in item.reasons or "feature_atom_match" in item.reasons for item in result.items),
        "profile_missing_warnings": any("profile_missing" in w for w in result.warnings),
        "per_doc_limit_enforced": per_doc_ok,
        "citations_created": int(citations),
        "search_results_created": int(search_results),
        "doctor_hard_findings": doctor["critical_doctor_findings"],
        "status": result.status,
    }
    if summary["citations_created"] != 0 or summary["search_results_created"] != 0:
        raise RuntimeError(f"retrieval wrote forbidden tables: {summary}")
    if not summary["retrieval_items_persisted"]:
        raise RuntimeError(f"no retrieval items persisted: {summary}")
    if doctor["critical_doctor_findings"]:
        raise RuntimeError(f"doctor hard findings: {doctor}")
    if summary["explicit_tag_filter_items"] < 1:
        raise RuntimeError(f"tag filter produced no items: {summary}")
    if not summary["taxonomy_boost_reasons"]:
        raise RuntimeError(f"expected taxonomy boost reasons: {summary}")
    if not summary["profile_missing_warnings"]:
        raise RuntimeError(
            f"expected profile_missing warnings for unprofiled documents: {summary}"
        )

    write_gate_summary(summary, gate_name="V032_RETRIEVAL_RELEASE_GATE", gate_root=root)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"V032_RETRIEVAL_RELEASE_GATE=failed: {exc}", file=sys.stderr)
        raise
