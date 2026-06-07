"""Layer E (optional): v0.3.2 retrieval real-corpus dogfood.

Stages repo-local content (or INDB_REAL_CORPUS), ingests with swallow gate stub,
profiles a subset of documents, then runs `retrieve_chunks` scenarios and writes
a structured report including observed failure modes.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from gate_common import TRUSTED_NEEDLE, configure_v02_vault, install_deterministic_swallow_stub, ROOT
from mvp_closeout_common import stage_real_corpus

from indbase_core.db import connect
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.profile import build_document_profile
from indbase_core.retrieval import retrieve_chunks
from indbase_core.tags import add_document_tag, add_tag
from indbase_core.vault import init_vault


def _inject_needle(sources: Path) -> None:
    for path in sources.rglob("*"):
        if path.suffix.casefold() not in {".md", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if TRUSTED_NEEDLE in text:
            continue
        if len(text.strip()) < 80:
            text = text.rstrip() + "\n\n" + ("Supporting paragraph. " * 10)
        path.write_text(text.rstrip() + f"\n\nNeedle: {TRUSTED_NEEDLE}\n", encoding="utf-8")


def _profile_subset(connection, *, limit: int) -> tuple[int, int]:
    rows = connection.execute(
        """
        SELECT doc_id
        FROM documents
        WHERE status = 'active'
          AND deleted_at IS NULL
          AND ingest_status = 'revisioned'
          AND current_revision_id IS NOT NULL
        ORDER BY created_at, doc_id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    built = 0
    for row in rows:
        try:
            build_document_profile(connection, str(row["doc_id"]))
            built += 1
        except ValueError:
            pass
    total = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM documents
        WHERE status = 'active'
          AND deleted_at IS NULL
          AND ingest_status = 'revisioned'
        """
    ).fetchone()["count"]
    return built, int(total)


def _run_scenario(connection, label: str, query: str, **kwargs: object) -> dict[str, object]:
    try:
        result = retrieve_chunks(connection, query, **kwargs)
        items = [
            {
                "rank": item.rank,
                "doc_id": item.doc_id,
                "chunk_id": item.chunk_id,
                "final_score": item.final_score,
                "reasons": list(item.reasons),
                "quote_len": len(item.quote),
                "quote_preview": item.quote[:120],
            }
            for item in result.items
        ]
        unique_docs = len({item["doc_id"] for item in items})
        return {
            "label": label,
            "query": query,
            "status": result.status,
            "result_count": result.result_count,
            "warnings": list(result.warnings),
            "unique_docs": unique_docs,
            "items": items,
            "error": None,
        }
    except ValueError as exc:
        return {"label": label, "query": query, "status": "error", "error": str(exc), "items": []}


def _annotate_bad_examples(report: dict[str, object]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    scenarios = report.get("scenarios", [])
    if not isinstance(scenarios, list):
        return findings

    for scenario in scenarios:
        if not isinstance(scenario, dict):
            continue
        label = str(scenario.get("label", ""))
        if scenario.get("status") == "failed" and scenario.get("result_count", 0) == 0:
            findings.append(
                {
                    "kind": "poor_recall",
                    "scenario": label,
                    "detail": f"No items for query: {scenario.get('query')}",
                }
            )
        if scenario.get("error"):
            findings.append(
                {
                    "kind": "filter_or_parse_failure",
                    "scenario": label,
                    "detail": str(scenario["error"]),
                }
            )
        items = scenario.get("items", [])
        if isinstance(items, list):
            if items and all("profile_missing" in item.get("reasons", []) for item in items if isinstance(item, dict)):
                findings.append(
                    {
                        "kind": "profile_missing_dominant",
                        "scenario": label,
                        "detail": "All items lack profile-based boosts (expected for unprofiled corpus).",
                    }
                )
            doc_ids = [str(item.get("doc_id")) for item in items if isinstance(item, dict)]
            if len(doc_ids) != len(set(doc_ids)) and len(doc_ids) > 1:
                findings.append(
                    {
                        "kind": "duplicate_documents",
                        "scenario": label,
                        "detail": f"Multiple chunks from same doc in top results: {doc_ids}",
                    }
                )
            for item in items:
                if not isinstance(item, dict):
                    continue
                if int(item.get("quote_len", 0)) < 20:
                    findings.append(
                        {
                            "kind": "weak_quote",
                            "scenario": label,
                            "detail": f"Short quote at rank {item.get('rank')}: {item.get('quote_preview')}",
                        }
                    )
                reasons = item.get("reasons", [])
                if isinstance(reasons, list) and item.get("final_score", 0) > 0.5:
                    boost_reasons = [r for r in reasons if r not in {"base_hybrid_match", "base_fts_match", "base_vector_match", "profile_missing"}]
                    if len(boost_reasons) >= 2:
                        findings.append(
                            {
                                "kind": "boost_stack",
                                "scenario": label,
                                "detail": f"Multiple taxonomy boosts at rank {item.get('rank')}: {boost_reasons}",
                            }
                        )

    baseline = next((s for s in scenarios if isinstance(s, dict) and s.get("label") == "english_baseline"), None)
    tag_run = next((s for s in scenarios if isinstance(s, dict) and s.get("label") == "tag_filter_rag"), None)
    if isinstance(baseline, dict) and isinstance(tag_run, dict):
        if baseline.get("result_count", 0) > 0 and tag_run.get("result_count", 0) == 0:
            findings.append(
                {
                    "kind": "filter_false_negative",
                    "scenario": "tag_filter_rag",
                    "detail": "tag:rag returned nothing while baseline had hits (tag may be unassigned on corpus).",
                }
            )
        if baseline.get("result_count", 0) == 0 and tag_run.get("result_count", 0) > 0:
            findings.append(
                {
                    "kind": "filter_too_strict",
                    "scenario": "tag_filter_rag",
                    "detail": "Unexpected: tag filter returned items when baseline was empty.",
                }
            )

    return findings


def main() -> None:
    root = ROOT / ".tmp" / f"v032-retrieval-dogfood-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    root.mkdir(parents=True)
    install_deterministic_swallow_stub()
    corpus = stage_real_corpus(root, target_files=85)
    sources = Path(str(corpus["sources"]))
    _inject_needle(sources)

    vault = root / "vault"
    init_vault(vault, category_template="indbase_default_v1")
    configure_v02_vault(vault, swallow_ingest=True, transition_output=False, min_markdown_chars=80)
    ingest = run_m3_ingest_pipeline(vault, sources, recursive=True)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        add_tag(connection, "rag", tag_type="method")
        profiled, doc_total = _profile_subset(connection, limit=12)
        profiled_docs = connection.execute(
            """
            SELECT doc_id FROM documents
            WHERE status = 'active' AND ingest_status = 'revisioned'
            ORDER BY created_at, doc_id
            LIMIT 12
            """
        ).fetchall()
        for row in profiled_docs[:5]:
            add_document_tag(connection, str(row["doc_id"]), "rag")

        scenarios = [
            _run_scenario(connection, "english_baseline", "hybrid search retrieval database", top_k=8, mode="hybrid"),
            _run_scenario(connection, "chinese_query", "检索 增强 生成 知识库", top_k=8, mode="hybrid"),
            _run_scenario(connection, "mixed_query", "RAG 检索 hybrid search SQLite", top_k=8, mode="hybrid"),
            _run_scenario(connection, "tag_filter_rag", "retrieval tag:rag", top_k=8, mode="hybrid"),
            _run_scenario(
                connection,
                "category_filter_cs",
                "category:计算机科学 hybrid search",
                top_k=8,
                mode="hybrid",
            ),
            _run_scenario(
                connection,
                "long_doc_diversity",
                "Supporting paragraph retrieval intelligence",
                top_k=12,
                per_doc_limit=2,
                mode="fts",
            ),
            _run_scenario(
                connection,
                "unprofiled_corpus",
                "TRUSTED_GATE_NEEDLE",
                top_k=10,
                mode="fts",
            ),
        ]

    report = {
        "gate": "v032_retrieval_real_corpus_dogfood",
        "source_mode": corpus.get("source_mode"),
        "input_files": corpus.get("input_files"),
        "written_revisions": ingest.written_revisions,
        "documents_total": doc_total,
        "documents_profiled": profiled,
        "vault": str(vault),
        "scenarios": scenarios,
    }
    report["bad_examples"] = _annotate_bad_examples(report)

    report_path = root / "v032_retrieval_dogfood_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = ROOT / "docs/testing/archive/v0.3.2-retrieval-dogfood-report.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        "doc_type: dogfood_report",
        "phase_id: v0.3.2-retrieval-intelligence-foundation",
        "status: archived",
        "canonical: true",
        "read_by_default: false",
        "related_plan: docs/planning/archive/v0.3.2-retrieval-intelligence/retrieval-intelligence-foundation.plan.md",
        "---",
        "",
        "# v0.3.2 Retrieval Dogfood Report",
        "",
        f"- Generated: {datetime.now().isoformat()}",
        "- Vault: disposable local dogfood vault (path intentionally not recorded)",
        f"- Corpus: {corpus.get('source_mode')} ({corpus.get('input_files')} files)",
        f"- Ingest revisions: {ingest.written_revisions}",
        f"- Profiled / total docs: {profiled} / {doc_total}",
        "",
        "## Scenarios",
        "",
    ]
    for scenario in scenarios:
        lines.append(f"### {scenario['label']}")
        lines.append(f"- Query: `{scenario.get('query', '')}`")
        lines.append(f"- Status: {scenario.get('status')} | Items: {scenario.get('result_count', 0)}")
        if scenario.get("warnings"):
            lines.append(f"- Warning count: {len(scenario['warnings'])}")
        if scenario.get("error"):
            lines.append(f"- Error: {scenario['error']}")
        lines.append("")
    lines.append("## Observed issues (heuristic)")
    lines.append("")
    if report["bad_examples"]:
        for item in report["bad_examples"]:
            lines.append(f"- **{item['kind']}** ({item['scenario']})")
    else:
        lines.append("- No automated bad-example heuristics fired on this run.")
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps({"report": str(report_path), "markdown": str(md_path), "bad_examples": len(report["bad_examples"])}, indent=2))
    print(f"V032_RETRIEVAL_DOGFOOD=passed")
    print(f"DOGFOOD_ROOT={root}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"V032_RETRIEVAL_DOGFOOD=failed: {exc}", file=sys.stderr)
        raise
