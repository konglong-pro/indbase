"""Vault health checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.util import find_spec
import json
from pathlib import Path
import shutil
import sqlite3

from indbase_core.artifact_policy import (
    LOCATOR_ARTIFACT_KEYS,
    is_relative_vault_path,
    is_vault_artifact_path,
    is_vault_original_path,
)
from indbase_core.config import ConfigError, load_config
from indbase_core.conversion import hash_markdown
from indbase_core.db import connect, load_migrations
from indbase_core.paths import vault_paths
from indbase_core.search_text import build_fts_text
from indbase_core.taxonomy import TAG_TYPES


OCR_SIDECAR_SUFFIXES = (".ocr.txt", ".ocr.json")
LOW_CONFIDENCE_OCR_THRESHOLD = 0.60


@dataclass(frozen=True)
class DoctorFinding:
    severity: str
    code: str
    message: str


@dataclass(frozen=True)
class DoctorReport:
    vault_path: Path
    findings: tuple[DoctorFinding, ...]
    providers: dict[str, object] = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        severities = {finding.severity for finding in self.findings}
        if "error" in severities or "critical" in severities:
            return 2
        if "warning" in severities:
            return 1
        return 0

    def to_dict(self) -> dict[str, object]:
        return {
            "vault_path": self.vault_path.as_posix(),
            "exit_code": self.exit_code,
            "findings": [
                {
                    "severity": finding.severity,
                    "code": finding.code,
                    "message": finding.message,
                }
                for finding in self.findings
            ],
            "providers": self.providers,
        }


def run_doctor(vault_path: Path | str) -> DoctorReport:
    paths = vault_paths(vault_path)
    findings: list[DoctorFinding] = []
    config = None

    missing_dirs = [path for path in paths.required_directories() if not path.is_dir()]
    for directory in missing_dirs:
        findings.append(
            DoctorFinding(
                "error",
                "missing_directory",
                f"Required directory is missing: {paths.relative_to_vault(directory)}",
            )
        )

    if not paths.config_path.is_file():
        findings.append(DoctorFinding("error", "missing_config", "Vault config is missing."))
    else:
        try:
            config = load_config(paths.config_path)
            if config.vault_path != paths.root:
                findings.append(
                    DoctorFinding(
                        "warning",
                        "config_vault_path_mismatch",
                        "Config vault_path does not match the checked vault path.",
                    )
                )
        except ConfigError as exc:
            findings.append(DoctorFinding("error", "invalid_config", str(exc)))

    if not paths.db_path.is_file():
        findings.append(DoctorFinding("error", "missing_database", "SQLite database is missing."))
    else:
        findings.extend(_check_database(paths.root, paths.db_path))

    findings.append(_ocr_availability_finding())
    if config is not None and config.features.swallow_ingest:
        findings.append(_swallow_availability_finding())
    if config is not None:
        findings.extend(_check_transition_output_runtime(paths, config))
    providers = _provider_doctor_summary(config)

    if not any(finding.severity in {"warning", "error", "critical"} for finding in findings):
        findings.append(DoctorFinding("info", "ok", "Vault health checks passed."))

    return DoctorReport(vault_path=paths.root, findings=tuple(findings), providers=providers)


def _ocr_availability_finding() -> DoctorFinding:
    if shutil.which("tesseract") is None:
        return DoctorFinding(
            "info",
            "ocr_tesseract_unavailable",
            "Tesseract OCR is not installed; M6.2 sidecar OCR remains available for explicit local OCR text import.",
        )
    return DoctorFinding(
        "info",
        "ocr_tesseract_available",
        "Tesseract OCR is installed; future OCR adapters can use it when page rendering is enabled.",
    )


def _swallow_availability_finding() -> DoctorFinding:
    if find_spec("swallow") is None:
        return DoctorFinding(
            "error",
            "swallow_unavailable",
            "swallow ingest is enabled but the swallow package is not installed.",
        )
    return DoctorFinding(
        "info",
        "swallow_available",
        "swallow ingest is enabled and the swallow package is importable.",
    )


def _provider_doctor_summary(config) -> dict[str, object]:
    if config is None:
        return {
            "swallow": {
                "configured": False,
                "binding_profile": "local_core",
                "capabilities_ok": False,
                "warnings": ["config_missing"],
            },
            "transition": {
                "configured": False,
                "binding_profile": "node_bridge",
                "capabilities_ok": False,
                "warnings": ["config_missing"],
            },
        }
    from indbase_integrations.swallow.doctor import check_swallow_provider
    from indbase_integrations.transition.doctor import check_transition_provider

    swallow = check_swallow_provider(
        configured=bool(config.features.swallow_ingest),
        binding_profile="local_core",
    ).to_dict()
    transition = check_transition_provider(
        configured=bool(config.features.transition_output),
        binding_profile="node_bridge",
    ).to_dict()
    transition["capabilities_ok"] = bool(transition.get("configured")) and bool(transition.get("node_ok"))
    return {
        "swallow": swallow,
        "transition": transition,
    }


def _check_database(vault_root: Path, db_path: Path) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    latest_versions = {migration.version for migration in load_migrations()}
    try:
        connection = connect(db_path)
        try:
            applied_versions = {
                str(row["version"])
                for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
            }
            missing = sorted(latest_versions - applied_versions)
            if missing:
                findings.append(
                    DoctorFinding(
                        "error",
                        "missing_migrations",
                        f"Schema migrations not applied: {', '.join(missing)}",
                    )
                )
            unknown = sorted(applied_versions - latest_versions)
            if unknown:
                findings.append(
                    DoctorFinding(
                        "error",
                        "unknown_migrations",
                        f"Schema migrations are newer or unknown: {', '.join(unknown)}",
                    )
                )
            category_count = connection.execute("SELECT COUNT(*) AS count FROM categories").fetchone()["count"]
            if int(category_count) == 0:
                findings.append(
                    DoctorFinding(
                        "warning",
                        "no_categories",
                        "No categories are present. Run init with a category template or add categories.",
                    )
                )
            findings.extend(_check_document_files(connection, vault_root))
            findings.extend(_check_source_file_integrity(connection, vault_root))
            findings.extend(_check_revision_integrity(connection, vault_root))
            findings.extend(_check_source_markdown_files(connection, vault_root))
            findings.extend(_check_source_shell_integrity(connection))
            findings.extend(_check_chunk_integrity(connection))
            findings.extend(_check_fts_integrity(connection))
            findings.extend(_check_source_snapshot_integrity(connection, vault_root))
            findings.extend(_check_chunk_source_locator_integrity(connection, vault_root))
            findings.extend(_check_ocr_integrity(connection))
            findings.extend(_check_embedding_integrity(connection))
            findings.extend(_check_translation_integrity(connection, vault_root))
            findings.extend(_check_candidate_card_integrity(connection, vault_root))
            findings.extend(_check_swallow_conversion_integrity(connection, vault_root))
            findings.extend(_check_archive_ingest_integrity(connection))
            findings.extend(_check_swallow_artifact_integrity(connection, vault_root))
            findings.extend(_check_provider_run_integrity(connection, vault_root))
            findings.extend(_check_output_run_integrity(connection, vault_root))
            findings.extend(_check_review_queue(connection))
            findings.extend(_check_taxonomy_integrity(connection))
            findings.extend(_check_tag_governance_integrity(connection))
            findings.extend(_check_retrieval_integrity(connection))
            findings.extend(_check_retrieval_evaluation_integrity(connection))
        finally:
            connection.close()
    except sqlite3.Error as exc:
        findings.append(DoctorFinding("error", "database_error", str(exc)))
    return findings


def _check_provider_run_integrity(connection: sqlite3.Connection, vault_root: Path) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    try:
        provider_rows = connection.execute(
            """
            SELECT provider_run_id, provider_id, provider_status, evidence_status,
                   evidence_root, manifest_artifact_ref_json, trace_artifact_ref_json
            FROM provider_runs
            ORDER BY created_at, provider_run_id
            """
        ).fetchall()
    except sqlite3.Error:
        return findings
    for row in provider_rows:
        provider_run_id = str(row["provider_run_id"])
        evidence_root = str(row["evidence_root"] or "")
        if row["evidence_status"] == "copied":
            if not evidence_root or not (vault_root / evidence_root).is_dir():
                findings.append(
                    DoctorFinding(
                        "error",
                        "provider_evidence_root_missing",
                        f"Provider run {provider_run_id} has copied evidence but missing evidence_root.",
                    )
                )
        for column, code in (
            ("manifest_artifact_ref_json", "provider_manifest_missing"),
            ("trace_artifact_ref_json", "provider_trace_missing"),
        ):
            ref_path = _artifact_ref_vault_path(row[column])
            if ref_path and not (vault_root / ref_path).is_file():
                findings.append(
                    DoctorFinding(
                        "error",
                        code,
                        f"Provider run {provider_run_id} references missing evidence file: {ref_path}",
                    )
                )
    findings.extend(
        _check_adopted_provider_refs(
            connection,
            table="ingest_runs",
            id_column="ingest_id",
            code="ingest_provider_run_missing",
        )
    )
    findings.extend(
        _check_adopted_provider_refs(
            connection,
            table="converter_runs",
            id_column="converter_run_id",
            code="converter_provider_run_missing",
        )
    )
    findings.extend(
        _check_adopted_provider_refs(
            connection,
            table="output_runs",
            id_column="output_run_id",
            code="output_provider_run_missing",
        )
    )
    return findings


def _check_adopted_provider_refs(
    connection: sqlite3.Connection,
    *,
    table: str,
    id_column: str,
    code: str,
) -> list[DoctorFinding]:
    rows = connection.execute(
        f"""
        SELECT owner.{id_column} AS owner_id, owner.adopted_provider_run_id
        FROM {table} owner
        LEFT JOIN provider_runs pr ON pr.provider_run_id = owner.adopted_provider_run_id
        WHERE owner.adopted_provider_run_id IS NOT NULL
          AND pr.provider_run_id IS NULL
        ORDER BY owner.{id_column}
        """
    ).fetchall()
    return [
        DoctorFinding(
            "error",
            code,
            f"{table}.{id_column} {row['owner_id']} references missing provider_run_id {row['adopted_provider_run_id']}.",
        )
        for row in rows
    ]


def _artifact_ref_vault_path(value: object) -> str | None:
    if not value:
        return None
    try:
        payload = json.loads(str(value))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    vault_path = payload.get("vault_path")
    return str(vault_path) if vault_path else None


def _check_document_files(connection: sqlite3.Connection, vault_root: Path) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    rows = connection.execute(
        """
        SELECT doc_id, current_revision_id, canonical_path, original_path, status
        FROM documents
        WHERE deleted_at IS NULL
        ORDER BY created_at, doc_id
        """
    ).fetchall()
    for row in rows:
        doc_id = str(row["doc_id"])
        original_path = row["original_path"]
        canonical_path = row["canonical_path"]
        current_revision_id = row["current_revision_id"]
        if original_path and not (vault_root / original_path).is_file():
            findings.append(
                DoctorFinding(
                    "error",
                    "missing_original_file",
                    f"Document {doc_id} original_path is missing: {original_path}",
                )
            )
        if current_revision_id and canonical_path and not (vault_root / canonical_path).is_file():
            findings.append(
                DoctorFinding(
                    "error",
                    "missing_canonical_markdown",
                    f"Document {doc_id} canonical_path is missing: {canonical_path}",
                )
            )
    return findings


def _check_source_file_integrity(connection: sqlite3.Connection, vault_root: Path) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    source_rows = connection.execute(
        """
        SELECT sf.source_file_id, sf.doc_id, sf.original_path
        FROM source_files sf
        LEFT JOIN documents d ON d.doc_id = sf.doc_id
        WHERE sf.deleted_at IS NULL
          AND d.doc_id IS NULL
        ORDER BY sf.created_at, sf.source_file_id
        """
    ).fetchall()
    for row in source_rows:
        findings.append(
            DoctorFinding(
                "error",
                "orphan_source_file",
                f"Source file {row['source_file_id']} references missing document {row['doc_id']}.",
            )
        )

    referenced_originals = {
        str(row["original_path"])
        for row in connection.execute(
            """
            SELECT original_path
            FROM source_files
            WHERE original_path IS NOT NULL
              AND deleted_at IS NULL
            """
        ).fetchall()
        if row["original_path"]
    }
    originals_root = vault_root / ".indbase" / "originals"
    if originals_root.is_dir():
        for original in sorted(path for path in originals_root.rglob("*") if path.is_file()):
            relative = original.relative_to(vault_root).as_posix()
            if relative in referenced_originals or _is_referenced_ocr_sidecar(relative, referenced_originals):
                continue
            findings.append(
                DoctorFinding(
                    "error",
                    "orphan_original_file",
                    f"Original archive file is not referenced by source_files: {relative}",
                )
            )
    return findings


def _is_referenced_ocr_sidecar(relative_path: str, referenced_originals: set[str]) -> bool:
    for suffix in OCR_SIDECAR_SUFFIXES:
        if relative_path.endswith(suffix):
            return relative_path[: -len(suffix)] in referenced_originals
    return False


def _check_revision_integrity(connection: sqlite3.Connection, vault_root: Path) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    findings.extend(_check_current_revision_links(connection))
    findings.extend(_check_orphan_revisions(connection))
    rows = connection.execute(
        """
        SELECT dr.revision_id, dr.doc_id, dr.markdown_path, dr.content_hash
        FROM document_revisions dr
        ORDER BY dr.created_at, dr.revision_id
        """
    ).fetchall()
    for row in rows:
        markdown_path = row["markdown_path"]
        absolute_path = vault_root / markdown_path
        if not absolute_path.is_file():
            findings.append(
                DoctorFinding(
                    "error",
                    "missing_revision_markdown",
                    f"Revision {row['revision_id']} markdown_path is missing: {markdown_path}",
                )
            )
            continue
        try:
            markdown = absolute_path.read_text(encoding="utf-8")
        except OSError as exc:
            findings.append(
                DoctorFinding(
                    "error",
                    "revision_markdown_unreadable",
                    f"Revision {row['revision_id']} markdown_path cannot be read: {exc}",
                )
            )
            continue
        frontmatter, body = _split_frontmatter(markdown)
        if frontmatter is None:
            findings.append(
                DoctorFinding(
                    "error",
                    "missing_frontmatter",
                    f"Revision {row['revision_id']} markdown has no source frontmatter.",
                )
            )
        else:
            expected = {
                "doc_id": row["doc_id"],
                "revision_id": row["revision_id"],
                "content_hash": row["content_hash"],
            }
            for key, expected_value in expected.items():
                if frontmatter.get(key) != expected_value:
                    findings.append(
                        DoctorFinding(
                            "error",
                            "frontmatter_mismatch",
                            f"Revision {row['revision_id']} frontmatter {key} does not match DB.",
                        )
                    )
        actual_hash = hash_markdown(body)
        if actual_hash != row["content_hash"]:
            findings.append(
                DoctorFinding(
                    "error",
                    "revision_content_hash_mismatch",
                    f"Revision {row['revision_id']} content hash does not match DB.",
                )
            )
    return findings


def _check_source_markdown_files(connection: sqlite3.Connection, vault_root: Path) -> list[DoctorFinding]:
    referenced_markdown = {
        str(row["markdown_path"])
        for row in connection.execute(
            """
            SELECT markdown_path
            FROM document_revisions
            WHERE deleted_at IS NULL
            """
        ).fetchall()
        if row["markdown_path"]
    }
    sources_root = vault_root / "sources"
    if not sources_root.is_dir():
        return []
    findings: list[DoctorFinding] = []
    for markdown_file in sorted(sources_root.rglob("*.md")):
        relative = markdown_file.relative_to(vault_root).as_posix()
        if relative in referenced_markdown:
            continue
        findings.append(
            DoctorFinding(
                "error",
                "orphan_markdown_file",
                f"Source Markdown file is not referenced by document_revisions: {relative}",
            )
        )
    return findings


def _check_source_shell_integrity(connection: sqlite3.Connection) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    indexed_shells = connection.execute(
        """
        SELECT doc_id, fts_status
        FROM documents
        WHERE current_revision_id IS NULL
          AND deleted_at IS NULL
          AND fts_status = 'indexed'
        ORDER BY created_at, doc_id
        """
    ).fetchall()
    for row in indexed_shells:
        findings.append(
            DoctorFinding(
                "error",
                "source_shell_indexed",
                f"Document {row['doc_id']} has no current revision but is marked indexed.",
            )
        )

    canonical_shells = connection.execute(
        """
        SELECT doc_id, canonical_path
        FROM documents
        WHERE current_revision_id IS NULL
          AND canonical_path IS NOT NULL
          AND deleted_at IS NULL
        ORDER BY created_at, doc_id
        """
    ).fetchall()
    for row in canonical_shells:
        findings.append(
            DoctorFinding(
                "error",
                "source_shell_has_canonical_markdown",
                f"Document {row['doc_id']} has no current revision but has canonical_path: {row['canonical_path']}.",
            )
        )

    chunk_shells = connection.execute(
        """
        SELECT d.doc_id, COUNT(c.chunk_id) AS count
        FROM documents d
        JOIN chunks c ON c.doc_id = d.doc_id
        WHERE d.current_revision_id IS NULL
          AND d.deleted_at IS NULL
          AND c.deleted_at IS NULL
        GROUP BY d.doc_id
        ORDER BY d.created_at, d.doc_id
        """
    ).fetchall()
    for row in chunk_shells:
        findings.append(
            DoctorFinding(
                "error",
                "source_shell_has_chunks",
                f"Document {row['doc_id']} has no current revision but has {row['count']} chunk(s).",
            )
        )

    fts_shells = connection.execute(
        """
        SELECT d.doc_id, COUNT(f.chunk_id) AS count
        FROM documents d
        JOIN chunks_fts f ON f.doc_id = d.doc_id
        WHERE d.current_revision_id IS NULL
          AND d.deleted_at IS NULL
        GROUP BY d.doc_id
        ORDER BY d.created_at, d.doc_id
        """
    ).fetchall()
    for row in fts_shells:
        findings.append(
            DoctorFinding(
                "error",
                "source_shell_has_fts",
                f"Document {row['doc_id']} has no current revision but has {row['count']} FTS row(s).",
            )
        )

    missing_review = connection.execute(
        """
        SELECT d.doc_id
        FROM documents d
        WHERE d.current_revision_id IS NULL
          AND d.ingest_status = 'failed'
          AND d.deleted_at IS NULL
          AND NOT EXISTS (
            SELECT 1
            FROM review_items ri
            LEFT JOIN converter_runs cr ON cr.converter_run_id = ri.target_id
            WHERE (ri.target_type = 'document' AND ri.target_id = d.doc_id)
               OR (ri.target_type = 'converter_run' AND cr.doc_id = d.doc_id)
          )
        ORDER BY d.created_at, d.doc_id
        """
    ).fetchall()
    for row in missing_review:
        findings.append(
            DoctorFinding(
                "error",
                "source_shell_missing_review",
                f"Failed source shell {row['doc_id']} has no review item explaining the next action.",
            )
        )

    missing_error = connection.execute(
        """
        SELECT d.doc_id
        FROM documents d
        WHERE d.current_revision_id IS NULL
          AND d.ingest_status = 'failed'
          AND d.deleted_at IS NULL
          AND NOT EXISTS (
            SELECT 1
            FROM errors e
            WHERE e.payload_json LIKE '%' || d.doc_id || '%'
          )
        ORDER BY d.created_at, d.doc_id
        """
    ).fetchall()
    for row in missing_error:
        findings.append(
            DoctorFinding(
                "error",
                "source_shell_missing_error",
                f"Failed source shell {row['doc_id']} has no error record explaining the failure.",
            )
        )
    return findings


def _check_current_revision_links(connection: sqlite3.Connection) -> list[DoctorFinding]:
    rows = connection.execute(
        """
        SELECT d.doc_id, d.current_revision_id
        FROM documents d
        LEFT JOIN document_revisions dr ON dr.revision_id = d.current_revision_id
        WHERE d.current_revision_id IS NOT NULL
          AND dr.revision_id IS NULL
        ORDER BY d.created_at, d.doc_id
        """
    ).fetchall()
    return [
        DoctorFinding(
            "error",
            "missing_current_revision",
            f"Document {row['doc_id']} current_revision_id is missing: {row['current_revision_id']}",
        )
        for row in rows
    ]


def _check_orphan_revisions(connection: sqlite3.Connection) -> list[DoctorFinding]:
    rows = connection.execute(
        """
        SELECT dr.revision_id, dr.doc_id
        FROM document_revisions dr
        LEFT JOIN documents d ON d.doc_id = dr.doc_id
        WHERE d.doc_id IS NULL
        ORDER BY dr.created_at, dr.revision_id
        """
    ).fetchall()
    return [
        DoctorFinding(
            "error",
            "orphan_revision",
            f"Revision {row['revision_id']} references missing document {row['doc_id']}.",
        )
        for row in rows
    ]


def _check_chunk_integrity(connection: sqlite3.Connection) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    missing_current_chunks = connection.execute(
        """
        SELECT d.doc_id, d.current_revision_id
        FROM documents d
        WHERE d.status = 'active'
          AND d.current_revision_id IS NOT NULL
          AND d.deleted_at IS NULL
          AND NOT EXISTS (
            SELECT 1
            FROM chunks c
            WHERE c.doc_id = d.doc_id
              AND c.revision_id = d.current_revision_id
              AND c.is_current = 1
              AND c.deleted_at IS NULL
          )
        ORDER BY d.created_at, d.doc_id
        """
    ).fetchall()
    for row in missing_current_chunks:
        findings.append(
            DoctorFinding(
                "error",
                "missing_current_chunks",
                f"Document {row['doc_id']} current revision has no current chunks.",
            )
        )

    orphan_chunks = connection.execute(
        """
        SELECT c.chunk_id, c.doc_id, c.revision_id
        FROM chunks c
        LEFT JOIN documents d ON d.doc_id = c.doc_id
        LEFT JOIN document_revisions dr ON dr.revision_id = c.revision_id
        WHERE d.doc_id IS NULL
           OR dr.revision_id IS NULL
        ORDER BY c.created_at, c.chunk_id
        """
    ).fetchall()
    for row in orphan_chunks:
        findings.append(
            DoctorFinding(
                "error",
                "orphan_chunk",
                f"Chunk {row['chunk_id']} references missing document or revision.",
            )
        )
    return findings


def _check_fts_integrity(connection: sqlite3.Connection) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    missing_fts_rows = connection.execute(
        """
        SELECT c.chunk_id, c.doc_id
        FROM chunks c
        JOIN documents d ON d.doc_id = c.doc_id
        WHERE d.status = 'active'
          AND d.deleted_at IS NULL
          AND d.current_revision_id = c.revision_id
          AND c.is_current = 1
          AND c.deleted_at IS NULL
          AND NOT EXISTS (
            SELECT 1
            FROM chunks_fts f
            WHERE f.chunk_id = c.chunk_id
          )
        ORDER BY c.created_at, c.chunk_id
        """
    ).fetchall()
    for row in missing_fts_rows:
        findings.append(
            DoctorFinding(
                "error",
                "fts_missing_chunk",
                f"Current chunk {row['chunk_id']} for document {row['doc_id']} is missing from FTS.",
            )
        )

    stale_fts_rows = connection.execute(
        """
        SELECT f.chunk_id, f.doc_id, f.revision_id
        FROM chunks_fts f
        LEFT JOIN chunks c ON c.chunk_id = f.chunk_id
        LEFT JOIN documents d ON d.doc_id = f.doc_id
        WHERE c.chunk_id IS NULL
           OR d.doc_id IS NULL
           OR d.deleted_at IS NOT NULL
           OR d.current_revision_id != f.revision_id
           OR c.revision_id != f.revision_id
           OR c.is_current != 1
           OR c.deleted_at IS NOT NULL
        ORDER BY f.chunk_id
        """
    ).fetchall()
    for row in stale_fts_rows:
        findings.append(
            DoctorFinding(
                "error",
                "fts_stale_row",
                f"FTS row for chunk {row['chunk_id']} is not an active current chunk.",
            )
        )
    findings.extend(_check_fts_metadata_integrity(connection))
    return findings


def _check_fts_metadata_integrity(connection: sqlite3.Connection) -> list[DoctorFinding]:
    rows = connection.execute(
        """
        SELECT f.chunk_id, f.doc_id, f.title, f.tags, f.category,
               d.title AS document_title, c.name AS category_name
        FROM chunks_fts f
        JOIN chunks ch ON ch.chunk_id = f.chunk_id
        JOIN documents d ON d.doc_id = f.doc_id
        LEFT JOIN categories c ON c.category_id = d.category_id
        WHERE d.status = 'active'
          AND d.deleted_at IS NULL
          AND d.current_revision_id = f.revision_id
          AND ch.revision_id = f.revision_id
          AND ch.is_current = 1
          AND ch.deleted_at IS NULL
        ORDER BY f.doc_id, f.chunk_id
        """
    ).fetchall()
    findings: list[DoctorFinding] = []
    for row in rows:
        expected_title = build_fts_text(row["document_title"])
        expected_tags = build_fts_text(_document_tags_text(connection, str(row["doc_id"])))
        expected_category = build_fts_text(row["category_name"])
        if (
            str(row["title"] or "") != expected_title
            or str(row["tags"] or "") != expected_tags
            or str(row["category"] or "") != expected_category
        ):
            findings.append(
                DoctorFinding(
                    "error",
                    "fts_metadata_stale",
                    f"FTS metadata for chunk {row['chunk_id']} does not match current title, category, or tags.",
                )
            )
    return findings


def _check_source_snapshot_integrity(connection: sqlite3.Connection, vault_root: Path) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    document_rows = connection.execute(
        """
        SELECT doc_id, source_snapshot_path
        FROM documents
        WHERE source_snapshot_path IS NOT NULL
          AND deleted_at IS NULL
        ORDER BY created_at, doc_id
        """
    ).fetchall()
    for row in document_rows:
        snapshot_path = str(row["source_snapshot_path"] or "")
        if snapshot_path and not is_vault_artifact_path(snapshot_path):
            findings.append(
                DoctorFinding(
                    "error",
                    "source_snapshot_not_durable",
                    f"Document {row['doc_id']} source_snapshot_path is not a durable artifact path: {snapshot_path}",
                )
            )
        elif snapshot_path and not (vault_root / snapshot_path).is_file():
            findings.append(
                DoctorFinding(
                    "error",
                    "missing_source_snapshot",
                    f"Document {row['doc_id']} source_snapshot_path is missing: {snapshot_path}",
                )
            )

    source_file_rows = connection.execute(
        """
        SELECT source_file_id, doc_id, source_snapshot_path
        FROM source_files
        WHERE source_snapshot_path IS NOT NULL
          AND deleted_at IS NULL
        ORDER BY created_at, source_file_id
        """
    ).fetchall()
    for row in source_file_rows:
        snapshot_path = str(row["source_snapshot_path"] or "")
        if snapshot_path and not is_vault_artifact_path(snapshot_path):
            findings.append(
                DoctorFinding(
                    "error",
                    "source_file_snapshot_not_durable",
                    f"Source file {row['source_file_id']} snapshot is not a durable artifact path: {snapshot_path}",
                )
            )
        elif snapshot_path and not (vault_root / snapshot_path).is_file():
            findings.append(
                DoctorFinding(
                    "error",
                    "missing_source_file_snapshot",
                    f"Source file {row['source_file_id']} for document {row['doc_id']} snapshot is missing: {snapshot_path}",
                )
            )
    return findings


def _check_chunk_source_locator_integrity(connection: sqlite3.Connection, vault_root: Path) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    rows = connection.execute(
        """
        SELECT c.chunk_id, c.doc_id, c.revision_id, c.source_locator_json
        FROM chunks c
        JOIN documents d ON d.doc_id = c.doc_id
        WHERE c.source_locator_json IS NOT NULL
          AND c.deleted_at IS NULL
          AND d.deleted_at IS NULL
        ORDER BY c.created_at, c.chunk_id
        """
    ).fetchall()
    for row in rows:
        chunk_id = str(row["chunk_id"])
        locators = _parse_locator_list(row["source_locator_json"])
        if locators is None:
            findings.append(
                DoctorFinding(
                    "error",
                    "source_locator_invalid",
                    f"Chunk {chunk_id} source_locator_json is not a valid locator list.",
                )
            )
            continue
        if not locators:
            findings.append(
                DoctorFinding(
                    "error",
                    "source_locator_empty",
                    f"Chunk {chunk_id} source_locator_json is empty.",
                )
            )
            continue
        for locator in locators:
            kind = str(locator.get("kind") or "")
            if not kind:
                findings.append(
                    DoctorFinding(
                        "error",
                        "source_locator_missing_kind",
                        f"Chunk {chunk_id} has a source locator without kind.",
                    )
                )
            findings.extend(_check_locator_paths(chunk_id, locator, vault_root))
            if kind == "archive_member":
                findings.extend(_check_archive_member_locator(chunk_id, locator))
            elif kind == "web_snapshot":
                findings.extend(_check_web_snapshot_locator(chunk_id, locator))
            elif kind in {"ocr", "ocr_page", "asr_transcript", "asr_segment"}:
                findings.extend(_check_media_locator(chunk_id, locator))
    return findings


def _parse_locator_list(value: object) -> list[dict[str, object]] | None:
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, list):
        return None
    locators: list[dict[str, object]] = []
    for locator in parsed:
        if not isinstance(locator, dict):
            return None
        locators.append(locator)
    return locators


def _check_locator_paths(chunk_id: str, locator: dict[str, object], vault_root: Path) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    for key in LOCATOR_ARTIFACT_KEYS:
        value = locator.get(key)
        if not isinstance(value, str) or not value:
            continue
        if not is_vault_artifact_path(value):
            findings.append(
                DoctorFinding(
                    "error",
                    "source_locator_artifact_not_durable",
                    f"Chunk {chunk_id} locator {key} is not a durable artifact path: {value}",
                )
            )
        elif not (vault_root / value).is_file():
            findings.append(
                DoctorFinding(
                    "error",
                    "source_locator_missing_artifact",
                    f"Chunk {chunk_id} locator {key} is missing: {value}",
                )
            )
    source_path = locator.get("source_path")
    if isinstance(source_path, str) and source_path and not is_vault_original_path(source_path):
        findings.append(
            DoctorFinding(
                "error",
                "source_locator_source_not_durable",
                f"Chunk {chunk_id} locator source_path is not a durable original path: {source_path}",
            )
        )
    elif isinstance(source_path, str) and source_path and not (vault_root / source_path).is_file():
        findings.append(
            DoctorFinding(
                "error",
                "source_locator_missing_source",
                f"Chunk {chunk_id} locator source_path is missing: {source_path}",
            )
        )
    return findings


def _check_archive_member_locator(chunk_id: str, locator: dict[str, object]) -> list[DoctorFinding]:
    required = ("archive_type", "member_path", "logical_source_id", "conversation_index", "source_path", "artifact")
    missing = _missing_locator_keys(locator, required)
    if not missing:
        return []
    return [
        DoctorFinding(
            "error",
            "archive_member_locator_incomplete",
            f"Chunk {chunk_id} archive_member locator is missing: {', '.join(missing)}.",
        )
    ]


def _check_web_snapshot_locator(chunk_id: str, locator: dict[str, object]) -> list[DoctorFinding]:
    missing = _missing_locator_keys(locator, ("url", "artifact"))
    if not missing:
        return []
    return [
        DoctorFinding(
            "error",
            "web_snapshot_locator_incomplete",
            f"Chunk {chunk_id} web_snapshot locator is missing: {', '.join(missing)}.",
        )
    ]


def _check_media_locator(chunk_id: str, locator: dict[str, object]) -> list[DoctorFinding]:
    if not _missing_locator_keys(locator, ("artifact", "source_path")):
        return []
    return [
        DoctorFinding(
            "error",
            "media_locator_incomplete",
            f"Chunk {chunk_id} OCR/ASR locator must include artifact and source_path.",
        )
    ]


def _missing_locator_keys(locator: dict[str, object], keys: tuple[str, ...]) -> list[str]:
    missing: list[str] = []
    for key in keys:
        value = locator.get(key)
        if value is None:
            missing.append(key)
        elif isinstance(value, str) and not value:
            missing.append(key)
    return missing


def _document_tags_text(connection: sqlite3.Connection, doc_id: str) -> str:
    rows = connection.execute(
        """
        SELECT t.name
        FROM document_tags dt
        JOIN tags t ON t.tag_id = dt.tag_id
        WHERE dt.doc_id = ?
          AND dt.deleted_at IS NULL
          AND t.deleted_at IS NULL
        ORDER BY t.name
        """,
        (doc_id,),
    ).fetchall()
    return " ".join(str(row["name"]) for row in rows)


def _check_ocr_integrity(connection: sqlite3.Connection) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    orphan_pages = connection.execute(
        """
        SELECT op.ocr_page_id, op.doc_id, op.revision_id
        FROM ocr_pages op
        LEFT JOIN documents d ON d.doc_id = op.doc_id
        LEFT JOIN document_revisions dr ON dr.revision_id = op.revision_id
        WHERE d.doc_id IS NULL
           OR dr.revision_id IS NULL
        ORDER BY op.created_at, op.ocr_page_id
        """
    ).fetchall()
    for row in orphan_pages:
        findings.append(
            DoctorFinding(
                "error",
                "orphan_ocr_page",
                f"OCR page {row['ocr_page_id']} references missing document or revision.",
            )
        )

    mismatched_pages = connection.execute(
        """
        SELECT op.ocr_page_id, op.doc_id, op.revision_id, dr.doc_id AS revision_doc_id
        FROM ocr_pages op
        JOIN document_revisions dr ON dr.revision_id = op.revision_id
        WHERE dr.doc_id != op.doc_id
        ORDER BY op.created_at, op.ocr_page_id
        """
    ).fetchall()
    for row in mismatched_pages:
        findings.append(
            DoctorFinding(
                "error",
                "ocr_page_revision_mismatch",
                f"OCR page {row['ocr_page_id']} doc_id does not match revision {row['revision_id']}.",
            )
        )

    missing_pages = connection.execute(
        """
        SELECT d.doc_id, d.current_revision_id
        FROM documents d
        JOIN document_revisions dr ON dr.revision_id = d.current_revision_id
        WHERE dr.converter_name LIKE 'ocr_%'
          AND d.deleted_at IS NULL
          AND NOT EXISTS (
            SELECT 1
            FROM ocr_pages op
            WHERE op.doc_id = d.doc_id
              AND op.revision_id = d.current_revision_id
              AND op.deleted_at IS NULL
          )
        ORDER BY d.created_at, d.doc_id
        """
    ).fetchall()
    for row in missing_pages:
        findings.append(
            DoctorFinding(
                "error",
                "missing_ocr_pages",
                f"OCR current revision {row['current_revision_id']} for document {row['doc_id']} has no OCR page rows.",
            )
        )

    converter_missing_revision = connection.execute(
        """
        SELECT cr.converter_run_id, cr.doc_id, cr.revision_id
        FROM converter_runs cr
        LEFT JOIN document_revisions dr ON dr.revision_id = cr.revision_id
        WHERE cr.status = 'succeeded'
          AND cr.converter_name LIKE 'ocr_%'
          AND (cr.revision_id IS NULL OR dr.revision_id IS NULL)
        ORDER BY cr.created_at, cr.converter_run_id
        """
    ).fetchall()
    for row in converter_missing_revision:
        findings.append(
            DoctorFinding(
                "error",
                "ocr_converter_missing_revision",
                f"OCR converter run {row['converter_run_id']} succeeded without a valid revision.",
            )
        )

    low_confidence_without_review = connection.execute(
        """
        SELECT DISTINCT op.doc_id
        FROM ocr_pages op
        WHERE op.deleted_at IS NULL
          AND (
            op.needs_review = 1
            OR op.quality_status = 'warning'
            OR (op.confidence IS NOT NULL AND op.confidence < ?)
          )
          AND NOT EXISTS (
            SELECT 1
            FROM review_items ri
            WHERE ri.type = 'ocr_low_quality'
              AND ri.target_type = 'document'
              AND ri.target_id = op.doc_id
          )
        ORDER BY op.doc_id
        """,
        (LOW_CONFIDENCE_OCR_THRESHOLD,),
    ).fetchall()
    for row in low_confidence_without_review:
        findings.append(
            DoctorFinding(
                "error",
                "ocr_low_confidence_without_review",
                f"Document {row['doc_id']} has low-confidence OCR pages without an OCR review item.",
            )
        )

    failed_conversions_without_review = connection.execute(
        """
        SELECT cr.converter_run_id, cr.doc_id
        FROM converter_runs cr
        WHERE cr.status = 'failed'
          AND cr.converter_name IN ('swallow', 'swallow_required_gate', 'markitdown')
          AND NOT EXISTS (
            SELECT 1
            FROM review_items ri
            WHERE ri.target_type = 'converter_run'
              AND ri.target_id = cr.converter_run_id
          )
        ORDER BY cr.created_at, cr.converter_run_id
        """
    ).fetchall()
    for row in failed_conversions_without_review:
        findings.append(
            DoctorFinding(
                "error",
                "conversion_failure_without_review",
                f"Failed converter run {row['converter_run_id']} has no review item.",
            )
        )

    failed_conversions_without_error = connection.execute(
        """
        SELECT cr.converter_run_id, cr.doc_id
        FROM converter_runs cr
        WHERE cr.status = 'failed'
          AND cr.converter_name IN ('swallow', 'swallow_required_gate', 'markitdown')
          AND NOT EXISTS (
            SELECT 1
            FROM errors e
            WHERE e.component = 'conversion'
              AND e.payload_json LIKE '%' || cr.doc_id || '%'
          )
        ORDER BY cr.created_at, cr.converter_run_id
        """
    ).fetchall()
    for row in failed_conversions_without_error:
        findings.append(
            DoctorFinding(
                "error",
                "conversion_failure_without_error",
                f"Failed converter run {row['converter_run_id']} has no conversion error record.",
            )
        )
    return findings


def _check_embedding_integrity(connection: sqlite3.Connection) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    missing_indexed_embeddings = connection.execute(
        """
        SELECT c.chunk_id, c.doc_id, c.revision_id
        FROM chunks c
        JOIN documents d ON d.doc_id = c.doc_id
        WHERE d.status = 'active'
          AND d.deleted_at IS NULL
          AND d.embedding_status = 'indexed'
          AND d.current_revision_id = c.revision_id
          AND c.is_current = 1
          AND c.deleted_at IS NULL
          AND NOT EXISTS (
            SELECT 1
            FROM embeddings e
            WHERE e.chunk_id = c.chunk_id
              AND e.doc_id = c.doc_id
              AND e.revision_id = c.revision_id
              AND e.status = 'indexed'
              AND e.deleted_at IS NULL
          )
        ORDER BY c.doc_id, c.sequence, c.chunk_id
        """
    ).fetchall()
    for row in missing_indexed_embeddings:
        findings.append(
            DoctorFinding(
                "error",
                "missing_vector_index_record",
                f"Document {row['doc_id']} is marked vector-indexed but chunk {row['chunk_id']} has no indexed embedding.",
            )
        )

    orphan_embeddings = connection.execute(
        """
        SELECT e.embedding_id, e.chunk_id, e.doc_id, e.revision_id
        FROM embeddings e
        LEFT JOIN chunks c ON c.chunk_id = e.chunk_id
        LEFT JOIN documents d ON d.doc_id = e.doc_id
        LEFT JOIN document_revisions dr ON dr.revision_id = e.revision_id
        WHERE e.deleted_at IS NULL
          AND (c.chunk_id IS NULL OR d.doc_id IS NULL OR dr.revision_id IS NULL)
        ORDER BY e.created_at, e.embedding_id
        """
    ).fetchall()
    for row in orphan_embeddings:
        findings.append(
            DoctorFinding(
                "error",
                "orphan_embedding",
                f"Embedding {row['embedding_id']} references missing chunk, document, or revision.",
            )
        )

    mismatched_embeddings = connection.execute(
        """
        SELECT e.embedding_id, e.chunk_id, e.doc_id, e.revision_id
        FROM embeddings e
        JOIN chunks c ON c.chunk_id = e.chunk_id
        WHERE e.deleted_at IS NULL
          AND (c.doc_id != e.doc_id OR c.revision_id != e.revision_id)
        ORDER BY e.created_at, e.embedding_id
        """
    ).fetchall()
    for row in mismatched_embeddings:
        findings.append(
            DoctorFinding(
                "error",
                "embedding_chunk_mismatch",
                f"Embedding {row['embedding_id']} does not match chunk {row['chunk_id']} document/revision identity.",
            )
        )

    stale_indexed_embeddings = connection.execute(
        """
        SELECT e.embedding_id, e.chunk_id, e.doc_id
        FROM embeddings e
        JOIN chunks c ON c.chunk_id = e.chunk_id
        JOIN documents d ON d.doc_id = e.doc_id
        WHERE e.deleted_at IS NULL
          AND e.status = 'indexed'
          AND (
            c.content_hash != e.content_hash
            OR c.deleted_at IS NOT NULL
            OR d.deleted_at IS NOT NULL
            OR d.status != 'active'
            OR d.current_revision_id != e.revision_id
            OR c.is_current != 1
          )
        ORDER BY e.created_at, e.embedding_id
        """
    ).fetchall()
    for row in stale_indexed_embeddings:
        findings.append(
            DoctorFinding(
                "error",
                "stale_indexed_embedding",
                f"Embedding {row['embedding_id']} for chunk {row['chunk_id']} is marked indexed but is not current.",
            )
        )
    return findings


def _check_translation_integrity(connection: sqlite3.Connection, vault_root: Path) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    rows = connection.execute(
        """
        SELECT t.translation_id, t.execution_id, t.source_doc_id, t.source_revision_id,
               t.source_chunk_ids_json, t.output_path, t.status,
               e.execution_id AS found_execution_id, e.output_path AS execution_output_path,
               d.doc_id AS found_doc_id, dr.revision_id AS found_revision_id,
               dr.doc_id AS revision_doc_id
        FROM translations t
        LEFT JOIN executions e ON e.execution_id = t.execution_id
        LEFT JOIN documents d ON d.doc_id = t.source_doc_id
        LEFT JOIN document_revisions dr ON dr.revision_id = t.source_revision_id
        WHERE t.deleted_at IS NULL
        ORDER BY t.created_at, t.translation_id
        """
    ).fetchall()
    for row in rows:
        translation_id = str(row["translation_id"])
        if row["found_execution_id"] is None:
            findings.append(
                DoctorFinding(
                    "error",
                    "orphan_translation_execution",
                    f"Translation {translation_id} references missing execution {row['execution_id']}.",
                )
            )
        if row["found_doc_id"] is None:
            findings.append(
                DoctorFinding(
                    "error",
                    "orphan_translation_document",
                    f"Translation {translation_id} references missing document {row['source_doc_id']}.",
                )
            )
        if row["found_revision_id"] is None:
            findings.append(
                DoctorFinding(
                    "error",
                    "orphan_translation_revision",
                    f"Translation {translation_id} references missing revision {row['source_revision_id']}.",
                )
            )
        elif row["revision_doc_id"] != row["source_doc_id"]:
            findings.append(
                DoctorFinding(
                    "error",
                    "translation_revision_document_mismatch",
                    f"Translation {translation_id} revision does not belong to document {row['source_doc_id']}.",
                )
            )

        chunk_ids = _parse_translation_chunk_ids(row["source_chunk_ids_json"])
        if chunk_ids is None:
            findings.append(
                DoctorFinding(
                    "error",
                    "translation_source_chunks_invalid",
                    f"Translation {translation_id} has invalid source_chunk_ids_json.",
                )
            )
        elif not chunk_ids:
            findings.append(
                DoctorFinding(
                    "error",
                    "translation_source_chunks_empty",
                    f"Translation {translation_id} has no source chunk IDs.",
                )
            )
        else:
            placeholders = ", ".join("?" for _ in chunk_ids)
            found_chunks = connection.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM chunks
                WHERE chunk_id IN ({placeholders})
                  AND doc_id = ?
                  AND revision_id = ?
                  AND deleted_at IS NULL
                """,
                (*chunk_ids, row["source_doc_id"], row["source_revision_id"]),
            ).fetchone()["count"]
            if int(found_chunks or 0) != len(chunk_ids):
                findings.append(
                    DoctorFinding(
                        "error",
                        "translation_source_chunk_missing",
                        f"Translation {translation_id} references missing source chunks.",
                    )
                )

        output_path = row["output_path"]
        execution_output_path = row["execution_output_path"]
        if output_path and not str(output_path).startswith("outputs/translations/"):
            findings.append(
                DoctorFinding(
                    "error",
                    "translation_output_outside_translations_dir",
                    f"Translation {translation_id} output_path is outside outputs/translations: {output_path}",
                )
            )
        if output_path and execution_output_path and output_path != execution_output_path:
            findings.append(
                DoctorFinding(
                    "error",
                    "translation_execution_output_mismatch",
                    f"Translation {translation_id} output_path does not match execution output_path.",
                )
            )
        if row["status"] == "succeeded" and (not output_path or not (vault_root / str(output_path)).is_file()):
            findings.append(
                DoctorFinding(
                    "warning",
                    "missing_translation_output",
                    f"Translation {translation_id} output file is missing: {output_path or '<empty>'}",
                )
            )
    return findings


def _parse_translation_chunk_ids(value: object) -> list[str] | None:
    if value is None:
        return None
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    return [str(item) for item in parsed if str(item).strip()]


def _check_candidate_card_integrity(connection: sqlite3.Connection, vault_root: Path) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    findings.extend(_check_candidate_card_records(connection, vault_root))
    findings.extend(_check_candidate_card_sources(connection))
    findings.extend(_check_atomic_note_files(connection, vault_root))
    return findings


def _check_candidate_card_records(connection: sqlite3.Connection, vault_root: Path) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    rows = connection.execute(
        """
        SELECT cc.candidate_card_id, cc.source_doc_id, cc.source_revision_id,
               cc.claims_json, cc.status, cc.accepted_note_path,
               d.doc_id AS found_doc_id, dr.revision_id AS found_revision_id,
               dr.doc_id AS revision_doc_id
        FROM candidate_cards cc
        LEFT JOIN documents d ON d.doc_id = cc.source_doc_id
        LEFT JOIN document_revisions dr ON dr.revision_id = cc.source_revision_id
        WHERE cc.deleted_at IS NULL
        ORDER BY cc.created_at, cc.candidate_card_id
        """
    ).fetchall()
    for row in rows:
        card_id = str(row["candidate_card_id"])
        if row["found_doc_id"] is None:
            findings.append(
                DoctorFinding(
                    "error",
                    "orphan_candidate_card_document",
                    f"Candidate card {card_id} references missing document {row['source_doc_id']}.",
                )
            )
        if row["found_revision_id"] is None:
            findings.append(
                DoctorFinding(
                    "error",
                    "orphan_candidate_card_revision",
                    f"Candidate card {card_id} references missing revision {row['source_revision_id']}.",
                )
            )
        elif row["revision_doc_id"] != row["source_doc_id"]:
            findings.append(
                DoctorFinding(
                    "error",
                    "candidate_card_revision_document_mismatch",
                    f"Candidate card {card_id} revision does not belong to document {row['source_doc_id']}.",
                )
            )

        claims = _parse_candidate_claims(row["claims_json"])
        if claims is None:
            findings.append(
                DoctorFinding(
                    "error",
                    "candidate_card_claims_invalid",
                    f"Candidate card {card_id} has invalid claims_json.",
                )
            )
            claims = []
        elif not claims:
            findings.append(
                DoctorFinding(
                    "error",
                    "candidate_card_claims_empty",
                    f"Candidate card {card_id} has no claims.",
                )
            )
        findings.extend(_check_candidate_claims(connection, row, claims))
        if row["status"] == "accepted":
            findings.extend(_check_accepted_candidate_card(connection, vault_root, row, claims))
    return findings


def _check_candidate_claims(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    claims: list[dict[str, object]],
) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    card_id = str(row["candidate_card_id"])
    for claim in claims:
        claim_id = str(claim.get("claim_id") or "")
        if not claim_id:
            findings.append(
                DoctorFinding(
                    "error",
                    "candidate_card_claim_missing_id",
                    f"Candidate card {card_id} has a claim without claim_id.",
                )
            )
        raw_chunk_ids = claim.get("source_chunk_ids")
        chunk_ids = _string_list(raw_chunk_ids)
        if not chunk_ids:
            findings.append(
                DoctorFinding(
                    "error",
                    "candidate_card_claim_missing_source_chunks",
                    f"Candidate card {card_id} claim {claim_id or '<missing>'} has no source chunks.",
                )
            )
        quotes = claim.get("quotes")
        quote_list = quotes if isinstance(quotes, list) else []
        if not quote_list or any(not isinstance(quote, dict) or not str(quote.get("text", "")).strip() for quote in quote_list):
            findings.append(
                DoctorFinding(
                    "error",
                    "candidate_card_claim_missing_quote",
                    f"Candidate card {card_id} claim {claim_id or '<missing>'} has no citation quote.",
                )
            )
        for chunk_id in chunk_ids:
            found_chunk = connection.execute(
                """
                SELECT chunk_id
                FROM chunks
                WHERE chunk_id = ?
                  AND doc_id = ?
                  AND revision_id = ?
                  AND deleted_at IS NULL
                """,
                (chunk_id, row["source_doc_id"], row["source_revision_id"]),
            ).fetchone()
            if found_chunk is None:
                findings.append(
                    DoctorFinding(
                        "error",
                        "candidate_card_claim_source_chunk_missing",
                        f"Candidate card {card_id} claim {claim_id or '<missing>'} references missing source chunk {chunk_id}.",
                    )
                )
            found_source = connection.execute(
                """
                SELECT candidate_card_source_id
                FROM candidate_card_sources
                WHERE candidate_card_id = ?
                  AND source_chunk_id = ?
                  AND deleted_at IS NULL
                LIMIT 1
                """,
                (card_id, chunk_id),
            ).fetchone()
            if found_source is None:
                findings.append(
                    DoctorFinding(
                        "error",
                        "candidate_card_claim_source_binding_missing",
                        f"Candidate card {card_id} claim {claim_id or '<missing>'} has no candidate_card_sources row for chunk {chunk_id}.",
                    )
                )
    return findings


def _check_candidate_card_sources(connection: sqlite3.Connection) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    rows = connection.execute(
        """
        SELECT ccs.candidate_card_source_id, ccs.candidate_card_id,
               ccs.source_doc_id, ccs.source_revision_id, ccs.source_chunk_id,
               ccs.claim_id, ccs.quote,
               cc.candidate_card_id AS found_card_id,
               cc.source_doc_id AS card_doc_id,
               cc.source_revision_id AS card_revision_id,
               d.doc_id AS found_doc_id,
               dr.revision_id AS found_revision_id,
               c.chunk_id AS found_chunk_id,
               c.doc_id AS chunk_doc_id,
               c.revision_id AS chunk_revision_id
        FROM candidate_card_sources ccs
        LEFT JOIN candidate_cards cc ON cc.candidate_card_id = ccs.candidate_card_id
        LEFT JOIN documents d ON d.doc_id = ccs.source_doc_id
        LEFT JOIN document_revisions dr ON dr.revision_id = ccs.source_revision_id
        LEFT JOIN chunks c ON c.chunk_id = ccs.source_chunk_id
        WHERE ccs.deleted_at IS NULL
        ORDER BY ccs.created_at, ccs.candidate_card_source_id
        """
    ).fetchall()
    for row in rows:
        source_id = str(row["candidate_card_source_id"])
        if row["found_card_id"] is None:
            findings.append(
                DoctorFinding(
                    "error",
                    "orphan_candidate_card_source_card",
                    f"Candidate card source {source_id} references missing card {row['candidate_card_id']}.",
                )
            )
        elif row["card_doc_id"] != row["source_doc_id"] or row["card_revision_id"] != row["source_revision_id"]:
            findings.append(
                DoctorFinding(
                    "error",
                    "candidate_card_source_card_mismatch",
                    f"Candidate card source {source_id} does not match parent card document/revision.",
                )
            )
        if row["found_doc_id"] is None:
            findings.append(
                DoctorFinding(
                    "error",
                    "orphan_candidate_card_source_document",
                    f"Candidate card source {source_id} references missing document {row['source_doc_id']}.",
                )
            )
        if row["found_revision_id"] is None:
            findings.append(
                DoctorFinding(
                    "error",
                    "orphan_candidate_card_source_revision",
                    f"Candidate card source {source_id} references missing revision {row['source_revision_id']}.",
                )
            )
        if row["found_chunk_id"] is None:
            findings.append(
                DoctorFinding(
                    "error",
                    "orphan_candidate_card_source_chunk",
                    f"Candidate card source {source_id} references missing chunk {row['source_chunk_id']}.",
                )
            )
        elif row["chunk_doc_id"] != row["source_doc_id"] or row["chunk_revision_id"] != row["source_revision_id"]:
            findings.append(
                DoctorFinding(
                    "error",
                    "candidate_card_source_chunk_mismatch",
                    f"Candidate card source {source_id} chunk does not match source document/revision.",
                )
            )
        if not str(row["quote"] or "").strip():
            findings.append(
                DoctorFinding(
                    "error",
                    "candidate_card_source_missing_quote",
                    f"Candidate card source {source_id} has no quote.",
                )
            )
    return findings


def _check_accepted_candidate_card(
    connection: sqlite3.Connection,
    vault_root: Path,
    row: sqlite3.Row,
    claims: list[dict[str, object]],
) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    card_id = str(row["candidate_card_id"])
    sources = connection.execute(
        """
        SELECT source_chunk_id
        FROM candidate_card_sources
        WHERE candidate_card_id = ?
          AND deleted_at IS NULL
        ORDER BY source_chunk_id
        """,
        (card_id,),
    ).fetchall()
    source_chunk_ids = [str(source["source_chunk_id"]) for source in sources]
    if not source_chunk_ids:
        findings.append(
            DoctorFinding(
                "error",
                "accepted_card_without_sources",
                f"Accepted candidate card {card_id} has no source bindings.",
            )
        )

    accepted_note_path = row["accepted_note_path"]
    if not accepted_note_path:
        findings.append(
            DoctorFinding(
                "error",
                "accepted_card_missing_note_path",
                f"Accepted candidate card {card_id} has no accepted_note_path.",
            )
        )
        return findings
    if not str(accepted_note_path).startswith("notes/atomic/"):
        findings.append(
            DoctorFinding(
                "error",
                "accepted_note_outside_atomic_dir",
                f"Accepted candidate card {card_id} note path is outside notes/atomic: {accepted_note_path}",
            )
        )
        return findings

    note_path = vault_root / str(accepted_note_path)
    if not note_path.is_file():
        findings.append(
            DoctorFinding(
                "error",
                "missing_accepted_note",
                f"Accepted candidate card {card_id} note file is missing: {accepted_note_path}",
            )
        )
        return findings
    try:
        markdown = note_path.read_text(encoding="utf-8")
    except OSError as exc:
        findings.append(
            DoctorFinding(
                "error",
                "accepted_note_unreadable",
                f"Accepted candidate card {card_id} note file cannot be read: {exc}",
            )
        )
        return findings
    frontmatter, body = _split_frontmatter(markdown)
    if frontmatter is None:
        findings.append(
            DoctorFinding(
                "error",
                "accepted_note_missing_frontmatter",
                f"Accepted candidate card {card_id} note has no frontmatter.",
            )
        )
    else:
        if frontmatter.get("candidate_card_id") != card_id:
            findings.append(
                DoctorFinding(
                    "error",
                    "accepted_note_card_id_mismatch",
                    f"Accepted candidate card {card_id} note frontmatter candidate_card_id does not match.",
                )
            )
        note_source_chunks = _string_list(frontmatter.get("source_chunk_ids"))
        missing_chunks = [chunk_id for chunk_id in source_chunk_ids if chunk_id not in note_source_chunks]
        if missing_chunks:
            findings.append(
                DoctorFinding(
                    "error",
                    "accepted_note_missing_source_chunks",
                    f"Accepted candidate card {card_id} note is missing source chunks: {', '.join(missing_chunks)}",
                )
            )
    for claim in claims:
        claim_id = str(claim.get("claim_id") or "")
        claim_chunk_ids = _string_list(claim.get("source_chunk_ids"))
        if not claim_id:
            continue
        if claim_id not in body or "Citations:" not in body or any(chunk_id not in body for chunk_id in claim_chunk_ids):
            findings.append(
                DoctorFinding(
                    "error",
                    "accepted_note_uncited_claim",
                    f"Accepted candidate card {card_id} note does not cite claim {claim_id}.",
                )
            )
    return findings


def _check_atomic_note_files(connection: sqlite3.Connection, vault_root: Path) -> list[DoctorFinding]:
    notes_root = vault_paths(vault_root).notes_atomic
    if not notes_root.is_dir():
        return []
    referenced_paths = {
        str(row["accepted_note_path"])
        for row in connection.execute(
            """
            SELECT accepted_note_path
            FROM candidate_cards
            WHERE accepted_note_path IS NOT NULL
              AND deleted_at IS NULL
            """
        ).fetchall()
        if row["accepted_note_path"]
    }
    candidate_ids = {
        str(row["candidate_card_id"])
        for row in connection.execute(
            """
            SELECT candidate_card_id
            FROM candidate_cards
            WHERE deleted_at IS NULL
            """
        ).fetchall()
    }
    findings: list[DoctorFinding] = []
    for note_path in sorted(notes_root.rglob("*.md")):
        relative = note_path.relative_to(vault_root).as_posix()
        try:
            markdown = note_path.read_text(encoding="utf-8")
        except OSError:
            continue
        frontmatter, _body = _split_frontmatter(markdown)
        if frontmatter is None or frontmatter.get("type") != "candidate_card":
            continue
        card_id = str(frontmatter.get("candidate_card_id") or "")
        if not card_id or card_id not in candidate_ids or relative not in referenced_paths:
            findings.append(
                DoctorFinding(
                    "error",
                    "orphan_atomic_note",
                    f"Candidate atomic note is not referenced by candidate_cards: {relative}",
                )
            )
    return findings


def _parse_candidate_claims(value: object) -> list[dict[str, object]] | None:
    if value is None:
        return None
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    claims: list[dict[str, object]] = []
    for item in parsed:
        if not isinstance(item, dict):
            return None
        claims.append(item)
    return claims


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def _check_review_queue(connection: sqlite3.Connection) -> list[DoctorFinding]:
    row = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM review_items
        WHERE status = 'pending'
        """
    ).fetchone()
    count = int(row["count"] or 0)
    if count == 0:
        return []
    return [
        DoctorFinding(
            "warning",
            "pending_review_items",
            f"Review queue has {count} pending item(s).",
        )
    ]


def _check_swallow_conversion_integrity(connection: sqlite3.Connection, vault_root: Path) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    rows = connection.execute(
        """
        SELECT cr.converter_run_id, cr.doc_id, cr.revision_id, cr.status,
               cr.promotion_status, cr.candidate_path, cr.external_trace_path,
               cr.primary_worker, cr.artifact_manifest_json, d.current_revision_id
        FROM converter_runs cr
        LEFT JOIN documents d ON d.doc_id = cr.doc_id
        WHERE cr.converter_name = 'swallow'
        ORDER BY cr.created_at, cr.converter_run_id
        """
    ).fetchall()
    for row in rows:
        converter_run_id = str(row["converter_run_id"])
        status = str(row["status"] or "")
        promotion_status = str(row["promotion_status"] or "")
        if promotion_status == "trusted-current":
            if status != "succeeded":
                findings.append(
                    DoctorFinding(
                        "error",
                        "swallow_promotion_status_mismatch",
                        f"Swallow converter run {converter_run_id} is trusted-current but status is {status}.",
                    )
                )
            if not row["revision_id"]:
                findings.append(
                    DoctorFinding(
                        "error",
                        "swallow_trusted_current_missing_revision",
                        f"Swallow converter run {converter_run_id} is trusted-current but has no revision_id.",
                    )
                )
            if not row["external_trace_path"]:
                findings.append(
                    DoctorFinding(
                        "error",
                        "swallow_trace_missing",
                        f"Swallow converter run {converter_run_id} is trusted-current but has no external_trace_path.",
                    )
                )
            if not row["primary_worker"]:
                findings.append(
                    DoctorFinding(
                        "error",
                        "swallow_primary_worker_missing",
                        f"Swallow converter run {converter_run_id} is trusted-current but has no primary_worker.",
                    )
                )
        if status == "pending_review":
            candidate_path = str(row["candidate_path"] or "")
            if not candidate_path or not (vault_root / candidate_path).is_file():
                findings.append(
                    DoctorFinding(
                        "error",
                        "swallow_pending_candidate_missing",
                        f"Pending swallow converter run {converter_run_id} has no readable candidate_path.",
                    )
                )
        if row["revision_id"] and row["doc_id"]:
            revision = connection.execute(
                """
                SELECT doc_id
                FROM document_revisions
                WHERE revision_id = ?
                """,
                (row["revision_id"],),
            ).fetchone()
            if revision is None:
                findings.append(
                    DoctorFinding(
                        "error",
                        "swallow_revision_missing",
                        f"Swallow converter run {converter_run_id} references missing revision {row['revision_id']}.",
                    )
                )
            elif revision["doc_id"] != row["doc_id"]:
                findings.append(
                    DoctorFinding(
                        "error",
                        "swallow_revision_doc_mismatch",
                        f"Swallow converter run {converter_run_id} revision belongs to a different document.",
                    )
                )
        findings.extend(_check_swallow_required_artifacts_archived(converter_run_id, row))
    return findings


def _check_swallow_required_artifacts_archived(converter_run_id: str, row: sqlite3.Row) -> list[DoctorFinding]:
    if not row["artifact_manifest_json"]:
        return []
    try:
        manifest = json.loads(str(row["artifact_manifest_json"]))
    except json.JSONDecodeError:
        return []
    if not isinstance(manifest, dict):
        return []
    required = manifest.get("required", [])
    archived_required = manifest.get("archived_required", [])
    if not isinstance(required, list) or not isinstance(archived_required, list):
        return []
    if (
        row["promotion_status"] == "trusted-current"
        and required
        and len(archived_required) < len(required)
    ):
        return [
            DoctorFinding(
                "error",
                "swallow_required_artifacts_not_archived",
                f"Swallow converter run {converter_run_id} is trusted-current but did not archive every required artifact.",
            )
        ]
    return []


def _check_archive_ingest_integrity(connection: sqlite3.Connection) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    runs = connection.execute(
        """
        SELECT ingest_id
        FROM ingest_runs
        WHERE source_kind = 'archive'
        ORDER BY created_at, ingest_id
        """
    ).fetchall()
    for run in runs:
        ingest_id = str(run["ingest_id"])
        parents = connection.execute(
            """
            SELECT ingest_item_id, logical_source_id
            FROM ingest_items
            WHERE ingest_id = ?
              AND parent_ingest_item_id IS NULL
              AND doc_id IS NULL
            ORDER BY created_at, ingest_item_id
            """,
            (ingest_id,),
        ).fetchall()
        if len(parents) != 1:
            findings.append(
                DoctorFinding(
                    "error",
                    "archive_ingest_parent_invalid",
                    f"Archive ingest {ingest_id} should have exactly one parent package item; found {len(parents)}.",
                )
            )
            parent_id = None
        else:
            parent_id = str(parents[0]["ingest_item_id"])
            logical_source_id = str(parents[0]["logical_source_id"] or "")
            if not logical_source_id.startswith("archive:"):
                findings.append(
                    DoctorFinding(
                        "error",
                        "archive_ingest_parent_logical_id_invalid",
                        f"Archive ingest parent {parent_id} has invalid logical_source_id: {logical_source_id}",
                    )
                )

        children = connection.execute(
            """
            SELECT ii.ingest_item_id, ii.doc_id, ii.parent_ingest_item_id,
                   ii.logical_source_id, ii.status, d.source_type, d.current_revision_id
            FROM ingest_items ii
            LEFT JOIN documents d ON d.doc_id = ii.doc_id
            WHERE ii.ingest_id = ?
              AND ii.parent_ingest_item_id IS NOT NULL
            ORDER BY ii.created_at, ii.ingest_item_id
            """,
            (ingest_id,),
        ).fetchall()
        if not children:
            findings.append(
                DoctorFinding(
                    "error",
                    "archive_ingest_children_missing",
                    f"Archive ingest {ingest_id} has no logical child items.",
                )
            )
        for child in children:
            child_id = str(child["ingest_item_id"])
            if parent_id is not None and child["parent_ingest_item_id"] != parent_id:
                findings.append(
                    DoctorFinding(
                        "error",
                        "archive_ingest_child_missing_parent",
                        f"Archive child item {child_id} does not point at the archive parent item.",
                    )
                )
            if not child["doc_id"]:
                findings.append(
                    DoctorFinding(
                        "error",
                        "archive_ingest_child_missing_document",
                        f"Archive child item {child_id} has no document.",
                    )
                )
                continue
            if child["source_type"] != "chatgpt_conversation":
                findings.append(
                    DoctorFinding(
                        "error",
                        "archive_ingest_child_source_type_mismatch",
                        f"Archive child item {child_id} document source_type is {child['source_type']}.",
                    )
                )
            if child["status"] == "succeeded" and not child["current_revision_id"]:
                findings.append(
                    DoctorFinding(
                        "error",
                        "archive_ingest_child_missing_revision",
                        f"Archive child item {child_id} succeeded without a current revision.",
                    )
                )
            if child["status"] == "succeeded":
                findings.extend(_check_archive_child_current_locators(connection, child))
    return findings


def _check_archive_child_current_locators(
    connection: sqlite3.Connection,
    child: sqlite3.Row,
) -> list[DoctorFinding]:
    rows = connection.execute(
        """
        SELECT chunk_id, source_locator_json
        FROM chunks
        WHERE doc_id = ?
          AND revision_id = ?
          AND is_current = 1
          AND deleted_at IS NULL
        ORDER BY sequence, chunk_id
        """,
        (child["doc_id"], child["current_revision_id"]),
    ).fetchall()
    findings: list[DoctorFinding] = []
    expected_logical_source_id = str(child["logical_source_id"] or "")
    for row in rows:
        locators = _parse_locator_list(row["source_locator_json"])
        if not locators:
            findings.append(
                DoctorFinding(
                    "error",
                    "archive_member_locator_missing",
                    f"Archive child chunk {row['chunk_id']} has no archive_member locator.",
                )
            )
            continue
        matching = [
            locator
            for locator in locators
            if locator.get("kind") == "archive_member"
            and str(locator.get("logical_source_id") or "") == expected_logical_source_id
        ]
        if not matching:
            findings.append(
                DoctorFinding(
                    "error",
                    "archive_member_locator_mismatch",
                    f"Archive child chunk {row['chunk_id']} has no locator for logical source {expected_logical_source_id}.",
                )
            )
    return findings


def _check_swallow_artifact_integrity(connection: sqlite3.Connection, vault_root: Path) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    rows = connection.execute(
        """
        SELECT converter_run_id, artifact_manifest_json, promotion_status, status
        FROM converter_runs
        WHERE converter_name = 'swallow'
          AND artifact_manifest_json IS NOT NULL
        ORDER BY created_at, converter_run_id
        """
    ).fetchall()
    for row in rows:
        converter_run_id = str(row["converter_run_id"])
        try:
            manifest = json.loads(str(row["artifact_manifest_json"]))
        except json.JSONDecodeError:
            findings.append(
                DoctorFinding(
                    "error",
                    "swallow_artifact_manifest_invalid",
                    f"Swallow converter run {converter_run_id} has invalid artifact_manifest_json.",
                )
            )
            continue
        if not isinstance(manifest, dict):
            findings.append(
                DoctorFinding(
                    "error",
                    "swallow_artifact_manifest_invalid",
                    f"Swallow converter run {converter_run_id} artifact manifest is not an object.",
                )
            )
            continue
        archived_required = manifest.get("archived_required", [])
        if not isinstance(archived_required, list):
            findings.append(
                DoctorFinding(
                    "error",
                    "swallow_artifact_manifest_invalid",
                    f"Swallow converter run {converter_run_id} archived_required is not a list.",
                )
            )
            continue
        for artifact in archived_required:
            artifact_path = str(artifact)
            if artifact_path and not is_vault_artifact_path(artifact_path):
                findings.append(
                    DoctorFinding(
                        "error",
                        "swallow_required_artifact_not_durable",
                        f"Swallow converter run {converter_run_id} required artifact is not durable: {artifact_path}",
                    )
                )
            elif artifact_path and not (vault_root / artifact_path).is_file():
                findings.append(
                    DoctorFinding(
                        "error",
                        "missing_swallow_required_artifact",
                        f"Swallow converter run {converter_run_id} required artifact is missing: {artifact_path}",
                    )
                )
    return findings


def _check_transition_output_runtime(paths, config) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    if not config.features.transition_output:
        return findings
    runtime_dir = paths.transition_runtime
    bridge = runtime_dir / "transition-bridge.mjs"
    transition_config = runtime_dir / "transition.config.json"
    node_modules = runtime_dir / "node_modules"
    if not bridge.is_file() or not transition_config.is_file() or not node_modules.is_dir():
        findings.append(
            DoctorFinding(
                "error",
                "transition_runtime_missing",
                "transition_output is enabled but the per-vault runtime is incomplete. "
                "Run `indb output runtime install`.",
            )
        )
    return findings


def _check_output_run_integrity(connection: sqlite3.Connection, vault_root: Path) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    try:
        connection.execute("SELECT 1 FROM output_runs LIMIT 1")
    except sqlite3.OperationalError:
        return findings

    never_promoted_current = connection.execute(
        """
        SELECT dr.revision_id, dr.doc_id
        FROM document_revisions dr
        JOIN documents d ON d.doc_id = dr.doc_id
        WHERE dr.promotion_status = 'never_promoted'
          AND d.current_revision_id = dr.revision_id
          AND dr.deleted_at IS NULL
        """
    ).fetchall()
    for row in never_promoted_current:
        findings.append(
            DoctorFinding(
                "error",
                "never_promoted_is_current",
                f"Document {row['doc_id']} current revision {row['revision_id']} is never_promoted.",
            )
        )

    rows = connection.execute(
        """
        SELECT output_run_id, status, evidence_manifest_path
        FROM output_runs
        WHERE deleted_at IS NULL
          AND mode = 'export'
          AND status IN ('succeeded', 'partial')
        ORDER BY created_at, output_run_id
        """
    ).fetchall()
    for row in rows:
        run_id = str(row["output_run_id"])
        if not row["evidence_manifest_path"]:
            findings.append(
                DoctorFinding(
                    "warning",
                    "output_run_missing_evidence",
                    f"Output run {run_id} has no evidence_manifest_path.",
                )
            )
        md_artifact = connection.execute(
            """
            SELECT path, sha256, status
            FROM output_artifacts
            WHERE output_run_id = ?
              AND format = 'md'
              AND deleted_at IS NULL
            """,
            (run_id,),
        ).fetchone()
        if md_artifact is None or md_artifact["status"] != "succeeded":
            findings.append(
                DoctorFinding(
                    "error",
                    "output_run_missing_normalized_md",
                    f"Output run {run_id} is missing a succeeded normalized.md artifact.",
                )
            )
            continue
        rel_path = str(md_artifact["path"] or "")
        if not rel_path.startswith("outputs/exports/"):
            findings.append(
                DoctorFinding(
                    "error",
                    "output_artifact_outside_exports",
                    f"Output run {run_id} normalized.md is outside outputs/exports: {rel_path}",
                )
            )
        if not is_relative_vault_path(rel_path):
            findings.append(
                DoctorFinding(
                    "error",
                    "output_artifact_unsafe_path",
                    f"Output run {run_id} artifact path is not vault-relative safe: {rel_path}",
                )
            )
        file_path = vault_root / rel_path
        if not file_path.is_file():
            findings.append(
                DoctorFinding(
                    "error",
                    "missing_output_artifact_file",
                    f"Output run {run_id} artifact file is missing: {rel_path}",
                )
            )
        elif md_artifact["sha256"]:
            actual = hash_markdown(file_path.read_text(encoding="utf-8"))
            if actual != md_artifact["sha256"]:
                findings.append(
                    DoctorFinding(
                        "error",
                        "output_artifact_hash_mismatch",
                        f"Output run {run_id} normalized.md hash does not match the database.",
                    )
                )
        if row["evidence_manifest_path"]:
            manifest_path = vault_root / str(row["evidence_manifest_path"])
            if not manifest_path.is_file():
                findings.append(
                    DoctorFinding(
                        "warning",
                        "missing_output_evidence_manifest",
                        f"Output run {run_id} evidence manifest is missing on disk.",
                    )
                )
    return findings


def _taxonomy_schema_ready(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'document_profiles'
        """
    ).fetchone()
    return row is not None


def _check_taxonomy_integrity(connection: sqlite3.Connection) -> list[DoctorFinding]:
    if not _taxonomy_schema_ready(connection):
        return []

    findings: list[DoctorFinding] = []
    allowed_types = ", ".join(f"'{value}'" for value in sorted(TAG_TYPES))

    invalid_tag_rows = connection.execute(
        f"""
        SELECT tag_id, name, type
        FROM tags
        WHERE deleted_at IS NULL
          AND status = 'active'
          AND (type IS NULL OR type NOT IN ({allowed_types}))
        LIMIT 20
        """
    ).fetchall()
    for row in invalid_tag_rows:
        findings.append(
            DoctorFinding(
                "error",
                "tag_invalid_type",
                f"Tag {row['tag_id']} ({row['name']}) has invalid or missing type: {row['type']!r}",
            )
        )

    alias_collisions = connection.execute(
        """
        SELECT normalized_alias, COUNT(*) AS count
        FROM tag_aliases
        WHERE deleted_at IS NULL
        GROUP BY normalized_alias
        HAVING COUNT(*) > 1
        LIMIT 20
        """
    ).fetchall()
    for row in alias_collisions:
        findings.append(
            DoctorFinding(
                "error",
                "tag_alias_collision",
                f"Alias collision for normalized alias {row['normalized_alias']} ({row['count']} rows).",
            )
        )

    dangling_document_tags = connection.execute(
        """
        SELECT dt.doc_id, dt.tag_id
        FROM document_tags dt
        LEFT JOIN tags t ON t.tag_id = dt.tag_id
        WHERE dt.deleted_at IS NULL
          AND dt.status = 'active'
          AND t.tag_id IS NULL
        LIMIT 20
        """
    ).fetchall()
    for row in dangling_document_tags:
        findings.append(
            DoctorFinding(
                "error",
                "document_tag_missing_tag",
                f"Document tag assignment {row['doc_id']} -> {row['tag_id']} references a missing tag.",
            )
        )

    dangling_doc_refs = connection.execute(
        """
        SELECT dt.doc_id, dt.tag_id
        FROM document_tags dt
        LEFT JOIN documents d ON d.doc_id = dt.doc_id
        WHERE dt.deleted_at IS NULL
          AND dt.status = 'active'
          AND d.doc_id IS NULL
        LIMIT 20
        """
    ).fetchall()
    for row in dangling_doc_refs:
        findings.append(
            DoctorFinding(
                "error",
                "document_tag_missing_doc",
                f"Document tag assignment references missing document {row['doc_id']}.",
            )
        )

    inactive_assignments = connection.execute(
        """
        SELECT dt.doc_id, dt.tag_id, t.status AS tag_status
        FROM document_tags dt
        JOIN tags t ON t.tag_id = dt.tag_id
        WHERE dt.deleted_at IS NULL
          AND dt.status = 'active'
          AND t.status != 'active'
        LIMIT 20
        """
    ).fetchall()
    for row in inactive_assignments:
        findings.append(
            DoctorFinding(
                "error",
                "document_tag_inactive_tag",
                f"Document {row['doc_id']} is assigned inactive tag {row['tag_id']} (status={row['tag_status']}).",
            )
        )

    stale_profiles = connection.execute(
        """
        SELECT dp.profile_id, dp.doc_id, dp.revision_id
        FROM document_profiles dp
        JOIN documents d ON d.doc_id = dp.doc_id
        WHERE dp.status = 'active'
          AND d.deleted_at IS NULL
          AND d.current_revision_id IS NOT NULL
          AND dp.revision_id != d.current_revision_id
        LIMIT 20
        """
    ).fetchall()
    for row in stale_profiles:
        findings.append(
            DoctorFinding(
                "error",
                "profile_for_old_revision_marked_active",
                f"Profile {row['profile_id']} is active for old revision {row['revision_id']} on doc {row['doc_id']}.",
            )
        )

    archived_profiles = connection.execute(
        """
        SELECT dp.profile_id, dp.doc_id
        FROM document_profiles dp
        JOIN documents d ON d.doc_id = dp.doc_id
        WHERE dp.status = 'active'
          AND d.status = 'archived'
          AND d.deleted_at IS NULL
        LIMIT 20
        """
    ).fetchall()
    for row in archived_profiles:
        findings.append(
            DoctorFinding(
                "error",
                "profile_for_archived_doc_marked_active",
                f"Profile {row['profile_id']} is active for archived document {row['doc_id']}.",
            )
        )

    source_shell_profiles = connection.execute(
        """
        SELECT dp.profile_id, dp.doc_id
        FROM document_profiles dp
        JOIN documents d ON d.doc_id = dp.doc_id
        WHERE dp.status = 'active'
          AND d.deleted_at IS NULL
          AND d.current_revision_id IS NULL
        LIMIT 20
        """
    ).fetchall()
    for row in source_shell_profiles:
        findings.append(
            DoctorFinding(
                "error",
                "profile_for_source_shell",
                f"Profile {row['profile_id']} exists for source shell document {row['doc_id']}.",
            )
        )

    dangling_profiles = connection.execute(
        """
        SELECT dp.profile_id, dp.doc_id, dp.revision_id
        FROM document_profiles dp
        LEFT JOIN documents d ON d.doc_id = dp.doc_id
        LEFT JOIN document_revisions dr ON dr.revision_id = dp.revision_id
        WHERE d.doc_id IS NULL OR dr.revision_id IS NULL
        LIMIT 20
        """
    ).fetchall()
    for row in dangling_profiles:
        findings.append(
            DoctorFinding(
                "error",
                "profile_dangling_reference",
                f"Profile {row['profile_id']} references missing doc/revision ({row['doc_id']}, {row['revision_id']}).",
            )
        )

    dangling_features = connection.execute(
        """
        SELECT fa.feature_id, fa.chunk_id
        FROM feature_atoms fa
        LEFT JOIN chunks c ON c.chunk_id = fa.chunk_id
        WHERE c.chunk_id IS NULL
        LIMIT 20
        """
    ).fetchall()
    for row in dangling_features:
        findings.append(
            DoctorFinding(
                "error",
                "feature_atom_missing_chunk",
                f"Feature atom {row['feature_id']} references missing chunk {row['chunk_id']}.",
            )
        )

    wrong_revision_features = connection.execute(
        """
        SELECT fa.feature_id, fa.doc_id, fa.revision_id, fa.chunk_id
        FROM feature_atoms fa
        JOIN chunks c ON c.chunk_id = fa.chunk_id
        WHERE fa.status = 'active'
          AND (c.doc_id != fa.doc_id OR c.revision_id != fa.revision_id)
        LIMIT 20
        """
    ).fetchall()
    for row in wrong_revision_features:
        findings.append(
            DoctorFinding(
                "error",
                "feature_atom_wrong_revision",
                f"Feature atom {row['feature_id']} chunk binding does not match doc/revision.",
            )
        )

    missing_evidence_candidates = connection.execute(
        """
        SELECT candidate_id, name
        FROM tag_candidates
        WHERE evidence_doc_ids_json IS NULL
           OR evidence_doc_ids_json = '[]'
           OR evidence_chunk_ids_json IS NULL
           OR evidence_chunk_ids_json = '[]'
        LIMIT 20
        """
    ).fetchall()
    for row in missing_evidence_candidates:
        findings.append(
            DoctorFinding(
                "error",
                "tag_candidate_missing_evidence",
                f"Tag candidate {row['candidate_id']} ({row['name']}) is missing evidence.",
            )
        )

    promoted_without_tag = connection.execute(
        """
        SELECT tc.candidate_id, tc.promoted_tag_id
        FROM tag_candidates tc
        WHERE tc.status = 'accepted'
          AND (
            tc.promoted_tag_id IS NULL
            OR NOT EXISTS (
              SELECT 1 FROM tags t WHERE t.tag_id = tc.promoted_tag_id
            )
          )
        LIMIT 20
        """
    ).fetchall()
    for row in promoted_without_tag:
        findings.append(
            DoctorFinding(
                "error",
                "tag_candidate_promoted_without_tag",
                f"Tag candidate {row['candidate_id']} is accepted without a valid promoted tag.",
            )
        )

    dangling_taxonomy_suggestions = connection.execute(
        """
        SELECT ts.suggestion_id, ts.doc_id, ts.revision_id
        FROM taxonomy_suggestions ts
        LEFT JOIN documents d ON d.doc_id = ts.doc_id
        LEFT JOIN document_revisions dr ON dr.revision_id = ts.revision_id
        WHERE ts.type IN ('category_assign', 'tag_assign', 'tag_candidate')
          AND (
            (ts.doc_id IS NOT NULL AND d.doc_id IS NULL)
            OR (ts.revision_id IS NOT NULL AND dr.revision_id IS NULL)
          )
        LIMIT 20
        """
    ).fetchall()
    for row in dangling_taxonomy_suggestions:
        findings.append(
            DoctorFinding(
                "error",
                "taxonomy_suggestion_dangling_reference",
                f"Taxonomy suggestion {row['suggestion_id']} references missing doc/revision.",
            )
        )

    invalid_category_suggestions = connection.execute(
        """
        SELECT ts.suggestion_id, json_extract(ts.payload_json, '$.category_id') AS category_id
        FROM taxonomy_suggestions ts
        WHERE ts.type = 'category_assign'
          AND json_extract(ts.payload_json, '$.category_id') IS NOT NULL
          AND NOT EXISTS (
            SELECT 1
            FROM categories c
            WHERE c.category_id = json_extract(ts.payload_json, '$.category_id')
          )
        LIMIT 20
        """
    ).fetchall()
    for row in invalid_category_suggestions:
        findings.append(
            DoctorFinding(
                "error",
                "taxonomy_suggestion_invalid_category",
                f"Taxonomy suggestion {row['suggestion_id']} targets missing category {row['category_id']}.",
            )
        )

    invalid_tag_suggestions = connection.execute(
        """
        SELECT ts.suggestion_id, json_extract(ts.payload_json, '$.tag_id') AS tag_id
        FROM taxonomy_suggestions ts
        WHERE ts.type = 'tag_assign'
          AND json_extract(ts.payload_json, '$.tag_id') IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM tags t WHERE t.tag_id = json_extract(ts.payload_json, '$.tag_id')
          )
        LIMIT 20
        """
    ).fetchall()
    for row in invalid_tag_suggestions:
        findings.append(
            DoctorFinding(
                "error",
                "taxonomy_suggestion_invalid_tag",
                f"Taxonomy suggestion {row['suggestion_id']} targets missing tag {row['tag_id']}.",
            )
        )

    return findings


def _tag_governance_schema_ready(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'tagger_runs'
        """
    ).fetchone()
    return row is not None


def _check_tag_governance_integrity(connection: sqlite3.Connection) -> list[DoctorFinding]:
    if not _tag_governance_schema_ready(connection):
        return []

    findings: list[DoctorFinding] = []

    alias_missing_tag = connection.execute(
        """
        SELECT ta.alias_id, ta.alias
        FROM tag_aliases ta
        LEFT JOIN tags t ON t.tag_id = ta.tag_id
        WHERE ta.deleted_at IS NULL
          AND t.tag_id IS NULL
        LIMIT 20
        """
    ).fetchall()
    for row in alias_missing_tag:
        findings.append(
            DoctorFinding(
                "error",
                "tag_alias_points_missing_tag",
                f"Alias {row['alias_id']} ({row['alias']}) points to a missing tag.",
            )
        )

    merged_target_missing = connection.execute(
        """
        SELECT tag_id, name, merged_into_tag_id
        FROM tags
        WHERE deleted_at IS NULL
          AND merged_into_tag_id IS NOT NULL
          AND merged_into_tag_id NOT IN (
            SELECT tag_id FROM tags WHERE deleted_at IS NULL
          )
        LIMIT 20
        """
    ).fetchall()
    for row in merged_target_missing:
        findings.append(
            DoctorFinding(
                "error",
                "tag_merged_target_missing",
                f"Tag {row['tag_id']} ({row['name']}) merges into missing tag {row['merged_into_tag_id']}.",
            )
        )

    auto_without_evidence = connection.execute(
        """
        SELECT dt.doc_id, dt.tag_id
        FROM document_tags dt
        WHERE dt.deleted_at IS NULL
          AND dt.status = 'active'
          AND dt.source = 'auto'
          AND (
            dt.evidence_chunk_ids_json IS NULL
            OR TRIM(dt.evidence_chunk_ids_json) IN ('', '[]')
          )
        LIMIT 20
        """
    ).fetchall()
    for row in auto_without_evidence:
        findings.append(
            DoctorFinding(
                "error",
                "auto_attached_tag_without_evidence",
                f"Auto-attached tag {row['tag_id']} on {row['doc_id']} lacks evidence chunks.",
            )
        )

    auto_inactive_tag = connection.execute(
        """
        SELECT dt.doc_id, dt.tag_id, t.status AS tag_status
        FROM document_tags dt
        JOIN tags t ON t.tag_id = dt.tag_id
        WHERE dt.deleted_at IS NULL
          AND dt.status = 'active'
          AND dt.source = 'auto'
          AND t.status IN ('deprecated', 'archived', 'merged')
        LIMIT 20
        """
    ).fetchall()
    for row in auto_inactive_tag:
        findings.append(
            DoctorFinding(
                "error",
                "auto_attached_deprecated_or_archived",
                f"Auto-attached tag {row['tag_id']} on {row['doc_id']} is {row['tag_status']}.",
            )
        )

    pending_without_resolution = connection.execute(
        """
        SELECT candidate_id, name
        FROM tag_candidates
        WHERE status = 'pending'
          AND candidate_type IS NOT NULL
          AND (resolution_status IS NULL OR TRIM(resolution_status) = '')
        LIMIT 20
        """
    ).fetchall()
    for row in pending_without_resolution:
        findings.append(
            DoctorFinding(
                "error",
                "tag_candidate_without_resolution",
                f"Governance candidate {row['candidate_id']} ({row['name']}) lacks resolution_status.",
            )
        )

    candidate_missing_run = connection.execute(
        """
        SELECT candidate_id
        FROM tag_candidates
        WHERE tagger_run_id IS NOT NULL
          AND tagger_run_id NOT IN (SELECT tagger_run_id FROM tagger_runs)
        LIMIT 20
        """
    ).fetchall()
    for row in candidate_missing_run:
        findings.append(
            DoctorFinding(
                "error",
                "tag_candidate_missing_run",
                f"Tag candidate {row['candidate_id']} references a missing tagger run.",
            )
        )

    formal_count = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM tags
        WHERE deleted_at IS NULL
          AND status = 'active'
        """
    ).fetchone()["count"]
    if int(formal_count or 0) > 500:
        findings.append(
            DoctorFinding(
                "warning",
                "formal_tag_soft_limit_exceeded",
                f"Active formal tag count {formal_count} exceeds the soft limit of 500.",
            )
        )

    return findings


def _retrieval_schema_ready(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'retrieval_runs'
        """
    ).fetchone()
    return row is not None


def _check_retrieval_integrity(connection: sqlite3.Connection) -> list[DoctorFinding]:
    if not _retrieval_schema_ready(connection):
        return []

    findings: list[DoctorFinding] = []
    runs = connection.execute(
        """
        SELECT retrieval_run_id, filters_json, planner_json, warnings_json,
               result_count, status
        FROM retrieval_runs
        ORDER BY created_at DESC
        LIMIT 200
        """
    ).fetchall()
    for row in runs:
        run_id = str(row["retrieval_run_id"])
        for field, code in (
            ("filters_json", "retrieval_run_invalid_json"),
            ("planner_json", "retrieval_run_invalid_json"),
            ("warnings_json", "retrieval_run_invalid_json"),
        ):
            raw = row[field]
            try:
                json.loads(str(raw or "{}"))
            except json.JSONDecodeError:
                findings.append(
                    DoctorFinding(
                        "error",
                        code,
                        f"Retrieval run {run_id} has invalid {field}.",
                    )
                )
        item_count = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM retrieval_items
            WHERE retrieval_run_id = ?
            """,
            (run_id,),
        ).fetchone()["count"]
        expected = int(row["result_count"] or 0)
        if int(item_count) != expected:
            findings.append(
                DoctorFinding(
                    "error",
                    "retrieval_run_count_mismatch",
                    f"Retrieval run {run_id} result_count={expected} but items={item_count}.",
                )
            )

    items = connection.execute(
        """
        SELECT ri.retrieval_item_id, ri.retrieval_run_id, ri.rank, ri.doc_id,
               ri.revision_id, ri.chunk_id, ri.quote,
               rr.retrieval_run_id AS run_exists
        FROM retrieval_items ri
        LEFT JOIN retrieval_runs rr ON rr.retrieval_run_id = ri.retrieval_run_id
        ORDER BY ri.created_at DESC
        LIMIT 500
        """
    ).fetchall()
    seen_ranks: dict[str, set[int]] = {}
    for row in items:
        item_id = str(row["retrieval_item_id"])
        run_id = str(row["retrieval_run_id"])
        if row["run_exists"] is None:
            findings.append(
                DoctorFinding(
                    "error",
                    "retrieval_item_missing_run",
                    f"Retrieval item {item_id} references missing run {run_id}.",
                )
            )
        rank = int(row["rank"])
        seen_ranks.setdefault(run_id, set())
        if rank in seen_ranks[run_id]:
            findings.append(
                DoctorFinding(
                    "error",
                    "retrieval_item_invalid_rank",
                    f"Retrieval run {run_id} has duplicate rank {rank}.",
                )
            )
        seen_ranks[run_id].add(rank)

        doc = connection.execute(
            "SELECT doc_id FROM documents WHERE doc_id = ?",
            (row["doc_id"],),
        ).fetchone()
        if doc is None:
            findings.append(
                DoctorFinding(
                    "error",
                    "retrieval_item_missing_doc",
                    f"Retrieval item {item_id} references missing document {row['doc_id']}.",
                )
            )
        revision = connection.execute(
            "SELECT revision_id FROM document_revisions WHERE revision_id = ?",
            (row["revision_id"],),
        ).fetchone()
        if revision is None:
            findings.append(
                DoctorFinding(
                    "error",
                    "retrieval_item_missing_revision",
                    f"Retrieval item {item_id} references missing revision {row['revision_id']}.",
                )
            )
        chunk = connection.execute(
            "SELECT chunk_id, revision_id, text FROM chunks WHERE chunk_id = ?",
            (row["chunk_id"],),
        ).fetchone()
        if chunk is None:
            findings.append(
                DoctorFinding(
                    "error",
                    "retrieval_item_missing_chunk",
                    f"Retrieval item {item_id} references missing chunk {row['chunk_id']}.",
                )
            )
            continue
        if str(chunk["revision_id"]) != str(row["revision_id"]):
            findings.append(
                DoctorFinding(
                    "error",
                    "retrieval_item_wrong_revision",
                    f"Retrieval item {item_id} revision does not match chunk revision.",
                )
            )
        quote = str(row["quote"] or "")
        if not quote.strip():
            findings.append(
                DoctorFinding(
                    "error",
                    "retrieval_item_quote_missing",
                    f"Retrieval item {item_id} has an empty quote.",
                )
            )
        elif quote.strip() not in str(chunk["text"]):
            findings.append(
                DoctorFinding(
                    "error",
                    "retrieval_item_quote_not_in_chunk",
                    f"Retrieval item {item_id} quote is not an exact substring of chunk text.",
                )
            )
    return findings


def _eval_schema_ready(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'retrieval_eval_cases'
        """
    ).fetchone()
    return row is not None


def _check_retrieval_evaluation_integrity(connection: sqlite3.Connection) -> list[DoctorFinding]:
    if not _eval_schema_ready(connection):
        return []

    findings: list[DoctorFinding] = []
    for row in connection.execute(
        """
        SELECT eval_case_id, options_json, expectations_json, status
        FROM retrieval_eval_cases
        ORDER BY created_at DESC
        LIMIT 200
        """
    ).fetchall():
        case_id = str(row["eval_case_id"])
        for field, code in (
            ("options_json", "retrieval_eval_case_invalid_json"),
            ("expectations_json", "retrieval_eval_case_invalid_json"),
        ):
            try:
                json.loads(str(row[field] or "{}"))
            except json.JSONDecodeError:
                findings.append(
                    DoctorFinding(
                        "error",
                        code,
                        f"Eval case {case_id} has invalid {field}.",
                    )
                )
        if str(row["status"]) not in {"active", "archived"}:
            findings.append(
                DoctorFinding(
                    "error",
                    "retrieval_eval_case_invalid_json",
                    f"Eval case {case_id} has invalid status.",
                )
            )

    for row in connection.execute(
        """
        SELECT er.eval_result_id, er.eval_run_id, er.eval_case_id, er.retrieval_run_id,
               run.eval_run_id AS run_exists, case_row.eval_case_id AS case_exists,
               rr.retrieval_run_id AS retrieval_exists
        FROM retrieval_eval_results er
        LEFT JOIN retrieval_eval_runs run ON run.eval_run_id = er.eval_run_id
        LEFT JOIN retrieval_eval_cases case_row ON case_row.eval_case_id = er.eval_case_id
        LEFT JOIN retrieval_runs rr ON rr.retrieval_run_id = er.retrieval_run_id
        ORDER BY er.created_at DESC
        LIMIT 500
        """
    ).fetchall():
        result_id = str(row["eval_result_id"])
        if row["run_exists"] is None:
            findings.append(
                DoctorFinding(
                    "error",
                    "retrieval_eval_result_missing_run",
                    f"Eval result {result_id} references missing eval run.",
                )
            )
        if row["case_exists"] is None:
            findings.append(
                DoctorFinding(
                    "error",
                    "retrieval_eval_result_missing_case",
                    f"Eval result {result_id} references missing eval case.",
                )
            )
        if row["retrieval_run_id"] and row["retrieval_exists"] is None:
            findings.append(
                DoctorFinding(
                    "error",
                    "retrieval_eval_result_missing_retrieval_run",
                    f"Eval result {result_id} references missing retrieval run.",
                )
            )

    for row in connection.execute(
        """
        SELECT readiness_report_id, retrieval_run_id, verdict, blockers_json,
               warnings_json, metrics_json
        FROM answer_readiness_reports
        ORDER BY created_at DESC
        LIMIT 200
        """
    ).fetchall():
        report_id = str(row["readiness_report_id"])
        run_id = str(row["retrieval_run_id"])
        run = connection.execute(
            "SELECT retrieval_run_id FROM retrieval_runs WHERE retrieval_run_id = ?",
            (run_id,),
        ).fetchone()
        if run is None:
            findings.append(
                DoctorFinding(
                    "error",
                    "readiness_report_missing_retrieval_run",
                    f"Readiness report {report_id} references missing retrieval run {run_id}.",
                )
            )
        if str(row["verdict"]) not in {"ready", "needs_more_evidence", "not_ready"}:
            findings.append(
                DoctorFinding(
                    "error",
                    "readiness_report_invalid_verdict",
                    f"Readiness report {report_id} has invalid verdict.",
                )
            )
        for field, code in (
            ("blockers_json", "readiness_report_invalid_json"),
            ("warnings_json", "readiness_report_invalid_json"),
            ("metrics_json", "readiness_report_invalid_json"),
        ):
            try:
                json.loads(str(row[field] or "{}"))
            except json.JSONDecodeError:
                findings.append(
                    DoctorFinding(
                        "error",
                        code,
                        f"Readiness report {report_id} has invalid {field}.",
                    )
                )
        blockers = _doctor_json_list(row["blockers_json"])
        for item in connection.execute(
            """
            SELECT retrieval_item_id, quote, chunk_id
            FROM retrieval_items
            WHERE retrieval_run_id = ?
            """,
            (run_id,),
        ).fetchall():
            quote = str(item["quote"] or "")
            chunk = connection.execute(
                "SELECT text FROM chunks WHERE chunk_id = ?",
                (item["chunk_id"],),
            ).fetchone()
            if chunk is None:
                continue
            if quote.strip() and quote not in str(chunk["text"]):
                if "quote_not_in_chunk" not in blockers:
                    findings.append(
                        DoctorFinding(
                            "error",
                            "readiness_report_quote_blocker_missing",
                            (
                                f"Readiness report {report_id} should block quote mismatch "
                                f"for item {item['retrieval_item_id']}."
                            ),
                        )
                    )
    return findings


def _doctor_json_list(raw: object) -> list[str]:
    try:
        payload = json.loads(str(raw or "[]"))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload]


def _split_frontmatter(markdown: str) -> tuple[dict[str, object] | None, str]:
    if not markdown.startswith("---\n"):
        return None, markdown
    end = markdown.find("\n---\n", 4)
    if end < 0:
        return None, markdown
    raw_frontmatter = markdown[4:end]
    body = markdown[end + len("\n---\n") :]
    if body.startswith("\n"):
        body = body[1:]
    return _parse_frontmatter(raw_frontmatter), body


def _parse_frontmatter(raw_frontmatter: str) -> dict[str, object]:
    parsed: dict[str, object] = {}
    for line in raw_frontmatter.splitlines():
        if not line or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        value = raw_value.strip()
        try:
            parsed[key.strip()] = json.loads(value)
        except json.JSONDecodeError:
            parsed[key.strip()] = value
    return parsed
