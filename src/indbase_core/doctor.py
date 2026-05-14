"""Vault health checks."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
import json
from pathlib import Path
import shutil
import sqlite3

from indbase_core.config import ConfigError, load_config
from indbase_core.conversion import hash_markdown
from indbase_core.db import connect, load_migrations
from indbase_core.paths import vault_paths
from indbase_core.search_text import build_fts_text


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
        }


def run_doctor(vault_path: Path | str) -> DoctorReport:
    paths = vault_paths(vault_path)
    findings: list[DoctorFinding] = []

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

    findings.append(_markitdown_availability_finding())
    findings.append(_ocr_availability_finding())

    if not any(finding.severity in {"warning", "error", "critical"} for finding in findings):
        findings.append(DoctorFinding("info", "ok", "Vault health checks passed."))

    return DoctorReport(vault_path=paths.root, findings=tuple(findings))


def _markitdown_availability_finding() -> DoctorFinding:
    if find_spec("markitdown") is None:
        return DoctorFinding(
            "info",
            "markitdown_unavailable",
            "MarkItDown is not installed; HTML fallback remains available and Tier 2 conversion will create visible review items.",
        )
    return DoctorFinding(
        "info",
        "markitdown_available",
        "MarkItDown is installed; HTML and Tier 2 best-effort conversion can use it.",
    )


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
            findings.extend(_check_ocr_integrity(connection))
            findings.extend(_check_embedding_integrity(connection))
            findings.extend(_check_translation_integrity(connection, vault_root))
            findings.extend(_check_candidate_card_integrity(connection, vault_root))
            findings.extend(_check_review_queue(connection))
        finally:
            connection.close()
    except sqlite3.Error as exc:
        findings.append(DoctorFinding("error", "database_error", str(exc)))
    return findings


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
          AND cr.converter_name = 'markitdown'
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
          AND cr.converter_name = 'markitdown'
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
