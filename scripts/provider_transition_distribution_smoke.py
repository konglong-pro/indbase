"""Installed-wheel transition bridge distribution smoke."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap

from provider_smoke_common import build_wheel, clean_env, create_venv


def main() -> None:
    if shutil.which("node") is None:
        raise SystemExit("Node.js is required for provider transition distribution smoke.")

    with tempfile.TemporaryDirectory(prefix="indbase-transition-dist-") as raw_tmp:
        tmp = Path(raw_tmp)
        wheel = build_wheel(tmp)
        venv_python = create_venv(tmp / "venv")
        env = clean_env()
        subprocess.run(
            [str(venv_python), "-m", "pip", "install", "--no-deps", str(wheel)],
            cwd=tmp,
            env=env,
            check=True,
        )
        script = textwrap.dedent(
            """
            from pathlib import Path

            from indbase_core.chunker import chunk_revision
            from indbase_core.conversion import hash_markdown
            from indbase_core.db import connect
            from indbase_core.output_service import normalize_replace_current
            from indbase_core.paths import vault_paths
            from indbase_core.transition_runtime import install_runtime
            from indbase_core.vault import init_vault

            vault = Path("vault").resolve()
            init_vault(vault)
            install_runtime(vault, run_npm_install=False)
            paths = vault_paths(vault)
            connection = connect(paths.db_path)
            doc_id = "doc_20260609_abc123"
            revision_id = "rev_doc_20260609_abc123_0001"
            body = "# Smoke\\n\\nTransition distribution smoke body.\\n"
            markdown_path = paths.source_markdown_path(doc_id, "smoke", 1)
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            rendered = (
                "---\\n"
                "schema_version: indbase.source.v1\\n"
                "type: source_document\\n"
                f"doc_id: {doc_id}\\n"
                f"revision_id: {revision_id}\\n"
                "title: Smoke\\n"
                "---\\n\\n"
                f"{body}"
            )
            markdown_path.write_text(rendered, encoding="utf-8")
            rel_path = paths.relative_to_vault(markdown_path)
            now = "2026-06-09T00:00:00+00:00"
            connection.execute(
                '''
                INSERT INTO documents (
                  doc_id, title, filename_slug, status, ingest_status,
                  current_revision_id, canonical_path, created_at, updated_at
                ) VALUES (?, 'Smoke', 'smoke', 'active', 'revisioned', ?, ?, ?, ?)
                ''',
                (doc_id, revision_id, rel_path, now, now),
            )
            connection.execute(
                '''
                INSERT INTO document_revisions (
                  revision_id, doc_id, sequence, markdown_path, content_hash,
                  converter_name, converter_version, promotion_status,
                  created_at, updated_at
                ) VALUES (?, ?, 1, ?, ?, 'smoke', 'smoke', 'promoted', ?, ?)
                ''',
                (revision_id, doc_id, rel_path, hash_markdown(body), now, now),
            )
            connection.commit()
            chunk_revision(connection, vault, revision_id)
            connection.commit()

            result = normalize_replace_current(connection, vault, doc_id=doc_id)
            assert result.status == "succeeded"
            row = connection.execute(
                '''
                SELECT provider_status, evidence_status, evidence_root
                FROM provider_runs
                WHERE output_run_id = ?
                ''',
                (result.output_run_id,),
            ).fetchone()
            assert row["provider_status"] == "success"
            assert row["evidence_status"] == "copied"
            evidence_root = vault / row["evidence_root"]
            assert (evidence_root / "evidence_index.json").is_file()
            assert (evidence_root / "transition_manifest.json").is_file()
            assert (evidence_root / "transition_trace.jsonl").is_file()
            connection.close()
            """
        )
        subprocess.run([str(venv_python), "-c", script], cwd=tmp, env=env, check=True)
    print("PROVIDER_TRANSITION_DISTRIBUTION_SMOKE: ok")


if __name__ == "__main__":
    sys.exit(main())
