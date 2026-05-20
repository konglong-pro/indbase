"""Real Node transition smoke gate (opt-in). Requires INDBASE_TRANSITION_SMOKE=1."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from gate_common import TRUSTED_NEEDLE, env_enabled, gate_skip, write_gate_summary, ROOT

from indbase_core.config import load_config
from indbase_core.db import connect
from indbase_core.output_service import export_source_revision
from indbase_core.paths import vault_paths
from indbase_core.transition_runtime import install_runtime, transition_bridge_smoke_available
from indbase_core.vault import init_vault


def main() -> None:
    gate_root = ROOT / ".tmp" / f"v02-transition-smoke-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    vault = gate_root / "vault"
    available, reason = transition_bridge_smoke_available(vault)
    if not env_enabled("INDBASE_TRANSITION_SMOKE"):
        summary = gate_skip(
            "D",
            "v02_transition_smoke",
            "Set INDBASE_TRANSITION_SMOKE=1 to run real transition bridge smoke.",
        )
        print(json.dumps(summary, sort_keys=True))
        print("V02_TRANSITION_SMOKE_GATE=skipped")
        return

    init_vault(vault, category_template="minimal")
    if env_enabled("INDBASE_TRANSITION_SMOKE_INSTALL"):
        install_runtime(vault, run_npm_install=True)
    else:
        install_runtime(vault, run_npm_install=False)
    available, reason = transition_bridge_smoke_available(vault)
    if not available:
        summary = gate_skip("D", "v02_transition_smoke", reason)
        print(json.dumps(summary, sort_keys=True))
        print("V02_TRANSITION_SMOKE_GATE=skipped")
        return

    body = (
        "# Transition Smoke\n\n"
        "```text\nhello transition gate\n```\n\n"
        + (f"Paragraph with {TRUSTED_NEEDLE}. " * 30)
    )
    paths = vault_paths(vault)
    paths.ensure_layout()
    doc_id = "doc_20250101_5f3a9c"
    revision_id = "rev_doc_20250101_5f3a9c_0001"
    markdown_path = paths.source_markdown_path(doc_id, "transition-smoke", 1)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown = (
        f"---\nschema_version: indbase.source.v1\ntype: source_document\n"
        f"doc_id: {doc_id}\nrevision_id: {revision_id}\ntitle: Smoke\n---\n\n{body}"
    )
    markdown_path.write_text(markdown, encoding="utf-8")
    rel = paths.relative_to_vault(markdown_path)

    connection = connect(paths.db_path)
    try:
        connection.execute(
            """
            INSERT INTO documents (
              doc_id, title, filename_slug, status, ingest_status, current_revision_id,
              canonical_path, created_at, updated_at
            ) VALUES (?, 'Smoke', 'transition-smoke', 'active', 'revisioned', ?, ?, '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')
            """,
            (doc_id, revision_id, rel),
        )
        connection.execute(
            """
            INSERT INTO document_revisions (
              revision_id, doc_id, sequence, markdown_path, content_hash,
              converter_name, converter_version, promotion_status, created_at, updated_at
            ) VALUES (?, ?, 1, ?, 'hash', 'test', 'test', 'promoted', '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')
            """,
            (revision_id, doc_id, rel),
        )
        connection.commit()
        result = export_source_revision(connection, vault, doc_id=doc_id, bridge_runner=None)
        if result.status not in {"succeeded", "partial"}:
            raise RuntimeError(f"transition smoke export failed: {result}")
        normalized = paths.outputs_exports / result.output_run_id / "normalized.md"
        if not normalized.is_file():
            raise RuntimeError("transition smoke missing normalized.md")
        if "```text" not in normalized.read_text(encoding="utf-8"):
            raise RuntimeError("transition smoke normalized.md missing fenced code block")
        evidence = paths.output_run_evidence_dir(result.output_run_id) / "transition_manifest.json"
        if not evidence.is_file():
            raise RuntimeError("transition smoke missing archived evidence manifest")
    finally:
        connection.close()

    summary = {
        "layer": "D",
        "gate": "v02_transition_smoke",
        "output_run_id": result.output_run_id,
        "output_status": result.status,
        "transition_output": load_config(paths.config_path).features.transition_output,
    }
    write_gate_summary(summary, gate_name="V02_TRANSITION_SMOKE_GATE", gate_root=gate_root)


if __name__ == "__main__":
    main()
