"""Run Windows path and Unicode filename checks for MVP release close-out."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import stat
import subprocess

from indbase_core.db import connect
from indbase_core.doctor import run_doctor
from indbase_core.embeddings import rebuild_vector_index
from indbase_core.indexer import rebuild_fts_index
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.paths import WINDOWS_RESERVED_BASENAMES, slugify
from indbase_core.search import search_chunks
from indbase_core.vault import init_vault


ROOT = Path.cwd()
IND_B = ROOT / ".venv" / "Scripts" / "indb.exe"
WINDOWS_ILLEGAL_CHARS = set('<>:"\\|?*')


def main() -> None:
    root = ROOT / ".tmp" / f"mvp-windows-path-unicode-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    source_root = root / "真实 资料 folder" / "日本語 資料"
    vault = root / "vault with spaces 中文"
    source_root.mkdir(parents=True)
    init_vault(vault)

    duplicate_seed = root / "seed duplicate.md"
    duplicate_seed.write_text("# Duplicate Seed\nduplicaterenameunique\n", encoding="utf-8")
    seed_result = run_m3_ingest_pipeline(vault, duplicate_seed)

    fixture_summary = _write_fixtures(source_root)
    ingest = run_m3_ingest_pipeline(vault, source_root, recursive=True)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        metrics = _database_metrics(connection, vault)
        search_metrics = _search_metrics(connection)
        rebuild_fts = rebuild_fts_index(connection, vault)
        rebuild_vectors = rebuild_vector_index(connection)
        search_after_rebuild = search_chunks(connection, "大语言模型").result_count
        first_doc_id = connection.execute(
            """
            SELECT doc_id
            FROM documents
            WHERE current_revision_id IS NOT NULL
              AND deleted_at IS NULL
            ORDER BY created_at, doc_id
            LIMIT 1
            """
        ).fetchone()["doc_id"]

    doc_open_metrics = _doc_open_metrics(vault, str(first_doc_id))
    doctor = run_doctor(vault)
    critical_doctor_findings = sum(1 for finding in doctor.findings if finding.severity in {"error", "critical"})

    summary = {
        **fixture_summary,
        **metrics,
        **search_metrics,
        **doc_open_metrics,
        "seed_ingest_succeeded": int(seed_result.status == "succeeded"),
        "recursive_ingest_completed": int(ingest.status in {"succeeded", "completed_with_issues"}),
        "recursive_ingest_unsupported_items": ingest.unsupported_items,
        "recursive_ingest_duplicate_items": ingest.duplicate_items,
        "fts_rebuild_failed_documents": rebuild_fts.failed_documents,
        "vector_rebuild_failed_chunks": rebuild_vectors.failed_chunks,
        "search_after_rebuild_results": search_after_rebuild,
        "doctor_exit_code": doctor.exit_code,
        "critical_doctor_findings": critical_doctor_findings,
        "slug_reserved_collisions": _slug_reserved_collisions(),
        "slug_illegal_char_outputs": _slug_illegal_char_outputs(),
        "slug_overlong_outputs": _slug_overlong_outputs(),
    }

    hard_zero = {
        "critical_doctor_findings": summary["critical_doctor_findings"],
        "unsafe_canonical_paths": summary["unsafe_canonical_paths"],
        "unsafe_original_paths": summary["unsafe_original_paths"],
        "missing_canonical_files": summary["missing_canonical_files"],
        "missing_original_files": summary["missing_original_files"],
        "unsupported_documents_created": summary["unsupported_documents_created"],
        "zero_chunk_current_revisions": summary["zero_chunk_current_revisions"],
        "source_shells_searchable": summary["source_shells_searchable"],
        "fts_rebuild_failed_documents": summary["fts_rebuild_failed_documents"],
        "vector_rebuild_failed_chunks": summary["vector_rebuild_failed_chunks"],
        "slug_reserved_collisions": summary["slug_reserved_collisions"],
        "slug_illegal_char_outputs": summary["slug_illegal_char_outputs"],
        "slug_overlong_outputs": summary["slug_overlong_outputs"],
        "doc_open_failures": summary["doc_open_failures"],
    }
    required_positive = {
        "seed_ingest_succeeded": summary["seed_ingest_succeeded"],
        "recursive_ingest_completed": summary["recursive_ingest_completed"],
        "unicode_documents": summary["unicode_documents"],
        "readonly_documents": summary["readonly_documents"],
        "duplicate_items": summary["duplicate_items"],
        "unsupported_items": summary["unsupported_items"],
        "cjk_search_results": summary["cjk_search_results"],
        "japanese_search_results": summary["japanese_search_results"],
        "space_path_search_results": summary["space_path_search_results"],
        "emoji_path_search_results": summary["emoji_path_search_results"],
        "readonly_search_results": summary["readonly_search_results"],
        "search_after_rebuild_results": summary["search_after_rebuild_results"],
        "doc_open_markdown_ok": summary["doc_open_markdown_ok"],
        "doc_open_original_ok": summary["doc_open_original_ok"],
    }
    if any(value != 0 for value in hard_zero.values()):
        raise RuntimeError(f"Windows path/Unicode hard zero metrics failed: {hard_zero}; summary={summary}")
    if any(value <= 0 for value in required_positive.values()):
        raise RuntimeError(f"Windows path/Unicode positive metrics failed: {required_positive}; summary={summary}")

    print(json.dumps(summary, sort_keys=True))
    print("MVP_WINDOWS_PATH_UNICODE_GATE=passed")
    print(f"DOGFOOD_ROOT={root}")


def _write_fixtures(source_root: Path) -> dict[str, int]:
    files: list[Path] = []

    def write_text(relative: str, content: str) -> None:
        path = source_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        files.append(path)

    def write_bytes(relative: str, content: bytes) -> None:
        path = source_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        files.append(path)

    write_text("中文 路径/中文 文件.md", "# 中文 文件\n大语言模型 知识数据库 幻觉控制 unicodegatezh\n")
    write_text("日本語 パス/日本語 ファイル.txt", "大規模言語モデル 知識管理 エージェント unicodegateja\n")
    write_text("space dir/file with spaces.txt", "spacepathunique text from a path with spaces\n")
    write_text("emoji 🧠/emoji-🧠.md", "# Emoji\nemojipathunique text\n")
    write_text("nested 中文/level 日本語/deep file.md", "# Deep\nnestedunicodeunique text\n")
    write_text(f"long names/{'very-long-title-' + 'a' * 120}.md", "# Long\nlongfilenameunique text\n")
    write_text("case lower/case-test.txt", "caselowerunique text\n")
    write_text("case upper/CASE-TEST.txt", "caseupperunique text\n")
    write_text("duplicates/renamed duplicate.md", "# Duplicate Seed\nduplicaterenameunique\n")
    write_bytes("unsupported/unsupported 文件.png", b"\x89PNG\r\n\x1a\n")
    write_bytes("unsupported/unsupported archive.zip", b"PK\x03\x04")

    readonly = source_root / "readonly 文件.txt"
    readonly.write_text("readonlypathunique text\n", encoding="utf-8")
    readonly.chmod(stat.S_IREAD)
    files.append(readonly)

    reserved_created = 0
    reserved_os_blocked = 0
    for relative, content in {
        "reserved/CON.txt": "reservedconunique text\n",
        "reserved/AUX.md": "# Reserved\nreservedauxunique text\n",
    }.items():
        try:
            write_text(relative, content)
            reserved_created += 1
        except OSError:
            reserved_os_blocked += 1

    return {
        "fixture_files_created": len(files),
        "reserved_fixture_files_created": reserved_created,
        "reserved_fixture_files_os_blocked": reserved_os_blocked,
    }


def _database_metrics(connection, vault: Path) -> dict[str, int]:
    rows = connection.execute(
        """
        SELECT doc_id, title, source_uri, canonical_path, original_path, current_revision_id
        FROM documents
        WHERE deleted_at IS NULL
        ORDER BY created_at, doc_id
        """
    ).fetchall()
    canonical_paths = [str(row["canonical_path"]) for row in rows if row["canonical_path"]]
    original_paths = [str(row["original_path"]) for row in rows if row["original_path"]]
    return {
        "documents": len(rows),
        "unicode_documents": sum(1 for row in rows if _has_non_ascii(str(row["source_uri"]))),
        "readonly_documents": sum(1 for row in rows if "readonly" in str(row["source_uri"]).casefold()),
        "duplicate_items": _count(connection, "SELECT COUNT(*) AS count FROM ingest_items WHERE status = 'duplicate'"),
        "unsupported_items": _count(connection, "SELECT COUNT(*) AS count FROM ingest_items WHERE status = 'unsupported'"),
        "unsupported_documents_created": _count(
            connection,
            """
            SELECT COUNT(*) AS count
            FROM documents
            WHERE source_type NOT IN ('md', 'txt', 'html', 'csv', 'json', 'docx', 'xlsx', 'pptx', 'pdf')
            """,
        ),
        "zero_chunk_current_revisions": _count(
            connection,
            """
            SELECT COUNT(*) AS count
            FROM documents d
            WHERE d.current_revision_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1
                FROM chunks c
                WHERE c.doc_id = d.doc_id
                  AND c.revision_id = d.current_revision_id
                  AND c.is_current = 1
                  AND c.deleted_at IS NULL
              )
            """,
        ),
        "source_shells_searchable": _count(
            connection,
            """
            SELECT COUNT(*) AS count
            FROM documents d
            JOIN chunks_fts f ON f.doc_id = d.doc_id
            WHERE d.current_revision_id IS NULL
              AND d.deleted_at IS NULL
            """,
        ),
        "unsafe_canonical_paths": sum(1 for path in canonical_paths if not _is_safe_vault_relative_path(path)),
        "unsafe_original_paths": sum(1 for path in original_paths if not _is_safe_vault_relative_path(path)),
        "missing_canonical_files": sum(1 for path in canonical_paths if not (vault / path).is_file()),
        "missing_original_files": sum(1 for path in original_paths if not (vault / path).is_file()),
        "overlong_canonical_filenames": sum(1 for path in canonical_paths if len(Path(path).name) > 160),
    }


def _search_metrics(connection) -> dict[str, int]:
    return {
        "cjk_search_results": search_chunks(connection, "大语言模型").result_count,
        "japanese_search_results": search_chunks(connection, "知識管理").result_count,
        "space_path_search_results": search_chunks(connection, "spacepathunique").result_count,
        "emoji_path_search_results": search_chunks(connection, "emojipathunique").result_count,
        "readonly_search_results": search_chunks(connection, "readonlypathunique").result_count,
        "long_filename_search_results": search_chunks(connection, "longfilenameunique").result_count,
        "case_lower_search_results": search_chunks(connection, "caselowerunique").result_count,
        "case_upper_search_results": search_chunks(connection, "caseupperunique").result_count,
    }


def _doc_open_metrics(vault: Path, doc_id: str) -> dict[str, int]:
    markdown = _run_indb("doc", "open", doc_id, "--vault", str(vault), "--print-path")
    original = _run_indb("doc", "open", doc_id, "--vault", str(vault), "--original", "--print-path")
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        row = connection.execute(
            "SELECT canonical_path, original_path FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
    markdown_path = vault / str(row["canonical_path"])
    original_path = vault / str(row["original_path"])
    markdown_ok = int(markdown.returncode == 0 and markdown_path.is_file())
    original_ok = int(original.returncode == 0 and original_path.is_file())
    return {
        "doc_open_markdown_ok": markdown_ok,
        "doc_open_original_ok": original_ok,
        "doc_open_failures": int(markdown_ok != 1) + int(original_ok != 1),
    }


def _run_indb(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [str(IND_B), *args],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def _is_safe_vault_relative_path(path: str) -> bool:
    parts = [part for part in path.replace("\\", "/").split("/") if part]
    for part in parts:
        if any(char in WINDOWS_ILLEGAL_CHARS for char in part):
            return False
        if part.endswith(" ") or part.endswith("."):
            return False
        if Path(part).stem.casefold() in WINDOWS_RESERVED_BASENAMES:
            return False
        if len(part) > 160:
            return False
    return True


def _slug_reserved_collisions() -> int:
    values = ("CON", "PRN", "AUX", "NUL", "COM1", "LPT1")
    return sum(1 for value in values if slugify(value) in WINDOWS_RESERVED_BASENAMES)


def _slug_illegal_char_outputs() -> int:
    values = ('name<bad>.txt', 'name:bad', 'name"bad', "name/bad", "name\\bad", "name|bad", "name?bad", "name*bad")
    return sum(1 for value in values if any(char in WINDOWS_ILLEGAL_CHARS for char in slugify(value)))


def _slug_overlong_outputs() -> int:
    return int(len(slugify("a" * 240)) > 80)


def _has_non_ascii(value: str) -> bool:
    return any(ord(char) > 127 for char in value)


def _count(connection, sql: str) -> int:
    return int(connection.execute(sql).fetchone()["count"] or 0)


if __name__ == "__main__":
    main()
