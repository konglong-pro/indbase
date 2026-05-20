"""Shared helpers for v0.3.1 taxonomy N1 gate scripts."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from gate_common import TRUSTED_NEEDLE, configure_v02_vault, install_deterministic_swallow_stub

from indbase_core.category_manager import suggest_category_assignments
from indbase_core.db import connect
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.profile import build_document_profile
from indbase_core.tags import add_tag
from indbase_core.taxonomy_manager import analyze_document_taxonomy
from indbase_core.vault import init_vault

from gate_common import ROOT


AI_SOURCE_TEXT = (
    "# AI Research\n\n"
    "AI research uses LLM RAG retrieval augmented generation and SQLite FTS database tooling.\n"
    "Hybrid search patterns support knowledge base design.\n"
    f"\nNeedle: {TRUSTED_NEEDLE}\n"
)


def make_gate_root(prefix: str) -> Path:
    root = ROOT / ".tmp" / f"{prefix}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _doc_id_for_source(connection, source: Path) -> str:
    row = connection.execute(
        """
        SELECT doc_id
        FROM documents
        WHERE normalized_source_uri LIKE ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (f"%{source.name}",),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"ingested document not found for source: {source}")
    return str(row["doc_id"])


def ingest_gate_source(vault: Path, source: Path) -> None:
    install_deterministic_swallow_stub()
    configure_v02_vault(vault, swallow_ingest=True, transition_output=False, min_markdown_chars=80)
    result = run_m3_ingest_pipeline(vault, source)
    if result.written_revisions < 1:
        raise RuntimeError(f"expected revision from ingest: {result}")


def build_profiled_vault(root: Path) -> dict[str, str]:
    vault = root / "vault"
    source = root / "ai-research.md"
    source.write_text(AI_SOURCE_TEXT, encoding="utf-8")
    init_vault(vault, category_template="academic")
    ingest_gate_source(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc_id = _doc_id_for_source(connection, source)
        add_tag(connection, "rag", tag_type="method")
        build_document_profile(connection, doc_id)
        analyze_document_taxonomy(connection, doc_id)
        suggest_category_assignments(connection, doc_id=doc_id, min_confidence=0.5)
    return {"vault": str(vault), "doc_id": doc_id, "source": str(source)}
