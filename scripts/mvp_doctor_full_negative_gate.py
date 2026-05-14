"""Run full-system doctor negative checks for MVP release close-out."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import sqlite3

import indbase_core.normalizers as normalizers
from indbase_core.cards import accept_candidate_card, generate_candidate_card
from indbase_core.categories import add_category
from indbase_core.db import connect
from indbase_core.doctor import run_doctor
from indbase_core.documents import set_document_category
from indbase_core.embeddings import rebuild_vector_index
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.tags import add_document_tag
from indbase_core.time import utc_now_iso
from indbase_core.translations import translate_full_document
from indbase_core.vault import init_vault


ROOT = Path.cwd()


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    expected_codes: frozenset[str]
    detected_codes: frozenset[str]
    findings: tuple[dict[str, str], ...]


def main() -> None:
    root = ROOT / ".tmp" / f"mvp-doctor-full-negative-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    root.mkdir(parents=True)

    original_markitdown = normalizers._run_markitdown_file
    try:
        scenarios = [
            _missing_original(root / "missing-original"),
            _missing_source_markdown(root / "missing-source-markdown"),
            _invalid_current_revision(root / "invalid-current-revision"),
            _missing_chunks(root / "missing-chunks"),
            _cleared_fts(root / "cleared-fts"),
            _missing_vector_index_record(root / "missing-vector-index-record"),
            _orphan_embedding(root / "orphan-embedding"),
            _source_shell_marked_searchable(root / "source-shell-searchable"),
            _missing_translation_output(root / "missing-translation-output"),
            _orphan_translation_record(root / "orphan-translation-record"),
            _missing_accepted_atomic_note(root / "missing-accepted-atomic-note"),
            _orphan_atomic_note(root / "orphan-atomic-note"),
            _candidate_card_source_chunk_missing(root / "candidate-source-missing"),
            _accepted_card_without_sources(root / "accepted-without-sources"),
            _uncited_accepted_claim(root / "uncited-accepted-claim"),
            _schema_version_mismatch(root / "schema-version-mismatch"),
            _config_missing(root / "config-missing"),
            _category_tag_fts_stale(root / "category-tag-fts-stale"),
        ]
    finally:
        normalizers._run_markitdown_file = original_markitdown

    summary = _summarize(scenarios)
    hard_zero = {
        "missing_expected_findings": summary["missing_expected_findings"],
        "weak_error_severity_findings": summary["weak_error_severity_findings"],
        "missing_vector_index_record_undetected": summary["missing_vector_index_record_undetected"],
        "category_tag_fts_stale_undetected": summary["category_tag_fts_stale_undetected"],
        "missing_generated_outputs_undetected": summary["missing_generated_outputs_undetected"],
    }
    if any(value != 0 for value in hard_zero.values()):
        raise RuntimeError(f"MVP doctor full negative hard metrics failed: {hard_zero}; summary={summary}")

    print(json.dumps(summary, sort_keys=True))
    print("MVP_DOCTOR_FULL_NEGATIVE_GATE=passed")
    print(f"DOGFOOD_ROOT={root}")


def _missing_original(root: Path) -> ScenarioResult:
    vault, data = _vault_with_doc(root)
    (vault / data["original_path"]).unlink()
    return _assert_doctor_finds("missing_original", vault, {"missing_original_file"})


def _missing_source_markdown(root: Path) -> ScenarioResult:
    vault, data = _vault_with_doc(root)
    (vault / data["canonical_path"]).unlink()
    return _assert_doctor_finds(
        "missing_source_markdown",
        vault,
        {"missing_canonical_markdown", "missing_revision_markdown"},
    )


def _invalid_current_revision(root: Path) -> ScenarioResult:
    vault, data = _vault_with_doc(root)
    _raw_exec(
        vault,
        "UPDATE documents SET current_revision_id = 'rev_missing_0001' WHERE doc_id = ?",
        (data["doc_id"],),
    )
    return _assert_doctor_finds(
        "invalid_current_revision",
        vault,
        {"missing_current_revision", "missing_current_chunks", "fts_stale_row"},
    )


def _missing_chunks(root: Path) -> ScenarioResult:
    vault, data = _vault_with_doc(root)
    _raw_exec(vault, "DELETE FROM chunks WHERE revision_id = ?", (data["current_revision_id"],))
    return _assert_doctor_finds("missing_chunks", vault, {"missing_current_chunks", "fts_stale_row"})


def _cleared_fts(root: Path) -> ScenarioResult:
    vault, _data = _vault_with_doc(root)
    _raw_exec(vault, "DELETE FROM chunks_fts")
    return _assert_doctor_finds("cleared_fts", vault, {"fts_missing_chunk"})


def _missing_vector_index_record(root: Path) -> ScenarioResult:
    vault, data = _vault_with_doc(root)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        rebuild_vector_index(connection)
        chunk_id = connection.execute(
            """
            SELECT chunk_id
            FROM chunks
            WHERE doc_id = ?
              AND revision_id = ?
              AND is_current = 1
            ORDER BY sequence, chunk_id
            LIMIT 1
            """,
            (data["doc_id"], data["current_revision_id"]),
        ).fetchone()["chunk_id"]
        connection.execute("DELETE FROM embeddings WHERE chunk_id = ?", (chunk_id,))
        connection.commit()
    return _assert_doctor_finds("missing_vector_index_record", vault, {"missing_vector_index_record"})


def _orphan_embedding(root: Path) -> ScenarioResult:
    vault, _data = _vault_with_doc(root)
    now = utc_now_iso()
    _raw_exec(
        vault,
        """
        INSERT INTO embeddings(
          embedding_id, chunk_id, doc_id, revision_id, provider, model,
          dimension, vector_ref, content_hash, status, created_at, updated_at
        )
        VALUES ('embedding_orphan_full_negative', 'chunk_missing', 'doc_missing', 'rev_missing',
                'local', 'hash-v1', 8, '{"vector":[0]}', 'sha256:missing',
                'indexed', ?, ?)
        """,
        (now, now),
    )
    return _assert_doctor_finds("orphan_embedding", vault, {"orphan_embedding"})


def _source_shell_marked_searchable(root: Path) -> ScenarioResult:
    vault, data = _vault_with_pdf_shell(root)
    _raw_exec(
        vault,
        "UPDATE documents SET fts_status = 'indexed' WHERE doc_id = ?",
        (data["doc_id"],),
    )
    _raw_exec(
        vault,
        """
        INSERT INTO chunks_fts(chunk_id, doc_id, revision_id, title, heading_path, text, tags, category)
        VALUES ('chunk_shell_fake', ?, 'rev_shell_fake', 'shell', '', 'shell fake searchable', '', '')
        """,
        (data["doc_id"],),
    )
    return _assert_doctor_finds(
        "source_shell_marked_searchable",
        vault,
        {"source_shell_indexed", "source_shell_has_fts", "fts_stale_row"},
    )


def _missing_translation_output(root: Path) -> ScenarioResult:
    vault, data = _vault_with_doc(root)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        result = translate_full_document(
            connection,
            vault,
            doc_id=data["doc_id"],
            revision_id=data["current_revision_id"],
            target_language="zh-CN",
        )
    (vault / result.output_path).unlink()
    return _assert_doctor_finds(
        "missing_translation_output",
        vault,
        {"missing_translation_output"},
        warning_codes={"missing_translation_output"},
    )


def _orphan_translation_record(root: Path) -> ScenarioResult:
    vault, data = _vault_with_doc(root)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        result = translate_full_document(
            connection,
            vault,
            doc_id=data["doc_id"],
            revision_id=data["current_revision_id"],
            target_language="zh-CN",
        )
        execution_id = connection.execute(
            "SELECT execution_id FROM translations WHERE translation_id = ?",
            (result.translation_id,),
        ).fetchone()["execution_id"]
    _raw_exec(
        vault,
        """
        UPDATE translations
        SET source_doc_id = 'doc_missing',
            source_revision_id = 'rev_missing',
            source_chunk_ids_json = '["chunk_missing"]'
        WHERE translation_id = ?
        """,
        (result.translation_id,),
    )
    _raw_exec(
        vault,
        "UPDATE executions SET output_path = 'outputs/translations/mismatch.md' WHERE execution_id = ?",
        (execution_id,),
    )
    return _assert_doctor_finds(
        "orphan_translation_record",
        vault,
        {
            "orphan_translation_document",
            "orphan_translation_revision",
            "translation_source_chunk_missing",
            "translation_execution_output_mismatch",
        },
    )


def _missing_accepted_atomic_note(root: Path) -> ScenarioResult:
    vault, card_id = _vault_with_accepted_card(root)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        note_path = connection.execute(
            "SELECT accepted_note_path FROM candidate_cards WHERE candidate_card_id = ?",
            (card_id,),
        ).fetchone()["accepted_note_path"]
    (vault / note_path).unlink()
    return _assert_doctor_finds("missing_accepted_atomic_note", vault, {"missing_accepted_note"})


def _orphan_atomic_note(root: Path) -> ScenarioResult:
    vault = root / "vault"
    init_vault(vault)
    orphan = vault / "notes" / "atomic" / "2099" / "01" / "candidate_card_missing.md"
    orphan.parent.mkdir(parents=True)
    orphan.write_text(
        "---\n"
        "schema_version: \"indbase.atomic_note.v1\"\n"
        "type: \"candidate_card\"\n"
        "candidate_card_id: \"candidate_card_missing\"\n"
        "---\n\n"
        "# Orphan\n",
        encoding="utf-8",
    )
    return _assert_doctor_finds("orphan_atomic_note", vault, {"orphan_atomic_note"})


def _candidate_card_source_chunk_missing(root: Path) -> ScenarioResult:
    vault, data = _vault_with_doc(root)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        generated = generate_candidate_card(connection, doc_id=data["doc_id"])
    _raw_exec(
        vault,
        """
        UPDATE candidate_card_sources
        SET source_chunk_id = 'chunk_missing'
        WHERE candidate_card_id = ?
        """,
        (generated.candidate_card_id,),
    )
    return _assert_doctor_finds(
        "candidate_card_source_chunk_missing",
        vault,
        {"candidate_card_claim_source_binding_missing", "orphan_candidate_card_source_chunk"},
    )


def _accepted_card_without_sources(root: Path) -> ScenarioResult:
    vault, card_id = _vault_with_accepted_card(root)
    _raw_exec(vault, "DELETE FROM candidate_card_sources WHERE candidate_card_id = ?", (card_id,))
    return _assert_doctor_finds(
        "accepted_card_without_sources",
        vault,
        {"accepted_card_without_sources", "candidate_card_claim_source_binding_missing"},
    )


def _uncited_accepted_claim(root: Path) -> ScenarioResult:
    vault, card_id = _vault_with_accepted_card(root)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        accepted_note_path = connection.execute(
            "SELECT accepted_note_path FROM candidate_cards WHERE candidate_card_id = ?",
            (card_id,),
        ).fetchone()["accepted_note_path"]
    note = vault / accepted_note_path
    note.write_text(note.read_text(encoding="utf-8").replace("Citations:", "References:"), encoding="utf-8")
    return _assert_doctor_finds("uncited_accepted_claim", vault, {"accepted_note_uncited_claim"})


def _schema_version_mismatch(root: Path) -> ScenarioResult:
    vault = root / "vault"
    init_vault(vault)
    _raw_exec(
        vault,
        "INSERT INTO schema_migrations(version, applied_at) VALUES ('9999_future', ?)",
        (utc_now_iso(),),
    )
    return _assert_doctor_finds("schema_version_mismatch", vault, {"unknown_migrations"})


def _config_missing(root: Path) -> ScenarioResult:
    vault = root / "vault"
    init_vault(vault)
    (vault / ".indbase" / "config" / "config.toml").unlink()
    return _assert_doctor_finds("config_missing", vault, {"missing_config"})


def _category_tag_fts_stale(root: Path) -> ScenarioResult:
    vault, data = _vault_with_doc(root)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        category_id = add_category(connection, "Fresh Gate Category")
        set_document_category(connection, data["doc_id"], category_id)
        add_document_tag(connection, data["doc_id"], "Fresh Gate Tag")
        connection.execute(
            """
            UPDATE chunks_fts
            SET category = 'stale category', tags = 'stale tag'
            WHERE doc_id = ?
            """,
            (data["doc_id"],),
        )
        connection.commit()
    return _assert_doctor_finds("category_tag_fts_stale", vault, {"fts_metadata_stale"})


def _vault_with_doc(root: Path) -> tuple[Path, dict[str, str]]:
    vault = root / "vault"
    source = root / "source.md"
    root.mkdir(parents=True)
    source.write_text(
        "# Doctor Full Negative\n"
        "This document gives the full negative gate stable chunks for source binding.\n\n"
        "## Evidence\n"
        "Generated artifacts must keep source chunk identifiers and citations.\n",
        encoding="utf-8",
    )
    init_vault(vault)
    run_m3_ingest_pipeline(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        row = connection.execute(
            """
            SELECT d.doc_id, d.current_revision_id, d.canonical_path, d.original_path
            FROM documents d
            WHERE d.current_revision_id IS NOT NULL
            """
        ).fetchone()
        return vault, {key: str(row[key]) for key in row.keys()}


def _vault_with_pdf_shell(root: Path) -> tuple[Path, dict[str, str]]:
    vault = root / "vault"
    source = root / "scan.pdf"
    root.mkdir(parents=True)
    source.write_bytes(b"%PDF image only")
    init_vault(vault)
    previous_markitdown = normalizers._run_markitdown_file
    normalizers._run_markitdown_file = lambda _path: " "
    try:
        run_m3_ingest_pipeline(vault, source)
    finally:
        normalizers._run_markitdown_file = previous_markitdown
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        row = connection.execute("SELECT doc_id, original_path FROM documents").fetchone()
        return vault, {key: str(row[key]) for key in row.keys()}


def _vault_with_accepted_card(root: Path) -> tuple[Path, str]:
    vault, data = _vault_with_doc(root)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        generated = generate_candidate_card(connection, doc_id=data["doc_id"])
        accept_candidate_card(connection, vault, generated.candidate_card_id)
        return vault, generated.candidate_card_id


def _assert_doctor_finds(
    name: str,
    vault: Path,
    expected_codes: set[str],
    *,
    warning_codes: set[str] | None = None,
) -> ScenarioResult:
    warning_codes = warning_codes or set()
    report = run_doctor(vault)
    findings = tuple(
        {"severity": finding.severity, "code": finding.code, "message": finding.message}
        for finding in report.findings
    )
    detected_codes = frozenset(finding["code"] for finding in findings)
    missing = expected_codes - set(detected_codes)
    if missing:
        raise RuntimeError(f"{name} missing doctor finding(s): {sorted(missing)}; findings={findings}")

    weak = [
        finding
        for finding in findings
        if finding["code"] in expected_codes
        and finding["code"] not in warning_codes
        and finding["severity"] not in {"error", "critical"}
    ]
    if weak:
        raise RuntimeError(f"{name} doctor severity too weak: {weak}")

    for code in warning_codes:
        matching = [finding for finding in findings if finding["code"] == code]
        if not matching or any(finding["severity"] != "warning" for finding in matching):
            raise RuntimeError(f"{name} expected warning severity for {code}: {matching}")
    return ScenarioResult(
        name=name,
        expected_codes=frozenset(expected_codes),
        detected_codes=detected_codes,
        findings=findings,
    )


def _raw_exec(vault: Path, sql: str, params: tuple[object, ...] = ()) -> None:
    connection = sqlite3.connect(vault / ".indbase" / "db.sqlite")
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(sql, params)
        connection.commit()
    finally:
        connection.close()


def _summarize(scenarios: list[ScenarioResult]) -> dict[str, object]:
    expected_codes = {code for scenario in scenarios for code in scenario.expected_codes}
    detected_codes = {code for scenario in scenarios for code in scenario.detected_codes}
    missing_expected = sum(len(scenario.expected_codes - scenario.detected_codes) for scenario in scenarios)
    weak_severity = 0
    for scenario in scenarios:
        for finding in scenario.findings:
            if finding["code"] in scenario.expected_codes and finding["code"] != "missing_translation_output":
                weak_severity += int(finding["severity"] not in {"error", "critical"})

    generated_codes = {
        "missing_translation_output",
        "orphan_translation_document",
        "orphan_translation_revision",
        "translation_source_chunk_missing",
        "translation_execution_output_mismatch",
        "missing_accepted_note",
        "orphan_atomic_note",
        "candidate_card_claim_source_binding_missing",
        "orphan_candidate_card_source_chunk",
        "accepted_card_without_sources",
        "accepted_note_uncited_claim",
    }
    generated_missing = sum(1 for code in generated_codes if code not in detected_codes)

    return {
        "scenarios": len(scenarios),
        "passed": len(scenarios),
        "expected_codes": sorted(expected_codes),
        "detected_codes": sorted(detected_codes),
        "missing_expected_findings": missing_expected,
        "weak_error_severity_findings": weak_severity,
        "source_corruption_detected": sum(
            1
            for code in detected_codes
            if code
            in {
                "missing_original_file",
                "missing_canonical_markdown",
                "missing_revision_markdown",
                "missing_current_revision",
                "missing_current_chunks",
            }
        ),
        "index_corruption_detected": sum(
            1
            for code in detected_codes
            if code
            in {
                "fts_missing_chunk",
                "fts_stale_row",
                "fts_metadata_stale",
                "missing_vector_index_record",
                "orphan_embedding",
                "source_shell_indexed",
                "source_shell_has_fts",
            }
        ),
        "generated_artifact_corruption_detected": sum(1 for code in detected_codes if code in generated_codes),
        "config_schema_corruption_detected": sum(
            1 for code in detected_codes if code in {"missing_config", "unknown_migrations"}
        ),
        "missing_vector_index_record_undetected": int("missing_vector_index_record" not in detected_codes),
        "category_tag_fts_stale_undetected": int("fts_metadata_stale" not in detected_codes),
        "missing_generated_outputs_undetected": generated_missing,
    }


if __name__ == "__main__":
    main()
