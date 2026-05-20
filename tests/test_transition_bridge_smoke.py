"""Real Node transition bridge smoke tests (opt-in)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from indbase_core.db import connect
from indbase_core.output_queries import get_output_run
from indbase_core.output_service import export_source_revision
from indbase_core.paths import vault_paths
from indbase_core.transition_runtime import (
    install_runtime,
    transition_bridge_smoke_available,
)
from indbase_core.vault import init_vault
from conftest_output import insert_minimal_document


@pytest.mark.transition_smoke
def test_real_node_bridge_contract_smoke(tmp_path: Path) -> None:
    if os.environ.get("INDBASE_TRANSITION_SMOKE") != "1":
        pytest.skip("Set INDBASE_TRANSITION_SMOKE=1 to run real transition bridge smoke tests.")

    vault = tmp_path / "vault"
    init_vault(vault)
    if os.environ.get("INDBASE_TRANSITION_SMOKE_INSTALL") == "1":
        install_runtime(vault, run_npm_install=True)
    else:
        install_runtime(vault, run_npm_install=False)

    available, reason = transition_bridge_smoke_available(vault)
    if not available:
        pytest.skip(reason)

    connection = connect(vault_paths(vault).db_path)
    try:
        doc_id = insert_minimal_document(
            connection,
            vault,
            body="# Smoke\n\n```text\nhello transition\n```\n",
        )
        result = export_source_revision(connection, vault, doc_id=doc_id, bridge_runner=None)
        assert result.status in {"succeeded", "partial"}
        view = get_output_run(connection, result.output_run_id)
        assert view is not None
        assert view.evidence_manifest_path
        normalized = vault_paths(vault).outputs_exports / result.output_run_id / "normalized.md"
        assert normalized.is_file()
        assert "```text" in normalized.read_text(encoding="utf-8")
    finally:
        connection.close()
